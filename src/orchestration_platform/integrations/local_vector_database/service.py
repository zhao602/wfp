# =============================================================================
# 本地向量数据库 service facade（integrations/local_vector_database/service.py）
#
# 职责：组装 store/index/probe，提供六项公开 API（CHG-L08 / design
# DC-01、§2.2.2 组 1）。
# 设计意图（对应 local_vector_database design INV-L03 / INV-L05 / INV-L09 /
# INV-L15、spec FR-05~FR-09 / AC-04 / AC-06 / AC-13 / AC-27）：
#   1. 出口契约复用 vector_knowledge_base.models：retrieve 出口经
#      sanitize_chunk_payload 净化后组装 RetrievalOutcome（不新造平行
#      契约，INV-L03/AC-27）；
#   2. 批量写/删/导入：逐条失败不整体失败（部分成功，AC-04/AC-13）；
#      集合标识前置校验（非法 → 确定性错误码，禁路径穿越 INV-L10）；
#   3. import_jsonl 文件不可读 → 整体失败 VECTOR_DB_IMPORT_FILE_UNREADABLE
#      （无部分写入）；export_jsonl 产物禁凭据（INV-L09）；
#   4. 增强后端声明启用但本期未接入增强检索实现（P2，DC-09）→
#      fail-safe 确定性降级为暴力精确检索 + local_degraded 标注（DC-05）；
#   5. 构造零副作用（懒初始化，AC-28）：不触盘、不探测依赖。
# =============================================================================

import json
from typing import Any, List, Optional, Sequence

from pydantic import ValidationError

from orchestration_platform.domain.errors import (
    VECTOR_DB_IMPORT_FILE_UNREADABLE,
    VECTOR_DB_STORAGE_UNAVAILABLE,
)
from orchestration_platform.integrations.local_vector_database.config import (
    LocalVectorDatabaseConfig,
    from_settings,
)
from orchestration_platform.integrations.local_vector_database.models import (
    EMBEDDING_KIND_HASH,
    DeleteReport,
    DiagnosticReport,
    ExportReport,
    ImportReport,
    MetadataFilter,
    RecordFailure,
    UpsertReport,
    VectorRecord,
)
from orchestration_platform.integrations.local_vector_database.pg_array_store import (
    PgArrayVectorStore,
)
from orchestration_platform.integrations.local_vector_database.retrieval import (
    BruteForceIndex,
    OptionalBackendProbe,
)
from orchestration_platform.integrations.local_vector_database.store import (
    SqliteVectorStore,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    RetrievalOutcome,
    VectorRetrievalError,
    sanitize_chunk_payload,
)


