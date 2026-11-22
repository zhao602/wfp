# =============================================================================
# PostgreSQL 原生数组向量存储（integrations/local_vector_database/pg_array_store.py）
#
# 职责：PgArrayVectorStore——与 SqliteVectorStore 同接口契约的可选存储路径
# （B2-C5 / design DC-B2-05 ①，CHG-B2 预告文件级设计）。
# 设计意图（对应 arch_review_gap_b2 spec FR-B2-05 / tasks T5.3/T5.4）：
#   1. 存储形态：表 vector_kb_records(collection_id, record_id, payload JSONB,
#      embedding DOUBLE PRECISION[], embedding_kind, PK(collection_id,
#      record_id)) + collection_meta（维度/度量/版本/计数）——schema 迁移走
#      persistence/postgres/schema_migrations 既有范式（v1_4 迁移文件）；
#   2. 连接复用：asyncpg 延迟导入 + 运行期 settings 同源配置（对齐
#      persistence/postgres/postgres_workflow_repository 既有范式），零新增
#      依赖（GK-06）；每操作短连接（对齐 SqliteVectorStore 短连接语义）；
#   3. 距离权威路径 = 应用层 vector_math.metric_score（sqlite/pg_array 同源
#      零漂移，DC-B2-05 ②）；SQL 算子（<=>）仅为 probe 命中时的可选增强
#      排序路径，未命中/失败自动回退应用层距离（可辨识降级登记，禁静默）；
#   4. 接口契约与 SqliteVectorStore 一致（ensure_collection/upsert/delete/
#      load_records/format_version/read_meta/diagnose），可直接接入既有
#      BruteForceIndex（duck typing，零检索层改动）；
#   5. 构造零副作用（不连库、不探测，对齐 AC-28 懒初始化范式）。
# =============================================================================

