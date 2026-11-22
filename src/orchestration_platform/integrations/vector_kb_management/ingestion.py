# =============================================================================
# 向量知识库管理两段式导入流水线（integrations/vector_kb_management/ingestion.py）
#
# 职责：受理（解析 → 分片 → 草稿，零写存储）+ 确认（向量化 → 委托
# upsert_records）+ 委托层错误码分层映射（CHG-07 / design §2.1.3 (a)、
# §2.3.3 委托调用表）。
# 设计意图（vector_kb_management design AC-01/AC-03/AC-06/AC-07/AC-09/
# AC-18/AC-38、INV-D08 / INV-D19 / INV-D20 / INV-D21、DC-04 / DC-05）：
#   1. 草稿与生效分离：受理仅产出草稿 + 作业 pending，向量存储集合零写入
#      （AC-38 / INV-D19，可用 diagnose(collection_id) 断言集合未创建）；
#   2. 确认才向量化并委托 LocalVectorDatabaseService.upsert_records
#      （collection_id=knowledge_base_id；record_id=chunk_id 确定性幂等键，
#      INV-D20：重复确认 → 覆盖更新，不新增重复）；
#   3. 失败不破坏（INV-D21）：向量化失败（无真实来源且未开兜底）→
#      VECTOR_KB_EMBEDDING_UNAVAILABLE，保留既有数据、不半写入（作业落
#      failed，可重确认）；写入失败条目 → partial（失败明细原样上报，AC-09）；
#   4. 降级必须标注（INV-D08）：哈希兜底仅降级路径，local_degraded=True
#      + 原因（伪语义不对外充真实）；出口经 lvd enforce_degraded_annotation
#      守护（hash_fallback 未标注 → InternalConsistencyError）；
#   5. 错误码分层映射（T4.3）：4 码映射（§2.3.3）+ 其余 VECTOR_DB_*
#      原样透传（不吞不伪造，保可诊断；VECTOR_DB_INVALID_VECTOR 按
#      DR-1 裁决透传，不新增 EMBEDDING_INVALID_VECTOR）。
# =============================================================================

import re
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Mapping, Optional

