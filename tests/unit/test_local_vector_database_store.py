# =============================================================================
# 本地向量数据库 store 层单元测试（tests/unit/test_local_vector_database_store.py）
#
# 覆盖（CHG-L09 / design §9.1 store 行，AC 对照）：
#   - 建集合/首写固化维度（AC-20）/ 集合标识白名单与落点边界（INV-L10）
#   - 幂等 upsert（同键覆盖不重复，AC-04/AC-05）/ 非法向量确定性拒绝（AC-08）
#   - delete 存在/不存在（AC-06/AC-07）/ 条目上限（FR-29/AC-38）
#   - 载入顺序 / 持久化恢复与写后立即可读（AC-12/AC-34/AC-35/AC-37）
#   - 诊断（AC-35）/ 格式版本跨版本明确拒绝（AC-36）
#   - 存储不可用（磁盘失败不破坏已提交数据）/ 计数自愈
#
# 沙箱验证：PYTHONPATH=src python -m pytest tests/unit/test_local_vector_database_store.py -q
# （同步 API 驱动，禁 asyncio.run；tmp_path 注入存储根，断言落点前缀不越权）
# =============================================================================

import random
import sqlite3

import pytest

from orchestration_platform.domain.errors import (
    VECTOR_DB_DIMENSION_MISMATCH,
    VECTOR_DB_FORMAT_VERSION_INCOMPATIBLE,
    VECTOR_DB_INVALID_COLLECTION_ID,
    VECTOR_DB_INVALID_VECTOR,
    VECTOR_DB_RECORD_LIMIT_EXCEEDED,
    VECTOR_DB_STORAGE_UNAVAILABLE,
)
from orchestration_platform.integrations.local_vector_database.models import (
    VectorRecord,
)
from orchestration_platform.integrations.local_vector_database.store import (
    SqliteVectorStore,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    VectorRetrievalError,
)


def _make_store(tmp_path, **kwargs) -> SqliteVectorStore:
    return SqliteVectorStore(storage_root=str(tmp_path / "vkb"), **kwargs)


def _record(record_id: str, dim: int = 3, text: str = "文本", source=None) -> VectorRecord:
    rng = random.Random(record_id)
    return VectorRecord(
        record_id=record_id,
        vector=[round(rng.random(), 6) for _ in range(dim)],
        text=text,
        source=source,
    )


def _assert_code(code: str, fn, *args, **kwargs) -> VectorRetrievalError:
    with pytest.raises(VectorRetrievalError) as exc_info:
        fn(*args, **kwargs)
    assert exc_info.value.error_code == code
    return exc_info.value


