# =============================================================================
# 向量诚实化 + PG 数组存储 + 探测器单测（tests/unit/test_vector_real_embedding_pg_array.py）
#
# 覆盖验收：arch_review_gap_b2_observability_perf tasks T5.1~T5.4
# （DC-B2-05 ①②③④ / GK-11 / INV-L08 / INV-B2-05 / HC-05）：
#   1. 开关默认态：vector_real_embedding_enabled=False、
#      local_vector_database_storage="sqlite"（关闭态与现状等价）；
#   2. 写入闸：True + 仅 hash_fallback → 可辨识错误拒绝（VECTOR_KB_REAL_
#      EMBEDDING_REQUIRED）；False → 零干预；真实来源不受影响；
#   3. 代际识别：显式 embedding_kind > source.provenance 推导 > 存量缺省
#      hash（三级回退）；hash 代际出口保持 degraded_annotation；
#   4. pg_array 路径：同接口契约接入 BruteForceIndex，距离对拍 vector_math
#      （应用层权威单源）；探测三态（命中 SQL 增强 / 未命中回退 / 失败回退）。
# =============================================================================

import asyncio
import json
import math
import sqlite3

import pytest

from orchestration_platform.configuration.settings import Settings, get_settings
from orchestration_platform.domain.errors import VECTOR_KB_REAL_EMBEDDING_REQUIRED
from orchestration_platform.integrations.local_vector_database.config import (
    LocalVectorDatabaseConfig,
)
from orchestration_platform.integrations.local_vector_database.embedding import (
    HashFallbackProvider,
    ExternalApiEmbeddingProvider,
    PROVENANCE_HASH_FALLBACK,
)
from orchestration_platform.integrations.local_vector_database.models import (
    EMBEDDING_KIND_HASH,
    EMBEDDING_KIND_REAL,
    VectorRecord,
    resolve_embedding_kind,
)
from orchestration_platform.integrations.local_vector_database.pg_array_store import (
    PgArrayVectorStore,
    _vector_literal,
)
from orchestration_platform.integrations.local_vector_database.pg_vector_probe import (
    PgVectorExtensionProbe,
)
from orchestration_platform.integrations.local_vector_database.retrieval import (
    BruteForceIndex,
)
from orchestration_platform.integrations.local_vector_database.service import (
    LocalVectorDatabaseService,
)
from orchestration_platform.integrations.local_vector_database.vector_math import (
    metric_score,
)
from orchestration_platform.integrations.vector_kb_management.ingestion import (
    _embedding_kind_for,
    _reject_hash_fallback_if_real_required,
)
from orchestration_platform.domain.errors import DomainError


# =============================================================================
# 假 asyncpg 连接（pg_array 存储语义 mock；零真实库依赖）
# =============================================================================