import asyncio
import importlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from orchestration_platform.domain.errors import (
    VECTOR_DB_DIMENSION_MISMATCH,
    VECTOR_DB_INVALID_COLLECTION_ID,
    VECTOR_DB_INVALID_VECTOR,
    VECTOR_DB_RECORD_LIMIT_EXCEEDED,
    VECTOR_DB_STORAGE_UNAVAILABLE,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.integrations.local_vector_database.models import (
    FORMAT_VERSION,
    CollectionMeta,
    DiagnosticReport,
    VectorRecord,
    VectorSearchHit,
    resolve_embedding_kind,
)
from orchestration_platform.integrations.local_vector_database.pg_vector_probe import (
    PgVectorExtensionProbe,
)
from orchestration_platform.integrations.local_vector_database.store import (
    _validate_collection_id,
    _validate_dimension,
    _validate_vector_components,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    CHUNK_TEXT_MAX_LENGTH,
    VectorRetrievalError,
)

# pg_array 存储格式版本（与 sqlite FORMAT_VERSION 同版本号管理）
PG_FORMAT_VERSION = FORMAT_VERSION


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PgArrayVectorStore:
    """PostgreSQL 原生数组向量存储（新建集合可选路径；DC-B2-05 ①）。

    - 同步门面：方法内部经 asyncio.run 桥接 asyncpg 异步驱动（既有检索层
      BruteForceIndex 为同步管线，本存储以同步接口直接接入）；
    - 每操作短连接（连接 → 执行 → 关闭），与 sqlite 短连接语义对齐；
    - 构造零副作用：不连库、不探测（AC-28）。
    """

    def __init__(
        self,
        default_metric: str = "cosine",
        max_records_per_collection: int = 100000,
        probe: Optional[PgVectorExtensionProbe] = None,
        connector: Optional[Any] = None,
    ) -> None:
        """构造零副作用。

        probe —— pgvector 扩展探测器（缺省新建；结果缓存于探测器内）。
        connector —— 可注入异步连接工厂（async () -> conn；单测 mock seam；
        缺省按运行期 settings 经 asyncpg.connect 建短连接）。
        """
        if default_metric not in ("cosine", "dot", "euclidean"):
            raise ValueError(f"未知度量取值：{default_metric}")
        if max_records_per_collection < 1:
            raise ValueError("max_records_per_collection 须为正整数")
        self._default_metric = default_metric
        self._max_records_per_collection = max_records_per_collection
        self._probe = probe if probe is not None else PgVectorExtensionProbe()
        self._connector = connector

    # ------------------------------------------------------------------
    # 探测与增强路径（DC-B2-05 ②）
    # ------------------------------------------------------------------

    @property
    def probe(self) -> PgVectorExtensionProbe:
        return self._probe

    def acceleration_available(self) -> bool:
        """SQL 算子增强路径是否可用（probe 命中且启用）。"""
        try:
            return self._probe.is_acceleration_available()
        except Exception:
            return False

    def search_accelerated(
        self,
        collection_id: str,
        query_vector: List[float],
        top_k: int,
        max_top_k: int,
        metric: str,
    ) -> Optional[List[VectorSearchHit]]:
        """可选增强路径：pgvector `<=>` cosine 距离 SQL 预排序 + 应用层权威计分。

        - 仅 metric=cosine 且 probe 命中且启用时走 SQL 预排序（取 top_k 邻域
          候选），再以 vector_math 同源口径逐条计分（分数仍由应用层权威计算，
          语义零漂移）；
        - 任何不可用/失败 → None（调用方回退应用层全量扫描路径，并可辨识
          降级登记，禁静默——DC-B2-05 ②）。
        """
        if metric != "cosine":
            return None
        if not self.acceleration_available():
            return None
        try:
            rows = self._run(
                self._fetch_top_by_cosine(collection_id, query_vector, top_k)
            )
        except Exception:
            return None
        hits: List[VectorSearchHit] = []
        for record_id, payload_json, embedding, kind, _distance in rows:
            payload = json.loads(payload_json) if isinstance(payload_json, str) else (
                payload_json or {}
            )
            source = payload.get("source") if isinstance(payload, dict) else None
            text = str(payload.get("text") or "")
            hits.append(
                VectorSearchHit(
                    record_id=record_id,
                    text=text,
                    score=self._score_cosine(query_vector, embedding, collection_id),
                    source=source,  # type: ignore[arg-type]
                    embedding_kind=resolve_embedding_kind(kind, source),  # type: ignore[arg-type]
                )
            )
        return hits

    # ------------------------------------------------------------------
    # 与 SqliteVectorStore 同接口契约（duck typing 接入 BruteForceIndex）
    # ------------------------------------------------------------------

    def ensure_collection(
        self, collection_id: str, dimension: int, metric: str
    ) -> CollectionMeta:
        """建集合/读元信息：首写固化维度（AC-20），同接口语义。"""
        _validate_collection_id(collection_id)
        _validate_dimension(dimension, collection_id)
        if metric not in ("cosine", "dot", "euclidean"):
            raise ValueError(f"未知度量取值：{metric}")
        try:
            existing = self._run(self._read_meta_async(collection_id))
        except VectorRetrievalError:
            raise
        except Exception as error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"pg_array 存储集合元信息不可读（集合：{collection_id}）",
            ) from error
        if existing is not None:
            if existing.declared_dimension != dimension:
                raise VectorRetrievalError(
                    VECTOR_DB_DIMENSION_MISMATCH,
                    (
                        f"向量维度与集合声明不一致（集合：{collection_id}；"
                        f"声明维度：{existing.declared_dimension}；"
                        f"传入维度：{dimension}）"
                    ),
                )
            return existing
        now = _utc_now_iso()
        try:
            self._run(
                self._insert_collection_meta_async(
                    collection_id, dimension, metric, now
                )
            )
        except Exception as error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"pg_array 存储集合创建失败（集合：{collection_id}）",
            ) from error
        return CollectionMeta(
            collection_id=collection_id,
            declared_dimension=dimension,
            metric=metric,  # type: ignore[arg-type]
            format_version=PG_FORMAT_VERSION,
            created_at=now,
            updated_at=now,
        )

    def upsert(self, collection_id: str, record: VectorRecord) -> bool:
        """幂等写入/更新：同 record_id 覆盖（ON CONFLICT DO UPDATE）。"""
        _validate_vector_components(record.vector, collection_id)
        payload: Dict[str, JsonValue] = {"text": record.text}
        if record.source is not None:
            payload["source"] = record.source
        if len(record.text) > CHUNK_TEXT_MAX_LENGTH:
            payload["text"] = record.text[:CHUNK_TEXT_MAX_LENGTH]
            source_annotated: Dict[str, JsonValue] = {"truncated": True}
            if record.source is not None:
                source_annotated.update(record.source)
                source_annotated["truncated"] = True
            payload["source"] = source_annotated
        try:
            self._run(
                self._upsert_async(collection_id, record, json.dumps(payload, ensure_ascii=False))
            )
        except VectorRetrievalError:
            raise
        except Exception as error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"pg_array 存储写入失败（集合：{collection_id}）",
            ) from error
        return True

    def delete(self, collection_id: str, record_id: str) -> bool:
        """幂等删除：不存在 → False（不视为错误，AC-06/AC-07）。"""
        try:
            return bool(
                self._run(self._delete_async(collection_id, record_id))
            )
        except Exception as error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"pg_array 存储删除失败（集合：{collection_id}）",
            ) from error

    def load_records(self, collection_id: str) -> Iterator[VectorRecord]:
        """全量载入记录（按 record_id 升序；集合不存在 → 空载入，INV-L15）。"""
        try:
            rows = self._run(self._load_async(collection_id))
        except VectorRetrievalError:
            raise
        except Exception as error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"pg_array 存储读取失败（集合：{collection_id}）",
            ) from error
        for record_id, payload_json, embedding, kind in rows:
            payload = json.loads(payload_json) if isinstance(payload_json, str) else (
                payload_json or {}
            )
            source = payload.get("source") if isinstance(payload, dict) else None
            yield VectorRecord(
                record_id=record_id,
                vector=[float(v) for v in (embedding or [])],
                text=str(payload.get("text") or ""),
                source=source,  # type: ignore[arg-type]
                embedding_kind=resolve_embedding_kind(kind, source),  # type: ignore[arg-type]
            )

    def format_version(self, collection_id: str) -> str:
        """读取集合持久化格式版本（集合不存在 → 空串；如实上报）。"""
        try:
            meta = self._run(self._read_meta_async(collection_id))
        except Exception as error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"pg_array 存储集合元信息不可读（集合：{collection_id}）",
            ) from error
        return meta.format_version if meta is not None else ""

    def read_meta(self, collection_id: str) -> Optional[CollectionMeta]:
        """只读集合元信息（集合缺失 → None，零副作用，INV-L15）。"""
        _validate_collection_id(collection_id)
        try:
            return self._run(self._read_meta_async(collection_id))
        except VectorRetrievalError as error:
            if error.error_code == VECTOR_DB_INVALID_COLLECTION_ID:
                raise
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"pg_array 存储集合元信息不可读（集合：{collection_id}）",
            ) from error
        except Exception as error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"pg_array 存储集合元信息不可读（集合：{collection_id}）",
            ) from error

    def diagnose(self, collection_id: str) -> DiagnosticReport:
        """一致性诊断（AC-35 语义）：计数/维度/版本；诊断不抛异常。"""
        issues: List[str] = []
        try:
            meta = self._run(self._read_meta_async(collection_id))
        except Exception:
            return DiagnosticReport(
                collection_id=collection_id,
                ok=False,
                integrity_check="unreadable",
                dimension_consistent=False,
                declared_dimension=None,
                record_count=0,
                format_version="",
                issues=["pg_array 集合元信息不可读（连接失败或集合不存在）"],
            )
        if meta is None:
            return DiagnosticReport(
                collection_id=collection_id,
                ok=False,
                integrity_check="collection_missing",
                dimension_consistent=False,
                declared_dimension=None,
                record_count=0,
                format_version="",
                issues=["集合不存在或尚未创建"],
            )
        try:
            count, bad_dim = self._run(self._diagnose_async(collection_id))
        except Exception as error:
            return DiagnosticReport(
                collection_id=collection_id,
                ok=False,
                integrity_check="unreadable",
                dimension_consistent=False,
                declared_dimension=meta.declared_dimension,
                record_count=0,
                format_version=meta.format_version,
                issues=[f"pg_array 记录扫描失败：{type(error).__name__}"],
            )
        dimension_consistent = bad_dim == 0
        if not dimension_consistent:
            issues.append(f"存在维度与声明不一致的记录（数量：{bad_dim}）")
        acceleration = self.acceleration_available()
        if not acceleration:
            issues.append(
                "pgvector 扩展未启用/不可用，检索采用应用层距离计算"
                "（可辨识降级，禁静默；DC-B2-05 ②）"
            )
        return DiagnosticReport(
            collection_id=collection_id,
            ok=dimension_consistent,
            integrity_check="pg_array",
            dimension_consistent=dimension_consistent,
            declared_dimension=meta.declared_dimension,
            record_count=count,
            format_version=meta.format_version,
            issues=issues,
        )

    # ------------------------------------------------------------------
    # 内部：同步桥 / 连接 / SQL
    # ------------------------------------------------------------------

    def _run(self, coro: Any) -> Any:
        """同步门面桥：asyncio.run 执行协程（每操作独立事件循环 + 短连接）。"""
        return asyncio.run(coro)

    async def _connect(self) -> Any:
        if self._connector is not None:
            return await self._connector()
        asyncpg = importlib.import_module("asyncpg")
        from orchestration_platform.configuration.settings import get_settings

        settings = get_settings()
        return await asyncpg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
        )

    async def _read_meta_async(self, collection_id: str) -> Optional[CollectionMeta]:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                "SELECT collection_id, declared_dimension, metric, format_version, "
                "created_at, updated_at FROM collection_meta WHERE collection_id = $1",
                collection_id,
            )
        finally:
            await conn.close()
        if row is None:
            return None
        try:
            return CollectionMeta(
                collection_id=str(row["collection_id"]),
                declared_dimension=int(row["declared_dimension"]),
                metric=str(row["metric"]),
                format_version=str(row["format_version"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
        except Exception as error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"pg_array 集合元信息缺失或损坏（集合：{collection_id}）",
            ) from error

    async def _insert_collection_meta_async(
        self, collection_id: str, dimension: int, metric: str, now: str
    ) -> None:
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO collection_meta(collection_id, declared_dimension, "
                    "metric, format_version, created_at, updated_at, record_count) "
                    "VALUES($1, $2, $3, $4, $5, $5, 0) "
                    "ON CONFLICT (collection_id) DO NOTHING",
                    collection_id,
                    dimension,
                    metric,
                    PG_FORMAT_VERSION,
                    now,
                )
        finally:
            await conn.close()

    async def _upsert_async(
        self, collection_id: str, record: VectorRecord, payload_json: str
    ) -> None:
        conn = await self._connect()
        try:
            async with conn.transaction():
                meta_row = await conn.fetchrow(
                    "SELECT declared_dimension, record_count FROM collection_meta "
                    "WHERE collection_id = $1 FOR UPDATE",
                    collection_id,
                )
                if meta_row is None:
                    # 集合不存在 → 按首条记录维度自动建集合（首写固化维度）
                    await conn.execute(
                        "INSERT INTO collection_meta(collection_id, "
                        "declared_dimension, metric, format_version, created_at, "
                        "updated_at, record_count) VALUES($1, $2, $3, $4, $5, $5, 0)",
                        collection_id,
                        len(record.vector),
                        self._default_metric,
                        PG_FORMAT_VERSION,
                        _utc_now_iso(),
                    )
                    declared = len(record.vector)
                    count = 0
                else:
                    declared = int(meta_row["declared_dimension"])
                    count = int(meta_row["record_count"] or 0)
                if len(record.vector) != declared:
                    raise VectorRetrievalError(
                        VECTOR_DB_DIMENSION_MISMATCH,
                        (
                            f"向量维度与集合声明不一致（集合：{collection_id}；"
                            f"声明维度：{declared}；实际维度：{len(record.vector)}）"
                        ),
                    )
                existing = await conn.fetchrow(
                    "SELECT 1 FROM vector_kb_records "
                    "WHERE collection_id = $1 AND record_id = $2",
                    collection_id,
                    record.record_id,
                )
                if existing is None:
                    if count + 1 > self._max_records_per_collection:
                        raise VectorRetrievalError(
                            VECTOR_DB_RECORD_LIMIT_EXCEEDED,
                            (
                                f"单集合条目数超上限（集合：{collection_id}；"
                                f"上限：{self._max_records_per_collection}）"
                            ),
                        )
                    count += 1
                kind = (
                    record.embedding_kind
                    if record.embedding_kind in ("real", "hash")
                    else None
                )
                await conn.execute(
                    "INSERT INTO vector_kb_records(collection_id, record_id, "
                    "payload, embedding, embedding_kind) VALUES($1, $2, $3::jsonb, "
                    "$4, $5) ON CONFLICT (collection_id, record_id) DO UPDATE SET "
                    "payload = EXCLUDED.payload, embedding = EXCLUDED.embedding, "
                    "embedding_kind = EXCLUDED.embedding_kind",
                    collection_id,
                    record.record_id,
                    payload_json,
                    record.vector,
                    kind,
                )
                await conn.execute(
                    "UPDATE collection_meta SET record_count = $2, updated_at = $3 "
                    "WHERE collection_id = $1",
                    collection_id,
                    count,
                    _utc_now_iso(),
                )
        finally:
            await conn.close()

    async def _delete_async(self, collection_id: str, record_id: str) -> bool:
        conn = await self._connect()
        try:
            async with conn.transaction():
                meta_row = await conn.fetchrow(
                    "SELECT record_count FROM collection_meta WHERE collection_id = $1",
                    collection_id,
                )
                if meta_row is None:
                    return False
                cursor = await conn.execute(
                    "DELETE FROM vector_kb_records "
                    "WHERE collection_id = $1 AND record_id = $2",
                    collection_id,
                    record_id,
                )
                deleted = cursor.strip().startswith("DELETE 1")
                if deleted:
                    await conn.execute(
                        "UPDATE collection_meta SET record_count = GREATEST("
                        "COALESCE(record_count, 1) - 1, 0), updated_at = $2 "
                        "WHERE collection_id = $1",
                        collection_id,
                        _utc_now_iso(),
                    )
                return deleted
        finally:
            await conn.close()

    async def _load_async(
        self, collection_id: str
    ) -> List[Tuple[str, Any, Any, Any]]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                "SELECT record_id, payload, embedding, embedding_kind "
                "FROM vector_kb_records WHERE collection_id = $1 "
                "ORDER BY record_id",
                collection_id,
            )
        finally:
            await conn.close()
        return [
            (str(row["record_id"]), row["payload"], row["embedding"], row["embedding_kind"])
            for row in rows
        ]

    async def _fetch_top_by_cosine(
        self, collection_id: str, query_vector: List[float], top_k: int
    ) -> List[Tuple[str, Any, Any, Any, float]]:
        """pgvector `<=>` cosine 距离 SQL 预排序（增强路径；扩展在位时可用）。"""
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                "SELECT record_id, payload, embedding, embedding_kind, "
                "(embedding <=> $2::text::vector) AS distance "
                "FROM vector_kb_records WHERE collection_id = $1 "
                "ORDER BY embedding <=> $2::text::vector LIMIT $3",
                collection_id,
                _vector_literal(query_vector),
                top_k,
            )
        finally:
            await conn.close()
        return [
            (
                str(row["record_id"]),
                row["payload"],
                row["embedding"],
                row["embedding_kind"],
                float(row["distance"]),
            )
            for row in rows
        ]

    async def _diagnose_async(self, collection_id: str) -> Tuple[int, int]:
        conn = await self._connect()
        try:
            count_row = await conn.fetchrow(
                "SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE "
                "COALESCE(array_length(embedding, 1), 0) <> "
                "(SELECT declared_dimension FROM collection_meta WHERE "
                "collection_id = $1)) AS bad FROM vector_kb_records "
                "WHERE collection_id = $1",
                collection_id,
            )
        finally:
            await conn.close()
        return int(count_row["n"]), int(count_row["bad"])

    @staticmethod
    def _score_cosine(
        query_vector: List[float], embedding: Any, collection_id: str
    ) -> float:
        """应用层权威计分（vector_math 同源；SQL 仅排序，分数不取 SQL 距离）。"""
        from orchestration_platform.integrations.local_vector_database.vector_math import (
            metric_score,
        )

        if embedding is None:
            raise VectorRetrievalError(
                VECTOR_DB_INVALID_VECTOR,
                f"pg_array 记录向量为空（集合：{collection_id}）",
            )
        return metric_score("cosine", query_vector, [float(v) for v in embedding])


def _vector_literal(vector: List[float]) -> str:
    """pgvector 字面量：'[1,2,3]' 形态（经 ::text::vector 显式转换注入）。"""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"