def _raw_update_meta(tmp_path, collection_id: str, key: str, value: str) -> None:
    path = tmp_path / "vkb" / f"{collection_id}.sqlite3"
    conn = sqlite3.connect(str(path))
    try:
        with conn:
            conn.execute(
                "INSERT INTO collection_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
    finally:
        conn.close()


class TestStoreSchemaAndCollectionId:
    """建库/DDL/元信息/白名单（T2.1、T2.2，AC-20、INV-L10）。"""

    def test_ensure_collection_creates_file_and_meta(self, tmp_path):
        store = _make_store(tmp_path)
        meta = store.ensure_collection("kb-1", 128, "cosine")
        path = tmp_path / "vkb" / "kb-1.sqlite3"
        assert path.exists()
        assert meta.collection_id == "kb-1"
        assert meta.declared_dimension == 128
        assert meta.metric == "cosine"
        assert meta.format_version == "1"
        assert meta.created_at
        assert meta.updated_at

    def test_storage_root_created_on_demand(self, tmp_path):
        store = _make_store(tmp_path)
        assert not (tmp_path / "vkb").exists()
        store.ensure_collection("kb-1", 4, "cosine")
        assert (tmp_path / "vkb").is_dir()

    def test_collection_file_within_injected_root(self, tmp_path):
        # INV-L10：落点前缀 = 注入根（不越权）
        store = _make_store(tmp_path)
        store.upsert("kb-1", _record("r-1", dim=2))
        expected_root = str(tmp_path / "vkb")
        assert str(tmp_path / "vkb" / "kb-1.sqlite3").startswith(expected_root)
        all_dbs = list(tmp_path.glob("**/*.sqlite3"))
        assert all_dbs
        for db in all_dbs:
            assert str(db).startswith(expected_root)

    def test_collection_id_whitelist(self, tmp_path):
        store = _make_store(tmp_path)
        for valid_id in ("kb-1", "kb_1", "a1", "a" * 128):
            store.ensure_collection(valid_id, 2, "cosine")
        for invalid_id in ("", "../evil", "a/b", "A", "a b", "kb.1", "a" * 129, ".hidden"):
            _assert_code(
                VECTOR_DB_INVALID_COLLECTION_ID,
                store.ensure_collection,
                invalid_id,
                2,
                "cosine",
            )
        # 路径穿越未越权：根目录外无新增文件
        assert not (tmp_path / "evil.sqlite3").exists()

    def test_ensure_dimension_fixed_on_first_write(self, tmp_path):
        # AC-20：首写固化维度；后续不一致声明被拒；同维度幂等
        store = _make_store(tmp_path)
        store.ensure_collection("kb-1", 128, "cosine")
        again = store.ensure_collection("kb-1", 128, "cosine")
        assert again.declared_dimension == 128
        _assert_code(
            VECTOR_DB_DIMENSION_MISMATCH,
            store.ensure_collection,
            "kb-1",
            384,
            "cosine",
        )

    def test_ensure_dimension_bounds(self, tmp_path):
        store = _make_store(tmp_path)
        _assert_code(VECTOR_DB_INVALID_VECTOR, store.ensure_collection, "kb-1", 0, "cosine")
        _assert_code(
            VECTOR_DB_INVALID_VECTOR, store.ensure_collection, "kb-1", 4097, "cosine"
        )

    def test_constructor_rejects_invalid_metric_and_limit(self):
        with pytest.raises(ValueError):
            SqliteVectorStore(storage_root="./x", default_metric="manhattan")
        with pytest.raises(ValueError):
            SqliteVectorStore(storage_root="./x", max_records_per_collection=0)


class TestStoreUpsert:
    """幂等 upsert（T2.3，AC-04/AC-05/AC-08/AC-20、FR-29）。"""

    def test_upsert_auto_creates_collection_with_first_record_dimension(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.upsert("kb-1", _record("r-1", dim=3)) is True
        meta = store.ensure_collection("kb-1", 3, "cosine")
        assert meta.declared_dimension == 3

    def test_upsert_idempotent_same_record_id_overwrites(self, tmp_path):
        # AC-04/AC-05：同 record_id = 覆盖更新，条目总数不变，内容为最新值
        store = _make_store(tmp_path)
        store.upsert("kb-1", _record("r-1", text="第一版"))
        store.upsert("kb-1", _record("r-1", text="第二版"))
        records = list(store.load_records("kb-1"))
        assert len(records) == 1
        assert records[0].text == "第二版"
        report = store.diagnose("kb-1")
        assert report.record_count == 1
        assert report.ok is True

    def test_upsert_dimension_mismatch_rejected_no_pollution(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert("kb-1", _record("r-1", dim=3))
        _assert_code(
            VECTOR_DB_DIMENSION_MISMATCH, store.upsert, "kb-1", _record("r-2", dim=4)
        )
        # 失败不污染集合（原子性）：仍只有 r-1，诊断健康
        assert [r.record_id for r in store.load_records("kb-1")] == ["r-1"]
        assert store.diagnose("kb-1").ok is True

    def test_upsert_invalid_vector_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert("kb-1", _record("r-1", dim=3))
        nan_record = VectorRecord(
            record_id="r-nan", vector=[0.1, float("nan"), 0.3], text="t"
        )
        _assert_code(VECTOR_DB_INVALID_VECTOR, store.upsert, "kb-1", nan_record)
        inf_record = VectorRecord(
            record_id="r-inf", vector=[float("inf"), 0.2, 0.3], text="t"
        )
        _assert_code(VECTOR_DB_INVALID_VECTOR, store.upsert, "kb-1", inf_record)
        empty_record = VectorRecord(record_id="r-empty", vector=[], text="t")
        _assert_code(VECTOR_DB_INVALID_VECTOR, store.upsert, "kb-1", empty_record)

    def test_upsert_new_collection_dimension_over_4096_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        huge = VectorRecord(record_id="r-huge", vector=[0.1] * 5000, text="t")
        _assert_code(VECTOR_DB_INVALID_VECTOR, store.upsert, "kb-1", huge)
        assert not (tmp_path / "vkb" / "kb-1.sqlite3").exists()

    def test_upsert_text_truncation_annotated(self, tmp_path):
        # 超长文本截断到 2000 并在 source 标注 truncated=true（净化语义对齐）
        store = _make_store(tmp_path)
        long_text = "长" * 2500
        store.upsert(
            "kb-1",
            _record("r-1", text=long_text, source={"type": "hr"}),
        )
        records = list(store.load_records("kb-1"))
        assert len(records) == 1
        assert len(records[0].text) == 2000
        assert records[0].source is not None
        assert records[0].source.get("truncated") is True
        assert records[0].source.get("type") == "hr"

    def test_upsert_record_limit_exceeded(self, tmp_path):
        # FR-29/AC-38：新条目超上限确定性拒绝；更新既有条目不计增
        store = _make_store(tmp_path, max_records_per_collection=2)
        store.upsert("kb-1", _record("r-1"))
        store.upsert("kb-1", _record("r-2"))
        _assert_code(VECTOR_DB_RECORD_LIMIT_EXCEEDED, store.upsert, "kb-1", _record("r-3"))
        # 同键覆盖不受限
        assert store.upsert("kb-1", _record("r-1", text="更新")) is True
        assert store.diagnose("kb-1").record_count == 2
        # 删除后腾出配额，可再写
        assert store.delete("kb-1", "r-2") is True
        assert store.upsert("kb-1", _record("r-3")) is True
        assert store.diagnose("kb-1").record_count == 2


class TestStoreDelete:
    """幂等 delete（T2.4，AC-06/AC-07）。"""

    def test_delete_existing_returns_true_and_removes(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert("kb-1", _record("r-1"))
        store.upsert("kb-1", _record("r-2"))
        assert store.delete("kb-1", "r-1") is True
        assert [r.record_id for r in store.load_records("kb-1")] == ["r-2"]
        report = store.diagnose("kb-1")
        assert report.record_count == 1
        assert report.ok is True

    def test_delete_missing_record_returns_false(self, tmp_path):
        # AC-07：不存在 → 成功返回“未删除”，集合状态不变
        store = _make_store(tmp_path)
        store.upsert("kb-1", _record("r-1"))
        assert store.delete("kb-1", "ghost") is False
        assert store.diagnose("kb-1").record_count == 1

    def test_delete_on_missing_collection_no_side_effect(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.delete("kb-1", "r-1") is False
        # 未建库、零副作用（懒初始化）
        assert not (tmp_path / "vkb").exists()

    def test_delete_then_upsert_reuses_slot(self, tmp_path):
        store = _make_store(tmp_path, max_records_per_collection=1)
        store.upsert("kb-1", _record("r-1"))
        _assert_code(VECTOR_DB_RECORD_LIMIT_EXCEEDED, store.upsert, "kb-1", _record("r-2"))
        assert store.delete("kb-1", "r-1") is True
        assert store.upsert("kb-1", _record("r-2")) is True


class TestStoreLoadAndPersistence:
    """载入/持久化/恢复（T2.5，AC-12/AC-34/AC-35/AC-37）。"""

    def test_load_records_ordered_by_record_id(self, tmp_path):
        store = _make_store(tmp_path)
        for rid in ("r-c", "r-a", "r-b"):
            store.upsert("kb-1", _record(rid))
        assert [r.record_id for r in store.load_records("kb-1")] == [
            "r-a",
            "r-b",
            "r-c",
        ]

    def test_load_roundtrips_vector_text_source(self, tmp_path):
        store = _make_store(tmp_path)
        record = _record("r-1", text="报销流程", source={"type": "hr", "page": 3})
        store.upsert("kb-1", record)
        loaded = list(store.load_records("kb-1"))[0]
        assert loaded.text == "报销流程"
        assert loaded.source == {"type": "hr", "page": 3}
        # struct 为 float32 定长编码：值在容差内一致
        assert loaded.vector == pytest.approx(record.vector, abs=1e-6)

    def test_load_missing_collection_empty_success(self, tmp_path):
        # INV-L15：集合不存在 → 空载入（成功，非失败），零副作用
        store = _make_store(tmp_path)
        assert list(store.load_records("kb-1")) == []
        assert not (tmp_path / "vkb").exists()

    def test_persistence_across_reopen(self, tmp_path):
        # AC-12/AC-34：写入 → 重开（新实例）→ 内容与计数一致
        store_a = _make_store(tmp_path)
        originals = [_record(rid) for rid in ("r-1", "r-2", "r-3")]
        for record in originals:
            store_a.upsert("kb-1", record)
        store_b = _make_store(tmp_path)
        reloaded = list(store_b.load_records("kb-1"))
        assert [r.record_id for r in reloaded] == ["r-1", "r-2", "r-3"]
        for original, again in zip(originals, reloaded):
            assert again.vector == pytest.approx(original.vector, abs=1e-6)
            assert again.text == original.text
        assert store_b.diagnose("kb-1").ok is True

    def test_write_visible_immediately(self, tmp_path):
        # AC-35：写后立即可读（增量即时可见）
        store = _make_store(tmp_path)
        store.upsert("kb-1", _record("r-1"))
        assert len(list(store.load_records("kb-1"))) == 1
        store.upsert("kb-1", _record("r-2"))
        assert len(list(store.load_records("kb-1"))) == 2


class TestStoreFormatVersionAndCorruption:
    """格式版本判定/损坏（T2.2/T2.5，AC-36/AC-37）。"""

    def test_format_version_roundtrip(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.format_version("kb-1") == ""
        store.ensure_collection("kb-1", 4, "cosine")
        assert store.format_version("kb-1") == "1"

    def test_cross_version_rejected_not_silently_misread(self, tmp_path):
        # AC-36：跨版本明确拒绝（禁静默错读）
        store = _make_store(tmp_path)
        store.upsert("kb-1", _record("r-1", dim=3))
        _raw_update_meta(tmp_path, "kb-1", "format_version", "999")
        assert store.format_version("kb-1") == "999"
        _assert_code(
            VECTOR_DB_FORMAT_VERSION_INCOMPATIBLE,
            store.upsert,
            "kb-1",
            _record("r-2", dim=3),
        )
        _assert_code(VECTOR_DB_FORMAT_VERSION_INCOMPATIBLE, store.ensure_collection, "kb-1", 3, "cosine")
        with pytest.raises(VectorRetrievalError) as exc_info:
            list(store.load_records("kb-1"))
        assert exc_info.value.error_code == VECTOR_DB_FORMAT_VERSION_INCOMPATIBLE
        # 诊断不崩溃：可诊断报告（ok=False）
        report = store.diagnose("kb-1")
        assert report.ok is False
        assert any("版本" in issue for issue in report.issues)

    def test_garbage_file_storage_unavailable_and_diagnosable(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "vkb").mkdir(parents=True)
        (tmp_path / "vkb" / "kb-1.sqlite3").write_bytes(b"this-is-not-a-sqlite-db")
        _assert_code(
            VECTOR_DB_STORAGE_UNAVAILABLE, store.upsert, "kb-1", _record("r-1")
        )
        with pytest.raises(VectorRetrievalError) as load_exc:
            list(store.load_records("kb-1"))
        assert load_exc.value.error_code == VECTOR_DB_STORAGE_UNAVAILABLE
        # 诊断可见（不崩溃、可定位）
        report = store.diagnose("kb-1")
        assert report.ok is False
        assert report.integrity_check == "unreadable"

    def test_storage_root_blocked_by_file(self, tmp_path):
        # 磁盘/落点不可用 → 明确报错，不产生半初始化
        blocker = tmp_path / "blocker"
        blocker.write_text("occupied")
        store = SqliteVectorStore(storage_root=str(blocker))
        _assert_code(
            VECTOR_DB_STORAGE_UNAVAILABLE,
            store.ensure_collection,
            "kb-1",
            3,
            "cosine",
        )
        assert blocker.read_text() == "occupied"

    def test_diagnose_detects_count_drift(self, tmp_path):
        # 计数漂移可诊断（AC-35）
        store = _make_store(tmp_path)
        store.upsert("kb-1", _record("r-1"))
        _raw_update_meta(tmp_path, "kb-1", "record_count", "42")
        report = store.diagnose("kb-1")
        assert report.ok is False
        assert any("计数" in issue for issue in report.issues)

    def test_record_count_selfheal_after_corruption(self, tmp_path):
        # meta 计数损坏 → 回退 COUNT(*) 自愈，写入继续正确
        store = _make_store(tmp_path)
        store.upsert("kb-1", _record("r-1"))
        _raw_update_meta(tmp_path, "kb-1", "record_count", "garbage")
        assert store.upsert("kb-1", _record("r-2")) is True
        report = store.diagnose("kb-1")
        assert report.record_count == 2
        assert report.ok is True

    def test_committed_data_survives_failed_write(self, tmp_path):
        # 磁盘写失败语义：失败写入不破坏已提交数据
        store = _make_store(tmp_path)
        store.upsert("kb-1", _record("r-1"))
        _assert_code(
            VECTOR_DB_DIMENSION_MISMATCH, store.upsert, "kb-1", _record("r-bad", dim=9)
        )
        reloaded = list(store.load_records("kb-1"))
        assert [r.record_id for r in reloaded] == ["r-1"]
        assert store.diagnose("kb-1").ok is True


class TestStoreBoundaryHardening:
    """T8.1 边界加固：存储根不可用明确报错 + 超限失败写不破坏已提交数据（AC-38 / DFX §4.1-3）。"""

    def test_unwritable_storage_root_storage_unavailable(self, tmp_path):
        # 存储根落点被文件占位 → makedirs OSError → 确定性 STORAGE_UNAVAILABLE（不崩溃）
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        store = SqliteVectorStore(
            storage_root=str(blocker / "nested"), default_metric="cosine"
        )
        error = _assert_code(
            VECTOR_DB_STORAGE_UNAVAILABLE, store.upsert, "kb-1", _record("r-1")
        )
        assert "存储根目录" in error.error_message

    def test_record_limit_failure_leaves_committed_data_intact(self, tmp_path):
        # 超限写失败（事务回滚）：已提交条目/计数/可读性/诊断均保持
        store = SqliteVectorStore(
            storage_root=str(tmp_path / "vkb"),
            default_metric="cosine",
            max_records_per_collection=2,
        )
        assert store.upsert("kb-1", _record("r-1")) is True
        assert store.upsert("kb-1", _record("r-2")) is True
        _assert_code(
            VECTOR_DB_RECORD_LIMIT_EXCEEDED, store.upsert, "kb-1", _record("r-3")
        )
        reloaded = list(store.load_records("kb-1"))
        assert [r.record_id for r in reloaded] == ["r-1", "r-2"]
        assert reloaded[0].vector == pytest.approx(_record("r-1").vector, abs=1e-6)
        report = store.diagnose("kb-1")
        assert report.ok is True
        assert report.record_count == 2