def _cosine_distance(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


class FakeAsyncConn:
    """asyncpg 连接语义 mock：内存态 collection_meta + vector_kb_records。"""

    def __init__(self, state):
        self._state = state

    async def fetchrow(self, query, *args):
        q = " ".join(query.split())
        if "FROM pg_extension" in q:
            return {"1": 1} if self._state.get("pgvector") else None
        if "COUNT(*)" in q:
            cid = args[0]
            records = self._state["records"].get(cid, {})
            declared = (self._state["meta"].get(cid) or {}).get(
                "declared_dimension", 0
            )
            bad = sum(
                1
                for r in records.values()
                if len(r["embedding"]) != declared
            )
            return {"n": len(records), "bad": bad}
        if "FROM collection_meta" in q:
            meta = self._state["meta"].get(args[0])
            if meta is None:
                return None
            if "record_count" in q:
                return {
                    "declared_dimension": meta["declared_dimension"],
                    "record_count": meta.get("record_count", 0),
                }
            return dict(meta)
        if "SELECT 1 FROM vector_kb_records" in q:
            key = (args[0], args[1])
            rec = self._state["records"].get(args[0], {}).get(key[1])
            return {"1": 1} if rec is not None else None
        return None

    async def fetch(self, query, *args):
        q = " ".join(query.split())
        cid = args[0]
        records = self._state["records"].get(cid, {})
        if "<=>" in q:
            query_vector = json.loads(args[1].replace("'", "").replace('"', ""))
            limit = int(args[2])
            rows = []
            for rid, rec in records.items():
                dist = _cosine_distance(query_vector, rec["embedding"])
                rows.append(
                    {
                        "record_id": rid,
                        "payload": rec["payload"],
                        "embedding": rec["embedding"],
                        "embedding_kind": rec.get("embedding_kind"),
                        "distance": dist,
                    }
                )
            rows.sort(key=lambda r: r["distance"])
            return rows[:limit]
        rows = []
        for rid in sorted(records):
            rec = records[rid]
            rows.append(
                {
                    "record_id": rid,
                    "payload": rec["payload"],
                    "embedding": rec["embedding"],
                    "embedding_kind": rec.get("embedding_kind"),
                }
            )
        return rows

    async def execute(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO collection_meta"):
            cid = args[0]
            if "ON CONFLICT" in q and cid in self._state["meta"]:
                return "INSERT 0 0"
            self._state["meta"][cid] = {
                "collection_id": cid,
                "declared_dimension": int(args[1]),
                "metric": str(args[2]),
                "format_version": str(args[3]),
                "created_at": str(args[4]),
                "updated_at": str(args[4]),
                "record_count": 0,
            }
            return "INSERT 0 1"
        if q.startswith("INSERT INTO vector_kb_records"):
            cid, rid, payload_json, embedding, kind = args
            records = self._state["records"].setdefault(cid, {})
            existed = rid in records
            records[rid] = {
                "payload": payload_json,
                "embedding": list(embedding),
                "embedding_kind": kind,
            }
            meta = self._state["meta"].get(cid)
            if meta is not None and not existed:
                meta["record_count"] = meta.get("record_count", 0) + 1
            return "INSERT 0 1"
        if q.startswith("DELETE FROM vector_kb_records"):
            cid, rid = args
            records = self._state["records"].get(cid, {})
            if rid in records:
                del records[rid]
                return "DELETE 1"
            return "DELETE 0"
        if q.startswith("UPDATE collection_meta"):
            cid = args[0]
            meta = self._state["meta"].get(cid)
            if meta is not None:
                if "GREATEST" in q:
                    # GREATEST(COALESCE(record_count,1)-1, 0)：删除后计数递减
                    meta["record_count"] = max(
                        0, meta.get("record_count", 1) - 1
                    )
                elif "record_count = " in q:
                    meta["record_count"] = int(args[1])
            return "UPDATE 1"
        return "EXECUTE 0"

    def transaction(self):
        return _FakeTransaction()

    async def close(self):
        return None


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _pg_store(state, probe=None):
    async def connector():
        return FakeAsyncConn(state)

    return PgArrayVectorStore(
        default_metric="cosine", max_records_per_collection=100, probe=probe,
        connector=connector,
    )


# =============================================================================
# T5.1 开关默认态（关闭态与现状等价，GK-11）
# =============================================================================


class TestSwitchDefaults:
    def test_settings_default_off(self):
        assert Settings(_env_file=None).vector_real_embedding_enabled is False

    def test_settings_storage_default_sqlite(self):
        assert Settings(_env_file=None).local_vector_database_storage == "sqlite"

    def test_singleton_defaults_unchanged(self):
        s = get_settings()
        assert getattr(s, "vector_real_embedding_enabled", False) is False
        assert getattr(s, "local_vector_database_storage", "sqlite") == "sqlite"


# =============================================================================
# T5.2 写入闸（DC-B2-05 ③，INV-L08 零放宽）
# =============================================================================


class TestRealEmbeddingGate:
    def _hash_provider(self):
        return HashFallbackProvider()

    def _real_provider(self):
        model = _api_model_config()
        return ExternalApiEmbeddingProvider(model, dimension=4)

    def test_gate_off_hash_allowed(self, monkeypatch):
        """关闭态（默认）：hash_fallback 写入零干预（与现状逐字节等价）。"""
        monkeypatch.setattr(
            get_settings(), "vector_real_embedding_enabled", False
        )
        _reject_hash_fallback_if_real_required(self._hash_provider())

    def test_gate_on_hash_rejected_identifiably(self, monkeypatch):
        """开启态：仅 hash_fallback 可用 → 可辨识错误拒绝（不伪造语义）。"""
        monkeypatch.setattr(
            get_settings(), "vector_real_embedding_enabled", True
        )
        with pytest.raises(DomainError) as exc_info:
            _reject_hash_fallback_if_real_required(self._hash_provider())
        assert exc_info.value.code == VECTOR_KB_REAL_EMBEDDING_REQUIRED
        assert "hash_fallback" in exc_info.value.message

    def test_gate_on_real_source_unaffected(self, monkeypatch):
        """开启态：真实来源（api/local_runtime/caller_supplied）不受影响。"""
        monkeypatch.setattr(
            get_settings(), "vector_real_embedding_enabled", True
        )
        _reject_hash_fallback_if_real_required(self._real_provider())

    def test_embedding_kind_tagging(self):
        """记录级代际随写入落盘（DC-B2-05 ④）：hash 来源 → hash，其余 → real。"""
        assert _embedding_kind_for(self._hash_provider()) == EMBEDDING_KIND_HASH

        class _FakeReal:
            provenance = "api"

        assert _embedding_kind_for(_FakeReal()) == EMBEDDING_KIND_REAL


def _api_model_config():
    from orchestration_platform.domain.models.node_configurations import (
        ModelLoadingConfig,
    )

    return ModelLoadingConfig(
        mode="api",
        base_url="https://embed.example.invalid",
        model_identifier="embed-test",
        timeout_seconds=5,
        allow_degraded_fallback=False,
    )


# =============================================================================
# T5.2 代际识别（三级回退 + 出口守护，DC-B2-05 ④ / INV-B2-05）
# =============================================================================


class TestEmbeddingKindGenerations:
    def test_explicit_field_authoritative(self):
        assert (
            resolve_embedding_kind("real", {"provenance": "hash_fallback"})
            == EMBEDDING_KIND_REAL
        )
        assert (
            resolve_embedding_kind("hash", {"provenance": "api"})
            == EMBEDDING_KIND_HASH
        )

    def test_provenance_derivation(self):
        assert (
            resolve_embedding_kind(None, {"provenance": "hash_fallback"})
            == EMBEDDING_KIND_HASH
        )
        assert resolve_embedding_kind(None, {"provenance": "api"}) == EMBEDDING_KIND_REAL
        assert (
            resolve_embedding_kind(None, {"provenance": "caller_supplied"})
            == EMBEDDING_KIND_REAL
        )

    def test_legacy_default_hash(self):
        """存量缺省 = hash 代际（无任何信息的记录不冒充真实语义）。"""
        assert resolve_embedding_kind(None, None) == EMBEDDING_KIND_HASH
        assert resolve_embedding_kind(None, {"type": "hr"}) == EMBEDDING_KIND_HASH

    def test_sqlite_roundtrip_kind_persisted(self, tmp_path):
        """显式代际随 sqlite 落盘 + 读取识别。"""
        from orchestration_platform.integrations.local_vector_database.store import (
            SqliteVectorStore,
        )

        store = SqliteVectorStore(str(tmp_path))
        store.upsert(
            "kb",
            VectorRecord(
                record_id="r-real",
                vector=[1.0, 0.0],
                text="真实代际",
                embedding_kind=EMBEDDING_KIND_REAL,
            ),
        )
        store.upsert(
            "kb",
            VectorRecord(
                record_id="r-hash",
                vector=[0.0, 1.0],
                text="伪向量代际",
                embedding_kind=EMBEDDING_KIND_HASH,
            ),
        )
        kinds = {
            r.record_id: r.embedding_kind
            for r in store.load_records("kb")
        }
        assert kinds["r-real"] == EMBEDDING_KIND_REAL
        assert kinds["r-hash"] == EMBEDDING_KIND_HASH

    def test_sqlite_legacy_records_resolve_hash(self, tmp_path):
        """sqlite 存量零破坏（HC-05）：旧 schema（无 embedding_kind 列）文件
        可读，记录按存量缺省识别为 hash 代际。"""
        db_path = tmp_path / "legacy.sqlite3"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE collection_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE vector_records ("
            "record_id TEXT PRIMARY KEY, vector BLOB NOT NULL, "
            "vector_dim INTEGER NOT NULL, text TEXT NOT NULL, source TEXT)"
        )
        import struct

        conn.execute(
            "INSERT INTO collection_meta VALUES ('declared_dimension', '2')"
        )
        conn.execute("INSERT INTO collection_meta VALUES ('metric', 'cosine')")
        conn.execute("INSERT INTO collection_meta VALUES ('format_version', '1')")
        conn.execute("INSERT INTO collection_meta VALUES ('created_at', 'x')")
        conn.execute("INSERT INTO collection_meta VALUES ('updated_at', 'x')")
        blob = struct.pack("<2f", 1.0, 0.0)
        conn.execute(
            "INSERT INTO vector_records VALUES ('r-legacy', ?, 2, '存量记录', NULL)",
            (blob,),
        )
        conn.commit()
        conn.close()

        from orchestration_platform.integrations.local_vector_database.store import (
            SqliteVectorStore,
        )

        store = SqliteVectorStore(str(tmp_path))
        records = list(store.load_records("legacy"))
        assert len(records) == 1
        assert records[0].embedding_kind == EMBEDDING_KIND_HASH

    def test_service_exit_guard_hash_generation_degraded(self, tmp_path):
        """hash 代际命中 → 出口强制降级标注（不冒充真实语义，INV-B2-05）。"""
        service = LocalVectorDatabaseService(
            config=_svc_config(tmp_path)
        )
        service.upsert_records(
            "kb",
            [
                VectorRecord(
                    record_id="r1",
                    vector=[1.0, 0.0],
                    text="伪向量",
                    source={"provenance": PROVENANCE_HASH_FALLBACK},
                ),
                VectorRecord(
                    record_id="r2",
                    vector=[0.9, 0.1],
                    text="真实向量",
                    source={"provenance": "api"},
                ),
            ],
        )
        outcome = service.retrieve("kb", [1.0, 0.0], 5)
        assert outcome.local_degraded is True
        assert outcome.local_degraded_reason
        assert "hash" in outcome.local_degraded_reason

    def test_service_real_generation_no_degradation(self, tmp_path):
        """全真实代际命中 → 无降级标注（对真实向量诚实计分）。"""
        service = LocalVectorDatabaseService(config=_svc_config(tmp_path))
        service.upsert_records(
            "kb",
            [
                VectorRecord(
                    record_id="r2",
                    vector=[1.0, 0.0],
                    text="真实向量",
                    source={"provenance": "api"},
                ),
            ],
        )
        outcome = service.retrieve("kb", [1.0, 0.0], 5)
        assert outcome.local_degraded is False
        assert outcome.chunks[0].score == pytest.approx(1.0, abs=1e-9)


def _svc_config(tmp_path):
    return LocalVectorDatabaseConfig(
        enabled=True, storage_root=str(tmp_path / "vkb"), storage="sqlite"
    )


# =============================================================================
# T5.3 pg_array 存储路径（同接口契约 + 距离对拍 vector_math 权威单源）
# =============================================================================


class TestPgArrayStore:
    def _state(self):
        return {"meta": {}, "records": {}, "pgvector": False}

    def test_ensure_upsert_load_roundtrip(self):
        state = self._state()
        store = _pg_store(state)
        meta = store.ensure_collection("kb", 3, "cosine")
        assert meta.declared_dimension == 3
        store.upsert(
            "kb",
            VectorRecord(
                record_id="r1",
                vector=[1.0, 0.0, 0.0],
                text="文档一",
                source={"type": "hr"},
                embedding_kind=EMBEDDING_KIND_REAL,
            ),
        )
        store.upsert(
            "kb",
            VectorRecord(
                record_id="r2", vector=[0.0, 1.0, 0.0], text="文档二"
            ),
        )
        records = {r.record_id: r for r in store.load_records("kb")}
        assert records["r1"].embedding_kind == EMBEDDING_KIND_REAL
        # 存量缺省：无代际记录读取时识别为 hash 代际（不冒充真实语义）
        assert records["r2"].embedding_kind == EMBEDDING_KIND_HASH
        assert records["r1"].text == "文档一"

    def test_dimension_mismatch_rejected(self):
        state = self._state()
        store = _pg_store(state)
        store.ensure_collection("kb", 2, "cosine")
        from orchestration_platform.integrations.vector_knowledge_base.models import (
            VectorRetrievalError,
        )
        from orchestration_platform.domain.errors import (
            VECTOR_DB_DIMENSION_MISMATCH,
        )

        with pytest.raises(VectorRetrievalError) as exc_info:
            store.upsert(
                "kb", VectorRecord(record_id="r1", vector=[1.0], text="t")
            )
        assert exc_info.value.error_code == VECTOR_DB_DIMENSION_MISMATCH

    def test_delete_idempotent(self):
        state = self._state()
        store = _pg_store(state)
        store.ensure_collection("kb", 1, "cosine")
        store.upsert("kb", VectorRecord(record_id="r1", vector=[1.0], text="t"))
        assert store.delete("kb", "r1") is True
        assert store.delete("kb", "r1") is False

    def test_bruteforce_scores_match_vector_math(self):
        """距离对拍 vector_math（应用层权威单源，sqlite/pg_array 同源零漂移）。"""
        state = self._state()
        store = _pg_store(state)
        store.ensure_collection("kb", 3, "cosine")
        vectors = {
            "r1": [1.0, 0.0, 0.0],
            "r2": [0.0, 1.0, 0.0],
            "r3": [0.7, 0.7, 0.0],
        }
        for rid, vec in vectors.items():
            store.upsert(
                "kb",
                VectorRecord(record_id=rid, vector=vec, text=rid),
            )
        index = BruteForceIndex(store, max_top_k=100)
        query = [0.8, 0.2, 0.0]
        hits = index.search("kb", query, 3, None, "cosine")
        assert [h.record_id for h in hits] == ["r1", "r3", "r2"]
        for hit in hits:
            assert hit.score == pytest.approx(
                metric_score("cosine", query, vectors[hit.record_id]), abs=1e-12
            )

    def test_diagnose_reports_degradation_when_probe_miss(self):
        """探测未命中 → 诊断报告可辨识降级登记（禁静默）。"""
        state = self._state()
        store = _pg_store(
            state, probe=PgVectorExtensionProbe(executor=lambda: False)
        )
        store.ensure_collection("kb", 1, "cosine")
        report = store.diagnose("kb")
        assert any("pgvector" in issue for issue in report.issues)

    def test_accelerated_path_scores_equal_authoritative(self):
        """增强路径：SQL `<=>` 预排序 + 应用层权威计分（分数不取 SQL 距离）。
        命中态 → 与全量扫描 top-K 一致。"""
        state = self._state()
        state["pgvector"] = True
        store = _pg_store(
            state, probe=PgVectorExtensionProbe(executor=lambda: True)
        )
        store.ensure_collection("kb", 3, "cosine")
        vectors = {
            "r1": [1.0, 0.0, 0.0],
            "r2": [0.0, 1.0, 0.0],
            "r3": [0.7, 0.7, 0.0],
        }
        for rid, vec in vectors.items():
            store.upsert("kb", VectorRecord(record_id=rid, vector=vec, text=rid))
        query = [0.8, 0.2, 0.0]
        hits = store.search_accelerated("kb", query, 3, 100, "cosine")
        assert hits is not None
        assert [h.record_id for h in hits] == ["r1", "r3", "r2"]
        for hit in hits:
            assert hit.score == pytest.approx(
                metric_score("cosine", query, vectors[hit.record_id]), abs=1e-9
            )

    def test_accelerated_fallback_when_probe_miss(self):
        """未命中态 → None（调用方回退应用层距离，可辨识降级登记）。"""
        state = self._state()
        store = _pg_store(
            state, probe=PgVectorExtensionProbe(executor=lambda: False)
        )
        store.ensure_collection("kb", 1, "cosine")
        store.upsert("kb", VectorRecord(record_id="r1", vector=[1.0], text="t"))
        assert (
            store.search_accelerated("kb", [1.0], 5, 100, "cosine") is None
        )

    def test_accelerated_fallback_on_sql_failure(self):
        """SQL 失败态 → None 回退（fail-safe，不伪造增强路径成功）。"""
        state = self._state()
        state["pgvector"] = True

        class _BrokenConn(FakeAsyncConn):
            async def fetch(self, query, *args):
                if "<=>" in query:
                    raise RuntimeError("operator does not exist")
                return await super().fetch(query, *args)

        async def connector():
            return _BrokenConn(state)

        store = PgArrayVectorStore(
            probe=PgVectorExtensionProbe(executor=lambda: True),
            connector=connector,
        )
        store.ensure_collection("kb", 1, "cosine")
        store.upsert("kb", VectorRecord(record_id="r1", vector=[1.0], text="t"))
        assert (
            store.search_accelerated("kb", [1.0], 5, 100, "cosine") is None
        )


# =============================================================================
# T5.4 探测器三态（tasks T5.4 验收；真实 pgvector 环境移交 N 类）
# =============================================================================


class TestPgVectorExtensionProbe:
    def test_hit_state(self):
        """命中态：扩展在位 → 增强路径可用（结果缓存）。"""
        probe = PgVectorExtensionProbe(executor=lambda: True)
        assert probe.probe() is True
        assert probe.is_acceleration_available() is True

    def test_miss_state(self):
        """未命中态：扩展不在位 → 应用层距离回退。"""
        probe = PgVectorExtensionProbe(executor=lambda: False)
        assert probe.probe() is False
        assert probe.is_acceleration_available() is False

    def test_error_state_fail_safe(self):
        """失败态：探测异常 → 按不可用处理（fail-safe，禁伪造可用性）。"""
        def _boom():
            raise RuntimeError("pg down")

        probe = PgVectorExtensionProbe(executor=_boom)
        assert probe.probe() is False

    def test_disabled_switch_short_circuits(self):
        """总闸关闭：不触发探测，增强路径不可用。"""
        calls = []

        def _executor():
            calls.append(1)
            return True

        probe = PgVectorExtensionProbe(executor=_executor, enabled=False)
        assert probe.is_acceleration_available() is False
        assert calls == []

    def test_vector_literal_format(self):
        assert _vector_literal([1.0, 2.5]) == "[1.0,2.5]"


# =============================================================================
# 服务层 pg_array 分派（T5.3 回退切换端到端）
# =============================================================================


class TestServiceStorageDispatch:
    def test_default_dispatch_sqlite(self, tmp_path):
        service = LocalVectorDatabaseService(config=_svc_config(tmp_path))
        from orchestration_platform.integrations.local_vector_database.store import (
            SqliteVectorStore,
        )

        assert isinstance(service._store, SqliteVectorStore)

    def test_pg_array_dispatch_with_fallback_annotation(self, tmp_path):
        """pg_array + 探测未命中 → 检索回退应用层距离且可辨识降级登记。"""
        from orchestration_platform.integrations.local_vector_database.pg_array_store import (
            PgArrayVectorStore as _Pg,
        )

        service = LocalVectorDatabaseService(
            config=LocalVectorDatabaseConfig(
                enabled=True, storage_root=str(tmp_path), storage="pg_array"
            )
        )
        assert isinstance(service._store, _Pg)
        # 注入假连接与未命中探测器
        state = {"meta": {}, "records": {}, "pgvector": False}
        service._store._connector = _make_connector(state)
        service._store._probe = PgVectorExtensionProbe(executor=lambda: False)
        service.upsert_records(
            "kb",
            [
                VectorRecord(
                    record_id="r1",
                    vector=[1.0, 0.0],
                    text="真实向量",
                    source={"provenance": "api"},
                ),
            ],
        )
        outcome = service.retrieve("kb", [1.0, 0.0], 5)
        assert outcome.local_degraded is True
        assert "pgvector" in outcome.local_degraded_reason
        assert outcome.chunks[0].score == pytest.approx(1.0, abs=1e-9)

    def test_pg_array_accelerated_no_probe_degradation(self, tmp_path):
        """pg_array + 探测命中 → SQL 预排序增强路径，无回退降级标注。"""
        service = LocalVectorDatabaseService(
            config=LocalVectorDatabaseConfig(
                enabled=True, storage_root=str(tmp_path), storage="pg_array"
            )
        )
        state = {"meta": {}, "records": {}, "pgvector": True}
        service._store._connector = _make_connector(state)
        service._store._probe = PgVectorExtensionProbe(executor=lambda: True)
        service.upsert_records(
            "kb",
            [
                VectorRecord(
                    record_id="r1",
                    vector=[1.0, 0.0],
                    text="真实向量",
                    source={"provenance": "api"},
                ),
            ],
        )
        outcome = service.retrieve("kb", [1.0, 0.0], 5)
        assert outcome.local_degraded is False
        assert outcome.chunks[0].text == "真实向量"


def _make_connector(state):
    async def connector():
        return FakeAsyncConn(state)

    return connector


def test_asyncio_bridge_runs_per_call():
    """同步桥每操作独立事件循环（asyncpg 池跨循环不可复用的规避口径）。"""
    state = {"meta": {}, "records": {}, "pgvector": False}
    store = _pg_store(state)
    store.ensure_collection("kb", 1, "cosine")
    store.upsert("kb", VectorRecord(record_id="r1", vector=[1.0], text="t"))
    # 连续两次独立调用均成功（各自 asyncio.run）
    assert store.delete("kb", "r1") is True