from orchestration_platform.domain.errors import (
    VECTOR_DB_DIMENSION_MISMATCH,
    VECTOR_DB_INVALID_COLLECTION_ID,
    VECTOR_DB_RECORD_LIMIT_EXCEEDED,
    VECTOR_DB_STORAGE_UNAVAILABLE,
    VECTOR_KB_CHUNK_LIMIT_EXCEEDED,
    VECTOR_KB_CHUNK_NOT_FOUND,
    VECTOR_KB_DIMENSION_BINDING_CONFLICT,
    VECTOR_KB_DOCUMENT_NOT_FOUND,
    VECTOR_KB_DRAFT_VERSION_CONFLICT,
    VECTOR_KB_EMBEDDING_SOURCE_MISSING,
    VECTOR_KB_EMBEDDING_UNAVAILABLE,
    VECTOR_KB_EMPTY_CONTENT,
    VECTOR_KB_INVALID_CHUNK_OPERATION,
    VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID,
    VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND,
    VECTOR_KB_REAL_EMBEDDING_REQUIRED,
    VECTOR_KB_STORAGE_UNAVAILABLE,
    VECTOR_KB_UNKNOWN_INGESTION_JOB,
    VECTOR_KB_WRITE_TEXT_TOO_LARGE,
    DomainError,
    InstanceStateConflictError,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.node_configurations import (
    ModelLoadingConfig,
)
from orchestration_platform.integrations.local_vector_database.embedding import (
    PROVENANCE_HASH_FALLBACK,
    EmbeddingProvider,
    ExternalApiEmbeddingProvider,
    HashFallbackProvider,
    LocalRuntimeEmbeddingProvider,
    enforce_degraded_annotation,
)
from orchestration_platform.integrations.local_vector_database.models import (
    EMBEDDING_KIND_HASH,
    EMBEDDING_KIND_REAL,
    RecordFailure,
    VectorRecord,
)
from orchestration_platform.integrations.local_vector_database.service import (
    LocalVectorDatabaseService,
)
from orchestration_platform.integrations.vector_kb_management.annotations import (
    ANNOTATIONS_KEY,
    attach_annotations,
    normalize_annotations,
)
from orchestration_platform.integrations.vector_kb_management.chunkers import (
    DEFAULT_CHUNK_STRATEGY,
    build_chunker,
)
from orchestration_platform.integrations.vector_kb_management.config import (
    VectorKbManagementConfig,
    from_settings,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    CHUNK_TEXT_LIMIT,
    AnnotationLimits,
    ChunkDraft,
    ChunkDraftConfirmResponse,
    ChunkDraftResponse,
    ChunkDraftState,
    ChunkFailureView,
    ChunkOperation,
    ChunkRecord,
    ChunkStrategy,
    ChunkStrategyConfig,
    ChunkerOutcome,
    ImportDraftResponse,
    IngestionJob,
    IngestionJobStatus,
    IngestionReportReport,
    IngestionResult,
    KBEmbeddingBinding,
    KnowledgeBase,
    KnowledgeBaseDocument,
    TEXT_WRITE_FAILED_CAP,
    TextWriteOutcome,
)
from orchestration_platform.integrations.vector_kb_management.parsers import (
    ParserRegistry,
)
from orchestration_platform.integrations.vector_kb_management.repository import (
    IKnowledgeBaseRepository,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    VectorRetrievalError,
)

# 委托层错误码映射（design §2.3.3；其余 VECTOR_DB_* 原样透传）
_DELEGATE_ERROR_MAP: Dict[str, str] = {
    VECTOR_DB_INVALID_COLLECTION_ID: VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID,
    VECTOR_DB_DIMENSION_MISMATCH: VECTOR_KB_DIMENSION_BINDING_CONFLICT,
    VECTOR_DB_RECORD_LIMIT_EXCEEDED: VECTOR_KB_CHUNK_LIMIT_EXCEEDED,
    VECTOR_DB_STORAGE_UNAVAILABLE: VECTOR_KB_STORAGE_UNAVAILABLE,
}


def map_delegate_error(error: VectorRetrievalError) -> DomainError:
    """委托层异常分层映射（T4.3 / design §2.3.3）。

    - 4 码映射为 VECTOR_KB_*（固定文本 + 零凭据）；
    - 其余 VECTOR_DB_* 原样透传（错误码逐字保留，不吞不伪造，保可诊断）。
    """
    mapped = _DELEGATE_ERROR_MAP.get(error.error_code)
    if mapped is not None:
        return DomainError(
            mapped, f"向量存储委托失败：{error.error_message}"
        )
    return DomainError(error.error_code, error.error_message)


def _read_setting(name: str, default: object) -> object:
    """容错读取运行期配置（字段不存在/配置源不可用 → 缺省值）。"""
    try:
        from orchestration_platform.configuration.settings import get_settings

        return getattr(get_settings(), name, default)
    except Exception:
        return default


def _read_embedding_api_key() -> Optional[str]:
    """api 模式密钥仅来自运行期 settings（INV-D04；禁写入日志/错误）。"""
    value = _read_setting("vector_kb_search_api_key", None)
    if isinstance(value, str) and value:
        return value
    return None


def _default_provider_factory(
    binding: KBEmbeddingBinding, declared_dimension: Optional[int]
) -> Optional[EmbeddingProvider]:
    """默认向量化来源解析（api / local；T4.2 `_resolve_provider` 缺省实现）。

    - 集合已固化维度（declared_dimension 已知）→ 传入预校验（防半写后
      维度冲突，R-04 语义）；首写集合维度未知 → 取 lvd 缺省维度约定
      （settings.local_vector_database_default_dimension，默认 128）；
    - api：复用既有 mode=api 契约（base_url 必填；密钥仅运行期注入）；
    - local：sentence_transformers 延迟导入（构造零副作用，缺失在
      embed 阶段确定性暴露，由上层决定降级）；
    - 真实来源不可达/运行时缺失为沙箱不可验证项（NV-02/NV-03），
      逻辑层面经注入式 provider_factory seam 单测。
    """
    dimension = (
        declared_dimension
        if declared_dimension is not None
        else int(_read_setting("local_vector_database_default_dimension", 128))
    )
    try:
        if binding.mode == "api":
            if not binding.base_url:
                return None
            model_config = ModelLoadingConfig(
                mode="api",
                model_identifier=binding.model_identifier,
                base_url=binding.base_url,
                timeout_seconds=binding.timeout_seconds,
            )
            return ExternalApiEmbeddingProvider(
                model_config,
                api_key=_read_embedding_api_key(),
                dimension=dimension,
            )
        if binding.mode == "local":
            return LocalRuntimeEmbeddingProvider(
                binding.model_identifier,
                dimension=dimension,
                device=(binding.device or "cpu"),
            )
    except Exception:
        return None
    return None


def _annotations_enabled() -> bool:
    """标注能力总闸（默认 False；未启用 → 不产 annotations、零行为变化；INV-07/AC-61）。"""
    return bool(_read_setting("vector_kb_annotation_enabled", False))


def _annotation_limits() -> AnnotationLimits:
    """从运行期配置读取标注上界（容错读取，缺省 32/512；DC-07 单点）。"""
    return AnnotationLimits(
        max_items=int(_read_setting("vector_kb_annotation_max_items", 32)),
        max_value_length=int(
            _read_setting("vector_kb_annotation_max_value_length", 512)
        ),
    )


def _utc_now_iso() -> str:
    """当前 UTC 时刻的 ISO-8601 字符串（入库时间系统生成；DC-04）。"""
    return datetime.now(timezone.utc).isoformat()


def _inject_ingested_at(
    chunks: List[ChunkRecord], ingested_at: str
) -> List[ChunkRecord]:
    """把入库时间注入既有 ``annotations`` 子对象（不改既有顶层键）。

    - 仅当 ``source["annotations"]`` 已存在（Mapping）时写入 ``ingested_at``；
    - 存量旧草稿（无 annotations）保持原样 → 零破坏（AC-63）。
    """
    enriched: List[ChunkRecord] = []
    for chunk in chunks:
        source: Dict[str, JsonValue] = dict(chunk.source or {})
        annotations = source.get(ANNOTATIONS_KEY)
        if isinstance(annotations, Mapping):
            new_annotations = dict(annotations)
            new_annotations["ingested_at"] = ingested_at
            source[ANNOTATIONS_KEY] = new_annotations
            enriched.append(chunk.model_copy(update={"source": source}))
        else:
            enriched.append(chunk)
    return enriched


def _build_chunk_ids(
    document_id: str, drafts: List[ChunkDraft]
) -> List[ChunkRecord]:
    """chunk_id 确定性派生（T4.1：document_id + 1 起序号；≡ record_id）。

    - document_id 每次受理唯一生成（内含内容版本语义：重新导入 → 新
      document_id → 新 chunk_id 集，不与旧记录冲突）；
    - source 补充 document_id（检索命中可回溯来源文档）。
    """
    records: List[ChunkRecord] = []
    for draft in drafts:
        source: Dict[str, JsonValue] = dict(draft.source or {})
        source["document_id"] = document_id
        records.append(
            ChunkRecord(
                chunk_id=f"{document_id}:{draft.index + 1:04d}",
                index=draft.index,
                text=draft.text,
                source=source,
                vectorized=False,
                provenance=None,
                local_degraded=False,
            )
        )
    return records


def _with_status(job: IngestionJob, status: IngestionJobStatus) -> IngestionJob:
    """作业状态迁移载体（其余字段原样保留，DC-05 状态机）。"""
    return IngestionJob(
        job_id=job.job_id,
        knowledge_base_id=job.knowledge_base_id,
        document_id=job.document_id,
        status=status,
        result=job.result,
        provenance=job.provenance,
        local_degraded=job.local_degraded,
    )


def _next_version(current: str) -> str:
    """草稿版本推进（"v1"→"v2"；数值 +1，与仓储最新版本排序规则兼容）。"""
    match = re.fullmatch(r"v(\d+)", current)
    if match is not None:
        return f"v{int(match.group(1)) + 1}"
    return f"{current}_1"  # 防御：非 vN 形态时追加后缀保证单调


def _reject_hash_fallback_if_real_required(provider: EmbeddingProvider) -> None:
    """真实 embedding 强制模式写入闸（B2-C5 / DC-B2-05 ③，INV-L08 零放宽）。

    vector_real_embedding_enabled=True 且来源为 hash_fallback（哈希伪向量）
    → 以可辨识错误 VECTOR_KB_REAL_EMBEDDING_REQUIRED 拒绝写入（不伪造语义）；
    关闭（默认）→ 零干预，行为与现状逐字节等价（HC-07/GK-11）；
    真实来源（api/local_runtime/caller_supplied）不受影响。
    """
    if provider.provenance != PROVENANCE_HASH_FALLBACK:
        return
    enabled = False
    try:
        from orchestration_platform.configuration.settings import get_settings

        enabled = bool(
            getattr(get_settings(), "vector_real_embedding_enabled", False)
        )
    except Exception:
        enabled = False
    if enabled:
        raise DomainError(
            VECTOR_KB_REAL_EMBEDDING_REQUIRED,
            "真实 embedding 强制模式已启用（vector_real_embedding_enabled=True），"
            "但仅有 hash_fallback 确定性伪向量来源可用；已拒绝写入"
            "（不伪造语义，DC-B2-05 ③）",
        )


def _embedding_kind_for(provider: EmbeddingProvider) -> str:
    """按 provider provenance 落记录级代际（DC-B2-05 ④：随写入落盘）。"""
    if provider.provenance == PROVENANCE_HASH_FALLBACK:
        return EMBEDDING_KIND_HASH
    return EMBEDDING_KIND_REAL


def _build_vector_records(
    document_id: str,
    chunks: List[ChunkRecord],
    vectors: List[List[float]],
    provider: EmbeddingProvider,
    local_degraded: bool,
) -> List[VectorRecord]:
    """组装委托写入载荷（source 补充 document_id/index/provenance/降级标注）。"""
    vector_records: List[VectorRecord] = []
    for record, vector in zip(chunks, vectors):
        source: Dict[str, JsonValue] = dict(record.source or {})
        source["document_id"] = document_id
        source["index"] = record.index
        source["provenance"] = provider.provenance
        if local_degraded:
            source["local_degraded"] = True
        vector_records.append(
            VectorRecord(
                record_id=record.chunk_id,
                vector=vector,
                text=record.text,
                source=source,
                embedding_kind=_embedding_kind_for(provider),  # type: ignore[arg-type]
            )
        )
    return vector_records


class IngestionPipeline:
    """两段式导入流水线门面（CHG-07 / design §2.1.3 (a)）。

    - 受理 ingest_document：解析 → 分片 → 草稿 + 作业 pending
      （向量存储集合零写入，AC-38 / INV-D19）；
    - 确认 confirm_import：向量化 → 委托 upsert_records（幂等覆盖，
      INV-D20；失败不破坏，INV-D21）；
    - 注入式构造：repository / vector_service / config / provider_factory
      均可注入（测试绕开全局 settings 与真实 embedding 来源）。
    """

    def __init__(
        self,
        repository: IKnowledgeBaseRepository,
        vector_service: LocalVectorDatabaseService,
        config: Optional[VectorKbManagementConfig] = None,
        provider_factory: Optional[
            Callable[[KBEmbeddingBinding, Optional[int]], Optional[EmbeddingProvider]]
        ] = None,
    ) -> None:
        self._repository = repository
        self._vector_service = vector_service
        self._config = config if config is not None else from_settings()
        self._provider_factory = (
            provider_factory
            if provider_factory is not None
            else _default_provider_factory
        )
        self._parser = ParserRegistry(
            max_file_size_mb=self._config.max_file_size_mb
        )

    def _annotation_limits(self) -> AnnotationLimits:
        """从运行期配置读取标注上界（复用模块级单点；DC-07）。"""
        return _annotation_limits()

    # ------------------------------------------------------------------
    # 受理（解析 → 分片 → 草稿，零写存储）
    # ------------------------------------------------------------------

    def ingest_document(
        self,
        knowledge_base_id: str,
        file_name: str,
        data: bytes,
        strategy: Optional[ChunkStrategy] = None,
        max_chunk_length: Optional[int] = None,
        overlap: Optional[int] = None,
        similarity_threshold: Optional[float] = None,
        hierarchy: Optional[List[str]] = None,
        annotation_origin: Optional[str] = None,
        annotation_published_at: Optional[str] = None,
        annotation_custom: Optional[Mapping[str, JsonValue]] = None,
    ) -> ImportDraftResponse:
        """受理导入（AC-01/AC-03/AC-18）：产出草稿 + 作业 pending（AC-38）。

        分支确定性（design §2.1.3 (a) 活动图）：
        - 知识库不存在 → VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND；
          标识非法 → VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID（仓储双保险）；
        - 文件超限 / 格式不支持 / 解析器缺失 / 空内容 / 参数非法 /
          分片总量超上界 → 对应确定性错误码（经解析层/分片层抛出）；
        - semantic 无真实来源 → VECTOR_KB_EMBEDDING_SOURCE_MISSING
          （引导改用 recursive，INV-D08）。
        """
        knowledge_base = self._require_knowledge_base(knowledge_base_id)
        parsed = self._parser.parse(file_name, data)
        effective_strategy: ChunkStrategy = (
            strategy
            if strategy is not None
            else (
                knowledge_base.default_chunk_strategy
                if knowledge_base.default_chunk_strategy is not None
                else DEFAULT_CHUNK_STRATEGY
            )
        )
        strategy_config = ChunkStrategyConfig(
            strategy=effective_strategy,
            max_chunk_length=(
                max_chunk_length
                if max_chunk_length is not None
                else self._config.default_chunk_length
            ),
            overlap=(
                overlap if overlap is not None else self._config.default_overlap
            ),
            hierarchy=hierarchy,
            similarity_threshold=(
                similarity_threshold
                if similarity_threshold is not None
                else self._config.default_similarity_threshold
            ),
        )
        outcome = self._chunk_parsed_text(
            knowledge_base_id, knowledge_base, parsed.text, strategy_config
        )
        document_id = f"doc_{uuid.uuid4().hex[:8]}"
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        records = _build_chunk_ids(document_id, outcome.chunks)
        if len(records) > self._config.max_chunks_per_kb:
            raise WorkflowValidationError(
                VECTOR_KB_CHUNK_LIMIT_EXCEEDED,
                f"分片数量超过单知识库上界：{len(records)} > "
                f"{self._config.max_chunks_per_kb}"
                "（可经 vector_kb_management_max_chunks_per_kb 调整）",
            )
        # vkb_doc_annotation（CHG-07 / DC-12 / OP-10）：受理期标注装配 +
        # 后台渠道来源必填校验（缺失 → 受理即确定性拒绝，零草稿/零作业/零写入）；
        # 总闸未启用 → 不产 annotations（既有行为零变化，INV-07/AC-61）。
        if _annotations_enabled():
            annotations = normalize_annotations(
                channel="backend",
                origin=annotation_origin,
                published_at=annotation_published_at,
                custom=annotation_custom,
                ingested_at=None,
                limits=self._annotation_limits(),
            )
            records = attach_annotations(
                records, lambda _record: dict(annotations)
            )
        document = KnowledgeBaseDocument(
            document_id=document_id,
            file_name=file_name,
            format=parsed.format,
            parse_status="parsed",
            chunk_strategy=strategy_config,
        )
        self._repository.save_document(
            knowledge_base_id, document, text=parsed.text
        )
        self._repository.save_draft(
            ChunkDraftState(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                draft_version="v1",
                base_version="",
                chunks=records,
                notices=outcome.notices,
                strategy_effective=outcome.strategy_effective,
            )
        )
        self._repository.save_job(
            IngestionJob(
                job_id=job_id,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                status="pending",
            )
        )
        return ImportDraftResponse(
            job_id=job_id,
            document_id=document_id,
            parse_status="parsed",
            chunk_count=len(records),
            chunks=[
                ChunkDraft(
                    chunk_id=record.chunk_id,
                    index=record.index,
                    text=record.text,
                    source=record.source,
                )
                for record in records
            ],
            notices=outcome.notices,
            strategy_effective=outcome.strategy_effective,
        )

    # ------------------------------------------------------------------
    # 确认（向量化 → 委托写入；幂等；失败不破坏）
    # ------------------------------------------------------------------

    def confirm_import(
        self,
        knowledge_base_id: str,
        document_id: str,
        job_id: str,
        snapshot_version: Optional[str] = None,
        allow_degraded_fallback: bool = False,
    ) -> IngestionReportReport:
        """确认导入（AC-06/AC-07）：向量化 + 委托 upsert_records。

        - 乐观并发：snapshot_version 与当前草稿版本不符 →
          VECTOR_KB_DRAFT_VERSION_CONFLICT（409）；
        - 无真实来源且未开兜底 → VECTOR_KB_EMBEDDING_UNAVAILABLE
          （作业落 failed，保留旧数据不半写入，可重确认，INV-D21）；
        - 开兜底（绑定或请求显式）→ 哈希兜底 + local_degraded=True +
          原因（伪语义，不对外充真实，INV-D08）；
        - 写入失败条目 → partial（失败明细原样上报，AC-09）；
        - record_id=chunk_id 幂等：重复确认覆盖更新不新增重复（INV-D20）。
        """
        started = time.perf_counter()
        knowledge_base = self._require_knowledge_base(knowledge_base_id)
        job = self._require_job(job_id, knowledge_base_id, document_id)
        draft = self._repository.get_draft(document_id)
        if draft is None:
            raise ResourceNotFoundError(
                VECTOR_KB_DOCUMENT_NOT_FOUND,
                f"导入草稿缺失，无法确认导入：{document_id}",
            )
        if (
            snapshot_version is not None
            and snapshot_version != draft.draft_version
        ):
            raise InstanceStateConflictError(
                VECTOR_KB_DRAFT_VERSION_CONFLICT,
                f"草稿版本冲突：请求 {snapshot_version}，"
                f"当前 {draft.draft_version}（草稿可能已被修改，请刷新后重试）",
            )
        binding = knowledge_base.embedding_model
        fallback_allowed = bool(
            binding.allow_degraded_fallback or allow_degraded_fallback
        )
        self._repository.save_job(_with_status(job, "running"))
        provider: Optional[EmbeddingProvider] = None
        local_degraded = False
        local_degraded_reason: Optional[str] = None
        vectors: List[List[float]] = []
        try:
            provider = self._resolve_provider(knowledge_base_id, binding)
            if provider is None:
                raise RuntimeError("无法解析向量化来源（绑定模式或参数缺失）")
            vectors = [provider.embed(record.text) for record in draft.chunks]
        except DomainError:
            raise
        except Exception:
            if not fallback_allowed:
                self._repository.save_job(_with_status(job, "failed"))
                raise DomainError(
                    VECTOR_KB_EMBEDDING_UNAVAILABLE,
                    "向量化失败（来源不可达或运行时缺失）；已保留既有数据，"
                    "未产生半写入（作业可重确认）",
                ) from None
            provider = HashFallbackProvider()
            try:
                _reject_hash_fallback_if_real_required(provider)
            except DomainError:
                # 真实 embedding 强制模式拒绝：与既有失败路径同语义——
                # 先落作业 failed 再抛出（作业不悬挂，可重确认）
                self._repository.save_job(_with_status(job, "failed"))
                raise
            local_degraded = True
            local_degraded_reason = (
                "真实 embedding 来源不可用，已按绑定声明开启哈希兜底降级"
                "（确定性伪向量，伪语义结果不对外充真实语义）"
            )
            vectors = [provider.embed(record.text) for record in draft.chunks]
        # 出口守护（INV-D08）：hash_fallback 必须带降级标注 + 原因
        enforce_degraded_annotation(
            provider.provenance, local_degraded, local_degraded_reason
        )
        # vkb_doc_annotation（CHG-07 / DC-04）：确认期注入入库时间（最近写入时刻，
        # 系统生成 ISO-8601）；存量无 annotations 的草稿保持原样（零破坏，AC-63）。
        enriched_chunks = _inject_ingested_at(draft.chunks, _utc_now_iso())
        vector_records = _build_vector_records(
            document_id, enriched_chunks, vectors, provider, local_degraded
        )
        try:
            upsert_report = self._vector_service.upsert_records(
                knowledge_base_id, vector_records
            )
        except VectorRetrievalError as error:
            self._repository.save_job(_with_status(job, "failed"))
            raise map_delegate_error(error) from None
        duration_ms = int((time.perf_counter() - started) * 1000)
        status = "partial" if upsert_report.failed else "succeeded"
        if not upsert_report.failed:
            # 生效基线回填（B5）：干净确认后以当前草稿为生效快照；
            # partial 不回填（失败条目未落库，后续确认按无基线全量重算）
            self._repository.set_confirmed_version(
                document_id, draft.draft_version
            )
        result = IngestionResult(
            chunk_count=len(draft.chunks),
            upserted_count=upsert_report.upserted_count,
            failed=upsert_report.failed,
            local_degraded_reason=local_degraded_reason,
            duration_ms=duration_ms,
        )
        self._repository.save_job(
            IngestionJob(
                job_id=job_id,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                status=status,
                result=result,
                provenance=provider.provenance,
                local_degraded=local_degraded,
            )
        )
        return IngestionReportReport(
            status=status,
            chunk_count=len(draft.chunks),
            upserted_count=upsert_report.upserted_count,
            failed=[
                ChunkFailureView(
                    chunk_id=failure.record_id, reason=failure.reason
                )
                for failure in upsert_report.failed
            ],
            provenance=provider.provenance,
            local_degraded=local_degraded,
            local_degraded_reason=local_degraded_reason,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # 分片编辑（合并/拆分/编辑 → 草稿；确认 → 仅重算受影响分片）
    # ------------------------------------------------------------------

    def apply_chunk_draft(
        self,
        knowledge_base_id: str,
        document_id: str,
        base_version: str,
        operations: List[ChunkOperation],
    ) -> ChunkDraftResponse:
        """提交调整草稿（AC-32/AC-33/AC-34/AC-35）：产草稿不生效（AC-38）。

        - 合并：≥2 个**相邻**分片，文本按确定性规则拼接（"\\n\\n"）；
        - 拆分：split_points 严格递增且落于 (0, len(text))，子段合计一致；
        - 编辑：text 非空（去空白）且 ≤ 2000；
        - 乐观并发：base_version 匹配当前草稿版本，否则
          VECTOR_KB_DRAFT_VERSION_CONFLICT（409）；
        - 非法操作 → VECTOR_KB_INVALID_CHUNK_OPERATION（422，AC-35 不得
          静默保存非法分片）；分片不存在 → VECTOR_KB_CHUNK_NOT_FOUND（404）；
        - 检索数据零变化（存储集合未写入，AC-38 / INV-D19）。
        """
        self._require_knowledge_base(knowledge_base_id)
        self._require_document(knowledge_base_id, document_id)
        if not operations:
            raise WorkflowValidationError(
                VECTOR_KB_INVALID_CHUNK_OPERATION,
                "调整指令列表为空（至少一条 merge/split/edit 指令）",
            )
        draft = self._repository.get_draft(document_id)
        if draft is None:
            raise ResourceNotFoundError(
                VECTOR_KB_DOCUMENT_NOT_FOUND,
                f"文档草稿缺失，无法调整分片：{document_id}",
            )
        if base_version != draft.draft_version:
            raise InstanceStateConflictError(
                VECTOR_KB_DRAFT_VERSION_CONFLICT,
                f"草稿版本冲突：请求 {base_version}，当前 {draft.draft_version}"
                "（草稿可能已被修改，请刷新后重试）",
            )
        new_chunks = self._apply_operations(draft.chunks, operations)
        if len(new_chunks) > self._config.max_chunks_per_kb:
            raise WorkflowValidationError(
                VECTOR_KB_CHUNK_LIMIT_EXCEEDED,
                f"调整后分片数量超过单知识库上界：{len(new_chunks)} > "
                f"{self._config.max_chunks_per_kb}",
            )
        new_draft = ChunkDraftState(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            draft_version=_next_version(draft.draft_version),
            base_version=draft.draft_version,
            chunks=new_chunks,
            notices=[],
            strategy_effective=draft.strategy_effective,
        )
        self._repository.save_draft(new_draft)
        return ChunkDraftResponse(
            draft_version=new_draft.draft_version,
            chunks=[
                ChunkDraft(
                    chunk_id=record.chunk_id,
                    index=record.index,
                    text=record.text,
                    source=record.source,
                )
                for record in new_chunks
            ],
            notices=[],
        )

    def confirm_chunk_draft(
        self,
        knowledge_base_id: str,
        document_id: str,
        draft_version: str,
    ) -> ChunkDraftConfirmResponse:
        """确认调整（AC-36/AC-37）：仅重算受影响分片 + 幂等更新/删除。

        - 受影响集 = 相对**生效基线**（document.confirmed_version 指向的
          草稿快照；None → 空基线）的差集：文本变化/新增 → 重算写入；
          消失 → 删除（record_id 幂等键，INV-D20）；
        - 多轮未确认编辑跨版本累积：基线为最近成功确认快照，链上全部
          变更一次生效（最后一次确认为准，DC-05）；
        - 重算失败（无兜底）→ **不删旧记录**（INV-D21），返回
          failed_at + reason（T5.2）；无来源（无兜底）→
          VECTOR_KB_EMBEDDING_UNAVAILABLE（对齐 T4 语义）；
        - 兜底（绑定声明）→ 哈希兜底 + local_degraded=True + 原因
          （伪语义标注，INV-D08）；
        - 写入部分失败 → 不执行删除、不推进基线（可重确认重试，
          INV-D21）；
        - 幂等：重复确认同一版本 → 基线已一致 → 空变更成功（INV-D20）。
        """
        knowledge_base = self._require_knowledge_base(knowledge_base_id)
        document = self._require_document(knowledge_base_id, document_id)
        latest = self._repository.get_draft(document_id)
        if latest is None:
            raise ResourceNotFoundError(
                VECTOR_KB_DOCUMENT_NOT_FOUND,
                f"文档草稿缺失，无法确认调整：{document_id}",
            )
        draft = self._repository.get_draft(document_id, draft_version)
        if draft is None:
            raise InstanceStateConflictError(
                VECTOR_KB_DRAFT_VERSION_CONFLICT,
                f"草稿版本冲突：请求 {draft_version}，"
                f"当前 {latest.draft_version}（草稿可能已被修改，请刷新后重试）",
            )
        baseline_chunks: List[ChunkRecord] = []
        if document.confirmed_version:
            baseline = self._repository.get_draft(
                document_id, document.confirmed_version
            )
            if baseline is not None:
                baseline_chunks = baseline.chunks
        baseline_texts = {
            record.chunk_id: record.text for record in baseline_chunks
        }
        draft_texts = {record.chunk_id: record.text for record in draft.chunks}
        deleted = [
            record.chunk_id
            for record in baseline_chunks
            if record.chunk_id not in draft_texts
        ]
        rewritten_records = [
            record
            for record in draft.chunks
            if record.chunk_id not in baseline_texts
            or baseline_texts[record.chunk_id] != record.text
        ]
        rewritten_ids = [record.chunk_id for record in rewritten_records]
        if not rewritten_records and not deleted:
            # 幂等重确认：无受影响分片 → 仅推进基线指针（不产生重复）
            self._repository.set_confirmed_version(
                document_id, draft_version
            )
            return ChunkDraftConfirmResponse(status="succeeded")
        binding = knowledge_base.embedding_model
        fallback_allowed = bool(binding.allow_degraded_fallback)
        local_degraded = False
        local_degraded_reason: Optional[str] = None
        if rewritten_records:
            provider = self._resolve_provider(knowledge_base_id, binding)
            vectors: List[List[float]] = []
            if provider is None:
                if not fallback_allowed:
                    raise DomainError(
                        VECTOR_KB_EMBEDDING_UNAVAILABLE,
                        "无可用向量化来源（绑定模式或参数缺失且未开启兜底）；"
                        "已保留旧记录，未产生半写入",
                    )
                provider = HashFallbackProvider()
                _reject_hash_fallback_if_real_required(provider)
                local_degraded = True
                local_degraded_reason = (
                    "真实 embedding 来源不可用，已按绑定声明开启哈希兜底降级"
                    "（确定性伪向量，伪语义结果不对外充真实语义）"
                )
            try:
                vectors = [
                    provider.embed(record.text)
                    for record in rewritten_records
                ]
            except Exception:
                if not fallback_allowed:
                    # 重算失败：不删旧记录（INV-D21），返回 failed_at + reason
                    return ChunkDraftConfirmResponse(
                        status="failed",
                        rewritten=[],
                        deleted=[],
                        failed_at=",".join(rewritten_ids),
                        reason="重算失败（向量化来源不可达或运行时缺失）；"
                        "已保留旧记录，未删除任何分片（可重确认）",
                        local_degraded=False,
                        local_degraded_reason=None,
                    )
                provider = HashFallbackProvider()
                _reject_hash_fallback_if_real_required(provider)
                local_degraded = True
                local_degraded_reason = (
                    "真实 embedding 来源不可用，已按绑定声明开启哈希兜底降级"
                    "（确定性伪向量，伪语义结果不对外充真实语义）"
                )
                vectors = [
                    provider.embed(record.text)
                    for record in rewritten_records
                ]
            # 出口守护（INV-D08）：hash_fallback 必须带降级标注 + 原因
            enforce_degraded_annotation(
                provider.provenance, local_degraded, local_degraded_reason
            )
            vector_records = _build_vector_records(
                document_id, rewritten_records, vectors, provider,
                local_degraded,
            )
            try:
                upsert_report = self._vector_service.upsert_records(
                    knowledge_base_id, vector_records
                )
            except VectorRetrievalError as error:
                raise map_delegate_error(error) from None
            if upsert_report.failed:
                # 部分失败：不执行删除、不推进基线（保留旧记录，可重确认重试）
                failed_ids = {
                    failure.record_id for failure in upsert_report.failed
                }
                return ChunkDraftConfirmResponse(
                    status="partial",
                    rewritten=[
                        chunk_id
                        for chunk_id in rewritten_ids
                        if chunk_id not in failed_ids
                    ],
                    deleted=[],
                    failed_at=",".join(sorted(failed_ids)),
                    reason=f"部分分片写入失败：{upsert_report.failed[0].reason}"
                    "（已保留旧记录，未执行删除；可重确认重试）",
                    local_degraded=local_degraded,
                    local_degraded_reason=local_degraded_reason,
                )
        if deleted:
            try:
                self._vector_service.delete_records(
                    knowledge_base_id, deleted
                )
            except VectorRetrievalError as error:
                raise map_delegate_error(error) from None
        self._repository.set_confirmed_version(document_id, draft_version)
        return ChunkDraftConfirmResponse(
            status="succeeded",
            rewritten=rewritten_ids,
            deleted=deleted,
            local_degraded=local_degraded,
            local_degraded_reason=local_degraded_reason,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _require_knowledge_base(
        self, knowledge_base_id: str
    ) -> KnowledgeBase:
        """知识库存在性前置（缺失 → 404；标识非法 → 422，双保险）。"""
        knowledge_base = self._repository.get(knowledge_base_id)
        if knowledge_base is None:
            raise ResourceNotFoundError(
                VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND,
                f"知识库不存在：{knowledge_base_id}",
            )
        return knowledge_base

    def _require_document(
        self, knowledge_base_id: str, document_id: str
    ) -> KnowledgeBaseDocument:
        """文档存在性与归属前置（缺失/归属不符 → 404 DOCUMENT_NOT_FOUND）。"""
        document = self._repository.get_document(document_id)
        if (
            document is None
            or (
                document.knowledge_base_id is not None
                and document.knowledge_base_id != knowledge_base_id
            )
        ):
            raise ResourceNotFoundError(
                VECTOR_KB_DOCUMENT_NOT_FOUND,
                f"文档不存在或不属于该知识库：{document_id}",
            )
        return document

    def _apply_operations(
        self, chunks: List[ChunkRecord], operations: List[ChunkOperation]
    ) -> List[ChunkRecord]:
        """顺序应用调整指令（在草稿快照副本上变形；操作后索引重编号）。"""
        working: List[ChunkRecord] = [
            chunk.model_copy(deep=True) for chunk in chunks
        ]
        for operation in operations:
            if operation.op == "merge":
                working = self._apply_merge(working, operation)
            elif operation.op == "split":
                working = self._apply_split(working, operation)
            else:
                working = self._apply_edit(working, operation)
        return [
            chunk.model_copy(update={"index": position})
            for position, chunk in enumerate(working)
        ]

    def _apply_merge(
        self, working: List[ChunkRecord], operation: ChunkOperation
    ) -> List[ChunkRecord]:
        """合并相邻分片（确定性拼接 "\\n\\n"；结果 ≤ 2000，AC-32）。"""
        chunk_ids = operation.chunk_ids or []
        if len(chunk_ids) < 2:
            raise WorkflowValidationError(
                VECTOR_KB_INVALID_CHUNK_OPERATION,
                "合并指令至少需要 2 个分片标识",
            )
        if len(set(chunk_ids)) != len(chunk_ids):
            raise WorkflowValidationError(
                VECTOR_KB_INVALID_CHUNK_OPERATION,
                "合并指令存在重复分片标识",
            )
        positions = self._chunk_positions(working)
        for chunk_id in chunk_ids:
            if chunk_id not in positions:
                raise ResourceNotFoundError(
                    VECTOR_KB_CHUNK_NOT_FOUND,
                    f"待合并分片不存在：{chunk_id}",
                )
        indexes = sorted(
            positions[chunk_id] for chunk_id in chunk_ids
        )
        if indexes != list(
            range(indexes[0], indexes[0] + len(indexes))
        ):
            raise WorkflowValidationError(
                VECTOR_KB_INVALID_CHUNK_OPERATION,
                "仅允许合并相邻分片（目标分片在文档序列中不连续）",
            )
        merged_text = "\n\n".join(
            working[position].text for position in indexes
        )
        if len(merged_text) > CHUNK_TEXT_LIMIT:
            raise WorkflowValidationError(
                VECTOR_KB_INVALID_CHUNK_OPERATION,
                f"合并后分片超长：{len(merged_text)} > {CHUNK_TEXT_LIMIT}"
                "（请先拆分或缩减相邻分片）",
            )
        merged = working[indexes[0]].model_copy(
            update={"text": merged_text}
        )
        result: List[ChunkRecord] = []
        removed_positions = set(indexes)
        for position, chunk in enumerate(working):
            if position == indexes[0]:
                result.append(merged)
            elif position not in removed_positions:
                result.append(chunk)
        return result

    def _apply_split(
        self, working: List[ChunkRecord], operation: ChunkOperation
    ) -> List[ChunkRecord]:
        """按字符偏移拆分（严格递增且落于 (0, len(text))，AC-33）。"""
        chunk_id = operation.chunk_id or ""
        points = operation.split_points or []
        positions = self._chunk_positions(working)
        if chunk_id not in positions:
            raise ResourceNotFoundError(
                VECTOR_KB_CHUNK_NOT_FOUND,
                f"待拆分分片不存在：{chunk_id}",
            )
        if not points:
            raise WorkflowValidationError(
                VECTOR_KB_INVALID_CHUNK_OPERATION,
                "拆分指令缺少切分点（split_points 不能为空）",
            )
        text = working[positions[chunk_id]].text
        for point in points:
            if point <= 0 or point >= len(text):
                raise WorkflowValidationError(
                    VECTOR_KB_INVALID_CHUNK_OPERATION,
                    f"非法拆分点：{point}（须严格落于 (0, {len(text)}) 内）",
                )
        if any(
            points[index] >= points[index + 1]
            for index in range(len(points) - 1)
        ):
            raise WorkflowValidationError(
                VECTOR_KB_INVALID_CHUNK_OPERATION,
                "拆分点必须严格递增（不得重复或回退）",
            )
        pieces: List[str] = []
        start = 0
        for point in points:
            pieces.append(text[start:point])
            start = point
        pieces.append(text[start:])
        position = positions[chunk_id]
        original = working[position]
        split_chunks = [
            original.model_copy(
                update={
                    "chunk_id": f"{original.chunk_id}.{sequence}",
                    "text": piece,
                }
            )
            for sequence, piece in enumerate(pieces, start=1)
        ]
        return working[:position] + split_chunks + working[position + 1 :]

    def _apply_edit(
        self, working: List[ChunkRecord], operation: ChunkOperation
    ) -> List[ChunkRecord]:
        """编辑分片文本（非空去空白且 ≤ 2000，AC-34/AC-35）。"""
        chunk_id = operation.chunk_id or ""
        positions = self._chunk_positions(working)
        if chunk_id not in positions:
            raise ResourceNotFoundError(
                VECTOR_KB_CHUNK_NOT_FOUND,
                f"待编辑分片不存在：{chunk_id}",
            )
        text = operation.text
        if text is None or not text.strip():
            raise WorkflowValidationError(
                VECTOR_KB_INVALID_CHUNK_OPERATION,
                "编辑文本不能为空（AC-35：不得静默保存非法分片）",
            )
        text = text.strip()
        if len(text) > CHUNK_TEXT_LIMIT:
            raise WorkflowValidationError(
                VECTOR_KB_INVALID_CHUNK_OPERATION,
                f"编辑文本超长：{len(text)} > {CHUNK_TEXT_LIMIT}",
            )
        position = positions[chunk_id]
        working[position] = working[position].model_copy(
            update={"text": text}
        )
        return working

    @staticmethod
    def _chunk_positions(chunks: List[ChunkRecord]) -> Dict[str, int]:
        """chunk_id → 文档内位置映射（编辑操作定位用）。"""
        return {
            chunk.chunk_id: position
            for position, chunk in enumerate(chunks)
        }

    def _require_job(
        self, job_id: str, knowledge_base_id: str, document_id: str
    ) -> IngestionJob:
        """作业存在性与归属前置（未知/归属不符 → 404 UNKNOWN_INGESTION_JOB）。"""
        job = self._repository.get_job(job_id)
        if (
            job is None
            or job.knowledge_base_id != knowledge_base_id
            or job.document_id != document_id
        ):
            raise ResourceNotFoundError(
                VECTOR_KB_UNKNOWN_INGESTION_JOB,
                f"摄取作业不存在或不属于该知识库/文档：{job_id}",
            )
        return job

    def _resolve_provider(
        self, knowledge_base_id: str, binding: KBEmbeddingBinding
    ) -> Optional[EmbeddingProvider]:
        """向量化来源解析（T4.2）：集合已固化维度时传入预校验（防半写后冲突）。"""
        declared_dimension: Optional[int] = None
        try:
            declared_dimension = self._vector_service.diagnose(
                knowledge_base_id
            ).declared_dimension
        except Exception:
            declared_dimension = None  # diagnose 只读不抛；防御兜底
        return self._provider_factory(binding, declared_dimension)

    def _chunk_parsed_text(
        self,
        knowledge_base_id: str,
        knowledge_base: KnowledgeBase,
        text: str,
        strategy_config: ChunkStrategyConfig,
    ) -> ChunkerOutcome:
        """分片分派（semantic 经注入式来源；失败确定性归一 EMBEDDING_SOURCE_MISSING）。"""
        if strategy_config.strategy == "semantic":
            provider = self._resolve_provider(
                knowledge_base_id, knowledge_base.embedding_model
            )
            chunker = build_chunker(
                "semantic",
                provider=provider,
                default_similarity_threshold=self._config.default_similarity_threshold,
            )
            try:
                return chunker.chunk_detailed(text, strategy_config)
            except DomainError:
                raise
            except Exception:
                raise WorkflowValidationError(
                    VECTOR_KB_EMBEDDING_SOURCE_MISSING,
                    "语义分块需真实 embedding 来源（解析或调用失败）；"
                    "建议改用 recursive 策略",
                ) from None
        chunker = build_chunker(strategy_config.strategy)
        return chunker.chunk_detailed(text, strategy_config)


# =============================================================================
# vkb_write_mode：即时写入服务门面（TextWriter，唯一写入调用点）
# =============================================================================

# 写入文本长度上界缺省（P-1 默认裁决；经 settings.vector_kb_write_max_text_length
# 覆盖，缺省 500000 字符 ≈500 KB 字符量级）
DEFAULT_WRITE_MAX_TEXT_LENGTH = 500000


class TextWriter:
    """即时写入服务门面（vkb_write_mode / design §2.2.2(3)）。

    工作流运行时节点级"文本 → 分片 → 向量化 → 写入"的即时单段门面
    （无草稿 / 无作业 / 无人工确认），是**节点侧唯一写入调用点**（OP-11）。

    - 构造零副作用、注入式：config / vector_service / provider_factory 可注入
      （测试绕开全局 settings 与真实 embedding 来源）；
    - 单点复用（INV-17）：分片经既有 ``build_chunker`` / ``chunk_detailed``，
      写入载荷经既有 ``_build_chunk_ids`` / ``_build_vector_records``，
      委托经既有 ``LocalVectorDatabaseService.upsert_records``，provider 经既有
      ``_default_provider_factory``，错误经既有 ``map_delegate_error``；
      **不复制任何分片/写入逻辑**，**不改** ``IngestionPipeline`` 及上述私有函数
      签名与语义。
    """

    def __init__(
        self,
        config: Optional[VectorKbManagementConfig] = None,
        vector_service: Optional[LocalVectorDatabaseService] = None,
        provider_factory: Optional[
            Callable[[KBEmbeddingBinding, Optional[int]], Optional[EmbeddingProvider]]
        ] = None,
    ) -> None:
        self._config = config if config is not None else from_settings()
        self._vector_service = (
            vector_service
            if vector_service is not None
            else LocalVectorDatabaseService()
        )
        self._provider_factory = (
            provider_factory
            if provider_factory is not None
            else _default_provider_factory
        )

    def write_text(
        self,
        knowledge_base_id: str,
        binding: KBEmbeddingBinding,
        text: str,
        document_id: str,
        strategy: Optional[ChunkStrategy] = None,
        max_chunk_length: Optional[int] = None,
        overlap: Optional[int] = None,
        similarity_threshold: Optional[float] = None,
        hierarchy: Optional[List[str]] = None,
        allow_degraded_fallback: Optional[bool] = None,
        annotation_origin: Optional[str] = None,
        annotation_published_at: Optional[str] = None,
        annotation_custom: Optional[Mapping[str, JsonValue]] = None,
    ) -> TextWriteOutcome:
        """即时写入（design §2.2.2(3)）：文本 → 分片 → 向量化 → upsert_records。

        - 前置：text 非空且未超上界（否则 VECTOR_KB_EMPTY_CONTENT /
          VECTOR_KB_WRITE_TEXT_TOO_LARGE，集合零写入）；document_id 非空
          （由调用方保证，幂等键派生基础）；
        - 分片参数三级优先级：``strategy``（调用方解析的节点显式 ?? 知识库默认）
          ?? 服务默认 ``DEFAULT_CHUNK_STRATEGY``；参数经既有
          ``validate_chunk_params`` 在 chunker 内前置拦截；
        - 无真实来源且未开兜底 → VECTOR_KB_EMBEDDING_UNAVAILABLE（集合零写入，
          不半写）；开兜底 → 哈希兜底 + local_degraded=True + 原因（INV-D08），
          出口经 ``enforce_degraded_annotation`` 守护；
        - 委托失败条目 → status=partial（逐条失败不整体失败，AC-44）；
        - 幂等：document_id 稳定 → record_id=chunk_id 同键覆盖不膨胀（AC-34/35）。
        """
        started = time.perf_counter()
        # ① 文本非空 + 长度上界（P-1：settings.vector_kb_write_max_text_length）
        if text is None or not text.strip():
            raise WorkflowValidationError(
                VECTOR_KB_EMPTY_CONTENT,
                "待写入文本为空（纯空白），无法写入",
            )
        max_text_length = int(
            _read_setting(
                "vector_kb_write_max_text_length", DEFAULT_WRITE_MAX_TEXT_LENGTH
            )
        )
        if len(text) > max_text_length:
            raise WorkflowValidationError(
                VECTOR_KB_WRITE_TEXT_TOO_LARGE,
                f"待写入文本超上界：{len(text)} > {max_text_length}"
                "（可经 vector_kb_write_max_text_length 调整）",
            )
        # ② 分片策略与参数解析（三级优先级；参数经 chunker 内 validate_chunk_params 前置拦截）
        effective_strategy: ChunkStrategy = (
            strategy if strategy is not None else DEFAULT_CHUNK_STRATEGY
        )
        strategy_config = ChunkStrategyConfig(
            strategy=effective_strategy,
            max_chunk_length=(
                max_chunk_length
                if max_chunk_length is not None
                else self._config.default_chunk_length
            ),
            overlap=(
                overlap if overlap is not None else self._config.default_overlap
            ),
            hierarchy=hierarchy,
            similarity_threshold=(
                similarity_threshold
                if similarity_threshold is not None
                else self._config.default_similarity_threshold
            ),
        )
        # ③ 分片（复用 build_chunker + chunk_detailed；semantic 经 provider 注入）
        provider = self._resolve_provider(knowledge_base_id, binding)
        if effective_strategy == "semantic":
            chunker = build_chunker(
                "semantic",
                provider=provider,
                default_similarity_threshold=self._config.default_similarity_threshold,
            )
            try:
                outcome = chunker.chunk_detailed(text, strategy_config)
            except DomainError:
                raise
            except Exception:
                raise WorkflowValidationError(
                    VECTOR_KB_EMBEDDING_SOURCE_MISSING,
                    "语义分块需真实 embedding 来源（解析或调用失败）；"
                    "建议改用 recursive 策略",
                ) from None
        else:
            chunker = build_chunker(effective_strategy)
            outcome = chunker.chunk_detailed(text, strategy_config)
        records = _build_chunk_ids(document_id, outcome.chunks)
        # ④ provider 解析与逐片段向量化 + 降级守护
        fallback_allowed = bool(
            binding.allow_degraded_fallback or allow_degraded_fallback
        )
        local_degraded = False
        local_degraded_reason: Optional[str] = None
        vectors: List[List[float]] = []
        if provider is None:
            if not fallback_allowed:
                raise DomainError(
                    VECTOR_KB_EMBEDDING_UNAVAILABLE,
                    "无可用向量化来源（绑定模式或参数缺失且未开启兜底）；"
                    "未产生半写入",
                )
            provider = HashFallbackProvider()
            _reject_hash_fallback_if_real_required(provider)
            local_degraded = True
            local_degraded_reason = (
                "真实 embedding 来源不可用，已开启哈希兜底降级"
                "（确定性伪向量，伪语义结果不对外充真实语义）"
            )
        try:
            vectors = [provider.embed(record.text) for record in records]
        except DomainError:
            raise
        except Exception:
            if not fallback_allowed:
                raise DomainError(
                    VECTOR_KB_EMBEDDING_UNAVAILABLE,
                    "向量化失败（来源不可达或运行时缺失）；未产生半写入",
                ) from None
            provider = HashFallbackProvider()
            _reject_hash_fallback_if_real_required(provider)
            local_degraded = True
            local_degraded_reason = (
                "真实 embedding 来源不可用，已开启哈希兜底降级"
                "（确定性伪向量，伪语义结果不对外充真实语义）"
            )
            vectors = [provider.embed(record.text) for record in records]
        # 出口守护（INV-D08）：hash_fallback 未标注 → 一致性异常
        enforce_degraded_annotation(
            provider.provenance, local_degraded, local_degraded_reason
        )
        # ⑤ 标注装配（vkb_doc_annotation / CHG-07 / DC-06 / DC-04）：节点渠道来源
        # 缺省"工作流写入"（显式覆盖优先），入库时间=写入时刻；幂等键不含标注（INV-11）。
        # 总闸未启用 → 不产 annotations（既有行为零变化，INV-07/AC-61）。
        if _annotations_enabled():
            annotations = normalize_annotations(
                channel="workflow",
                origin=annotation_origin,
                published_at=annotation_published_at,
                custom=annotation_custom,
                ingested_at=_utc_now_iso(),
                limits=_annotation_limits(),
            )
            records = attach_annotations(
                records, lambda _record: dict(annotations)
            )
        # ⑥ 写入载荷组装（复用 _build_vector_records；source 零凭据）
        vector_records = _build_vector_records(
            document_id, records, vectors, provider, local_degraded
        )
        # ⑥ 委托写入（复用 upsert_records + map_delegate_error；唯一写入口）
        try:
            upsert_report = self._vector_service.upsert_records(
                knowledge_base_id, vector_records
            )
        except VectorRetrievalError as error:
            raise map_delegate_error(error) from None
        # ⑦ 组装报告（失败清单按 P-3 上界截断并标注；成功计数不失真）
        duration_ms = int((time.perf_counter() - started) * 1000)
        failed_all = list(upsert_report.failed)
        failed_truncated = len(failed_all) > TEXT_WRITE_FAILED_CAP
        status = "partial" if failed_all else "succeeded"
        return TextWriteOutcome(
            status=status,
            chunk_count=len(records),
            upserted_count=upsert_report.upserted_count,
            failed=failed_all[:TEXT_WRITE_FAILED_CAP],
            provenance=provider.provenance,
            local_degraded=local_degraded,
            local_degraded_reason=local_degraded_reason,
            duration_ms=duration_ms,
            document_id=document_id,
            failed_truncated=failed_truncated,
        )

    def _resolve_provider(
        self, knowledge_base_id: str, binding: KBEmbeddingBinding
    ) -> Optional[EmbeddingProvider]:
        """向量化来源解析（复用模块级 _default_provider_factory；diagnose 预校验维度）。"""
        declared_dimension: Optional[int] = None
        try:
            declared_dimension = self._vector_service.diagnose(
                knowledge_base_id
            ).declared_dimension
        except Exception:
            declared_dimension = None  # diagnose 只读不抛；防御兜底
        return self._provider_factory(binding, declared_dimension)