class LocalVectorDatabaseService:
    """本地向量数据库 facade（design §2.2.2 组 1 公开 API，DC-01）。

    构造零副作用（不触盘、不探测依赖，AC-28）；config=None 时经
    config.from_settings() 只读派生（注入式构造可绕开全局缓存）。
    """

    def __init__(self, config: Optional[LocalVectorDatabaseConfig] = None) -> None:
        self._config = config if config is not None else from_settings()
        self._storage = getattr(self._config, "storage", "sqlite") or "sqlite"
        if self._storage == "pg_array":
            # 新建集合可选路径（DC-B2-05 ①）：PostgreSQL 原生数组存储；
            # 构造零副作用（不连库、不探测）
            self._store: Any = PgArrayVectorStore(
                default_metric=self._config.default_metric,
                max_records_per_collection=self._config.max_records_per_collection,
            )
            self._pg_array = True
        else:
            # 存量默认路径：sqlite 行为零变化（HC-05）
            self._store = SqliteVectorStore(
                storage_root=self._config.storage_root,
                default_metric=self._config.default_metric,
                max_records_per_collection=self._config.max_records_per_collection,
            )
            self._pg_array = False
        self._index = BruteForceIndex(self._store, max_top_k=self._config.max_top_k)
        self._probe = OptionalBackendProbe()

    # ------------------------------------------------------------------
    # 公开 API（design §2.2.2 组 1）
    # ------------------------------------------------------------------

    def upsert_records(
        self, collection_id: str, records: Sequence[VectorRecord]
    ) -> UpsertReport:
        """批量幂等 upsert（AC-04/AC-05）：逐条失败不整体失败（部分成功）。"""
        self._require_collection_id(collection_id)
        upserted = 0
        failed: List[RecordFailure] = []
        for record in records:
            try:
                self._store.upsert(collection_id, record)
                upserted += 1
            except VectorRetrievalError as error:
                failed.append(
                    RecordFailure(
                        record_id=record.record_id, reason=error.error_message
                    )
                )
        return UpsertReport(upserted_count=upserted, failed=failed)

    def delete_records(
        self, collection_id: str, record_ids: Sequence[str]
    ) -> DeleteReport:
        """幂等删除（AC-06/AC-07）：不存在条目返回“未删除”，不视为错误。"""
        self._require_collection_id(collection_id)
        deleted = 0
        missing: List[str] = []
        for record_id in record_ids:
            if self._store.delete(collection_id, record_id):
                deleted += 1
            else:
                missing.append(record_id)
        return DeleteReport(deleted_count=deleted, missing_ids=missing)

    def retrieve(
        self,
        collection_id: str,
        query_vector: Sequence[float],
        top_k: int,
        filter_: Optional[MetadataFilter] = None,
        metric: Optional[str] = None,
    ) -> RetrievalOutcome:
        """Top-K 检索（AC-08/AC-27）：出口复用 sanitize_chunk_payload +
        RetrievalOutcome（INV-L03）。

        - metric=None → 取配置默认度量；
        - 增强后端声明启用但本期未接入实现（P2）→ fail-safe 确定性
          降级为暴力精确检索 + local_degraded 标注（DC-05）。
        """
        effective_metric = (
            metric if metric is not None else self._config.default_metric
        )
        accelerated_used = False
        pg_array_fallback_reason: Optional[str] = None
        if (
            self._pg_array
            and effective_metric == "cosine"
            and filter_ is None
            and top_k >= 1
        ):
            # pg_array 可选增强路径（DC-B2-05 ②）：pgvector `<=>` SQL 预排序；
            # 不可用/失败 → None → 回退应用层距离（可辨识降级登记，禁静默）
            accelerated_hits = self._store.search_accelerated(
                collection_id,
                list(query_vector),
                min(top_k, self._config.max_top_k),
                self._config.max_top_k,
                effective_metric,
            )
            if accelerated_hits is not None:
                hits = accelerated_hits
                accelerated_used = True
            else:
                pg_array_fallback_reason = (
                    "pgvector 扩展未命中/未启用或 SQL 加速不可用，"
                    "已自动回退应用层距离计算（可辨识降级，禁静默）"
                )
        else:
            hits = []
        if not accelerated_used:
            hits = self._index.search(
                collection_id, query_vector, top_k, filter_, effective_metric
            )
        payload = [
            {"text": hit.text, "score": hit.score, "source": hit.source}
            for hit in hits
        ]
        base_reason = None
        if self._config.backend:
            base_reason = (
                "已声明增强后端但本期未接入增强检索实现，"
                "已确定性降级为暴力精确检索（不伪造检索语义）"
            )
        if pg_array_fallback_reason and hits:
            base_reason = (
                pg_array_fallback_reason
                if base_reason is None
                else f"{base_reason}；{pg_array_fallback_reason}"
            )
        # hash 代际出口守护（DC-B2-05 ④ / INV-B2-05）：命中含 hash 伪向量代际
        # 且出口尚无降级标注 → 强制降级标注（不冒充真实语义，INV-L08 延伸）
        if hits and not base_reason:
            hash_present = any(
                getattr(hit, "embedding_kind", None) == EMBEDDING_KIND_HASH
                for hit in hits
            )
            if hash_present:
                base_reason = (
                    "命中记录包含 hash 伪向量代际（存量缺省或显式伪向量），"
                    "已保持降级标注（不冒充真实语义，INV-B2-05）"
                )
        if base_reason:
            return RetrievalOutcome(
                chunks=sanitize_chunk_payload(payload),
                local_degraded=True,
                local_degraded_reason=base_reason,
            )
        return RetrievalOutcome(chunks=sanitize_chunk_payload(payload))

    def import_jsonl(self, collection_id: str, path: str) -> ImportReport:
        """JSONL 导入（AC-13）：逐行解析、逐条校验、部分成功。

        文件不可读（缺失/权限/编码）→ 整体失败
        VECTOR_DB_IMPORT_FILE_UNREADABLE（无部分写入）。
        """
        self._require_collection_id(collection_id)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError):
            raise VectorRetrievalError(
                VECTOR_DB_IMPORT_FILE_UNREADABLE,
                f"导入文件不可读（路径：{path}）",
            ) from None
        imported = 0
        failed: List[RecordFailure] = []
        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                failed.append(
                    RecordFailure(
                        record_id=f"line:{line_number}",
                        reason="空行不是合法的向量记录（须为 JSON 对象）",
                    )
                )
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("行不是 JSON 对象")
                record = VectorRecord.model_validate(payload)
            except (ValueError, ValidationError):
                failed.append(
                    RecordFailure(
                        record_id=f"line:{line_number}",
                        reason="行不是合法的向量记录 JSON 对象（字段缺失/类型不符）",
                    )
                )
                continue
            try:
                self._store.upsert(collection_id, record)
                imported += 1
            except VectorRetrievalError as error:
                failed.append(
                    RecordFailure(
                        record_id=record.record_id, reason=error.error_message
                    )
                )
        return ImportReport(imported_count=imported, failed=failed)

    def export_jsonl(self, collection_id: str, path: str) -> ExportReport:
        """JSONL 导出（FR-09）：一行一记录全量导出（产物禁凭据，INV-L09）。"""
        self._require_collection_id(collection_id)
        records = list(self._store.load_records(collection_id))
        try:
            with open(path, "w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(record.model_dump_json() + "\n")
        except OSError:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"导出文件写入失败（路径：{path}）",
            ) from None
        return ExportReport(exported_count=len(records))

    def diagnose(self, collection_id: str) -> DiagnosticReport:
        """一致性诊断（AC-35）：透传 store 诊断报告。"""
        return self._store.diagnose(collection_id)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _require_collection_id(self, collection_id: str) -> None:
        """集合标识前置校验（design 组 1 前置条件）。

        经 store.read_meta 只读探针触发白名单校验：非法标识 →
        确定性 VECTOR_DB_INVALID_COLLECTION_ID（禁路径穿越，INV-L10）；
        合法但集合缺失 → None（零副作用，不建集合，INV-L15）。
        """
        self._store.read_meta(collection_id)
