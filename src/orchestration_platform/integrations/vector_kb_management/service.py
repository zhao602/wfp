# =============================================================================
# 向量知识库管理服务门面（integrations/vector_kb_management/service.py）
#
# 职责：端点唯一后端入口（CHG-08）——绑定解析（只读）+ 知识库 CRUD/删除
# （元数据级联 + 可选向量清除）+ 导入/分片门面（转发 ingestion 流水线）+
# Embedding 候选（零凭据）+ 集合诊断（design §2.2.2 T1~T9、DC-07）。
# 设计意图（vector_kb_management design AC-39~AC-48 / AC-51~AC-53、
# INV-D10 / INV-D17 / INV-D22 / INV-D24）：
#   1. 一库一模型强绑定：KnowledgeBase.embedding_model 单值必填——
#      创建/更新未绑定 → VECTOR_KB_EMBEDDING_BINDING_REQUIRED（AC-40）；
#      一模型可绑多库（多对一，AC-43，无唯一性约束天然支持）；
#   2. 三路径一致（INV-D22/AC-53）：后台展示 / 前台选用 / 摄取向量化
#      经同一仓储实体（KnowledgeBase.embedding_model）解析出同一绑定，
#      resolve_binding 与执行器绑定解析同源同读；
#   3. 改绑维度前置校验（INV-D24/R-04）：集合已固化维度且新模型维度
#      可前置判定且不符 → VECTOR_KB_DIMENSION_BINDING_CONFLICT 前置拒绝；
#      维度不可前置判定（探测不可达 / 集合未创建）→ 放行改绑，写入期由
#      VECTOR_DB_DIMENSION_MISMATCH → VECTOR_KB_DIMENSION_BINDING_CONFLICT
#      映射兜底（确定性拒绝，不产生静默错写）；
#   4. 候选零凭据（AC-44/AC-45）：settings 即时派生 + 字段白名单
#      （model_identifier/mode/label）；settings 无 Embedding 标识字段
#      → 候选空 + allow_manual_input=true（手工填写，spec §5.5.3-1）；
#   5. 只读解析零写入：resolve_binding 经 repository.get（不建库、不触
#      集合，懒创建语义不破坏）；
#   6. 门面即唯一入口（T7.3）：导入/分片/确认转发 IngestionPipeline，
#      确认成功后回填草稿 vectorized 标注（分片列表可见性）；
#      删除默认仅元数据（purge_vectors=true 显式破坏性，登记实验）；
#      分页/归属/存在性以领域错误码确定性拒绝（422/404/409 语义经
#      错误子类承载，design §2.2.2 异常映射）。
# =============================================================================

import re
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Mapping, Optional, Set

from orchestration_platform.domain.errors import (
    VECTOR_KB_DIMENSION_BINDING_CONFLICT,
    VECTOR_KB_DOCUMENT_NOT_FOUND,
    VECTOR_KB_EMBEDDING_BINDING_REQUIRED,
    VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID,
    VECTOR_KB_INVALID_PAGINATION,
    VECTOR_KB_KNOWLEDGE_BASE_ALREADY_EXISTS,
    VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND,
    VECTOR_KB_ANNOTATION_INVALID,
    VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED,
    VECTOR_KB_CHUNK_NOT_FOUND,
    VECTOR_KB_DRAFT_VERSION_CONFLICT,
    VECTOR_KB_EMBEDDING_UNAVAILABLE,
    VECTOR_KB_STORAGE_UNAVAILABLE,
    VECTOR_KB_UNKNOWN_INGESTION_JOB,
    DomainError,
    InstanceStateConflictError,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.node_configurations import (
    ModelLoadingConfig,
)
from orchestration_platform.integrations.local_vector_database.models import (
    DiagnosticReport,
    RecordFailure,
    VectorRecord,
)
from orchestration_platform.integrations.local_vector_database.service import (
    LocalVectorDatabaseService,
)
from orchestration_platform.integrations.vector_kb_management.annotations import (
    ANNOTATIONS_KEY,
    ascii_lowercase,
    normalize_annotations,
    validate_custom_annotations,
)
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.integrations.vector_kb_management.config import (
    VectorKbManagementConfig,
    from_settings,
)
from orchestration_platform.integrations.vector_kb_management.ingestion import (
    IngestionPipeline,
    _next_version,
    map_delegate_error,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    KNOWLEDGE_BASE_ID_PATTERN,
    AnnotationAuditRecord,
    AnnotationLimits,
    AnnotationPatch,
    BulkAnnotateInput,
    BulkAnnotateReport,
    ChunkDraft,
    ChunkDraftConfirmResponse,
    ChunkDraftResponse,
    ChunkListResponse,
    ChunkOperation,
    ChunkRecord,
    ChunkStrategy,
    DeleteKnowledgeBaseResponse,
    EmbeddingCandidatesResponse,
    ImportDraftResponse,
    IngestionJob,
    IngestionReportReport,
    KBEmbeddingBinding,
    KBEmbeddingBindingSummary,
    KnowledgeBase,
    KnowledgeBaseDocument,
    KnowledgeBaseListResponse,
    KnowledgeBaseView,
    LowercaseReport,
    LowercaseRequest,
)
from orchestration_platform.integrations.vector_kb_management.repository import (
    IKnowledgeBaseRepository,
    SqliteKnowledgeBaseRepository,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    VectorRetrievalError,
)


def _annotation_limits() -> AnnotationLimits:
    """标注上界（读运行期 settings，缺省 32/512；DC-07）。"""
    settings = get_settings()
    return AnnotationLimits(
        max_items=int(getattr(settings, "vector_kb_annotation_max_items", 32)),
        max_value_length=int(
            getattr(settings, "vector_kb_annotation_max_value_length", 512)
        ),
    )


def _utc_now_iso() -> str:
    """当前 UTC 时刻 ISO-8601（审计/执行时刻）。"""
    return datetime.now(timezone.utc).isoformat()


def _require_valid_knowledge_base_id(knowledge_base_id: str) -> None:
    """标识白名单前置校验（禁路径穿越，INV-D11；与域模型 pattern 双保险）。"""
    if not re.fullmatch(KNOWLEDGE_BASE_ID_PATTERN, knowledge_base_id or ""):
        raise WorkflowValidationError(
            VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID,
            (
                f"知识库标识非法：{knowledge_base_id!r}"
                "（白名单 ^[a-z0-9][a-z0-9_-]{0,127}$）"
            ),
        )


def _require_valid_binding(binding: KBEmbeddingBinding) -> None:
    """绑定标识非空校验（spec §5.5.3-2：标识空白 → 确定性拒绝）。"""
    if not (binding.model_identifier or "").strip():
        raise WorkflowValidationError(
            VECTOR_KB_EMBEDDING_BINDING_REQUIRED,
            "Embedding 模型标识非法：空白标识拒绝创建/更新（spec §5.5.3-2）",
        )


def _require_valid_pagination(page: int, page_size: int) -> None:
    """分页参数校验（design §2.2.2 T5：page ≥ 1，1 ≤ page_size ≤ 200）。"""
    if page < 1 or page_size < 1 or page_size > 200:
        raise WorkflowValidationError(
            VECTOR_KB_INVALID_PAGINATION,
            (
                f"分页参数非法：page={page}（须 ≥ 1）、"
                f"page_size={page_size}（须 1 ≤ page_size ≤ 200）"
            ),
        )


def _probe_binding_dimension(binding: KBEmbeddingBinding) -> Optional[int]:
    """默认维度探测（改绑前置校验，INV-D24）。

    经 provider 工厂构造真实来源后试嵌一条探测文本，返回真实向量维度：

    - 构造失败 / 来源不可达 / 运行时缺失 → None（无法前置判定 → 放行
      改绑，写入期由 VECTOR_DB_DIMENSION_MISMATCH →
      VECTOR_KB_DIMENSION_BINDING_CONFLICT 映射兜底，不产生静默错写）；
    - 真实探测路径沙箱不可验证（NV-02/NV-03），经注入式
      dimension_probe seam 单测。
    """
    from orchestration_platform.integrations.vector_kb_management.ingestion import (
        _default_provider_factory,
    )

    provider = _default_provider_factory(binding, None)
    if provider is None:
        return None
    try:
        return len(provider.embed("维度探测"))
    except Exception:
        return None


def binding_to_model_config(binding: KBEmbeddingBinding) -> ModelLoadingConfig:
    """绑定 → 节点生效模型配置转换（同构字段映射，AC-48/AC-51/AC-53）。

    - mode/model_identifier/base_url/timeout_seconds 同构直映
      （base_url 为 api 嵌入服务地址，检索链路 _embed_query 实际使用，
      非凭据字段）；
    - device 白名单归一（cpu/gpu；非法值 → cpu 缺省）；
    - 零凭据不变：本转换不涉及 api_key（密钥仅运行期 settings 注入）。
    """
    device = binding.device if binding.device in ("cpu", "gpu") else "cpu"
    return ModelLoadingConfig(
        mode=binding.mode,
        model_identifier=binding.model_identifier,
        base_url=binding.base_url,
        timeout_seconds=binding.timeout_seconds,
        device=device,
        allow_degraded_fallback=binding.allow_degraded_fallback,
    )


def model_config_to_binding(cfg: ModelLoadingConfig) -> KBEmbeddingBinding:
    """节点生效模型配置 → 绑定反向转换（vkb_write_mode / design §2.2.2(5)）。

    - 同构字段直映（mode/model_identifier/base_url/timeout_seconds；
      base_url 为嵌入服务地址，非凭据字段）；
    - device 白名单归一（cpu/gpu；非法值 → cpu 缺省，与 binding_to_model_config 对称）；
    - 零凭据不变：不涉及 api_key（密钥仅运行期 settings 注入）；
    - 用途：写入模式下节点字段回退（绑定缺失时）统一供门面解析 provider
      （OP-11 最小暴露面：门面为唯一 provider 解析点）。
    """
    device = cfg.device if cfg.device in ("cpu", "gpu") else "cpu"
    return KBEmbeddingBinding(
        mode=cfg.mode,
        model_identifier=cfg.model_identifier,
        base_url=cfg.base_url,
        timeout_seconds=cfg.timeout_seconds,
        device=device,
        allow_degraded_fallback=cfg.allow_degraded_fallback,
    )


class VectorKbManagementService:
    """知识库管理服务门面（端点唯一后端入口，CHG-08）。

    - 注入式构造：config / repository / vector_service / dimension_probe
      均可注入（测试绕开全局 settings 与真实 embedding 来源）；
    - 门面方法：绑定解析（resolve_binding）/ 知识库 CRUD 与视图
      （create / list / get / update / delete）/ 导入与确认
      （import_document / confirm_import / get_ingestion_job）/
      分片（list_chunks / apply_chunk_draft / confirm_chunk_draft）/
      候选（list_embedding_candidates）/ 诊断（diagnose_collection）；
    - 导入/分片/确认转发 IngestionPipeline；确认成功后回填草稿
      vectorized 标注（分片列表可见性，T5 ChunkView 语义）。
    """

    def __init__(
        self,
        config: Optional[VectorKbManagementConfig] = None,
        repository: Optional[IKnowledgeBaseRepository] = None,
        vector_service: Optional[LocalVectorDatabaseService] = None,
        dimension_probe: Optional[
            Callable[[KBEmbeddingBinding], Optional[int]]
        ] = None,
    ) -> None:
        self._config = config if config is not None else from_settings()
        self._repository = (
            repository
            if repository is not None
            else SqliteKnowledgeBaseRepository(self._config.storage_root)
        )
        # 向量服务缺省自动构造（lvd 构造零副作用：不触盘、不建目录，
        # AC-28/INV-D23——服务仅在被路由启用后构造）；
        # 测试可注入桩（dimension_probe/vector_service seam，NV-02/NV-03）。
        self._vector_service = (
            vector_service
            if vector_service is not None
            else LocalVectorDatabaseService()
        )
        self._dimension_probe = (
            dimension_probe
            if dimension_probe is not None
            else _probe_binding_dimension
        )
        self._pipeline_instance: Optional[IngestionPipeline] = None

    # ------------------------------------------------------------------
    # 绑定解析（只读，三路径一致，AC-49/AC-53/INV-D22）
    # ------------------------------------------------------------------

    def resolve_binding(
        self, knowledge_base_id: str
    ) -> KBEmbeddingBindingSummary:
        """绑定解析（只读零写入）：返回去凭据绑定摘要。

        与执行器绑定解析、摄取向量化同源同读（同一仓储实体），
        三路径一致（INV-D22/AC-53）；知识库不存在 → 404 语义错误码。
        """
        knowledge_base = self._require_knowledge_base(knowledge_base_id)
        return knowledge_base.binding_summary()

    # ------------------------------------------------------------------
    # 知识库创建 / 更新（T1/T2；单值必填 + 改绑维度前置校验）
    # ------------------------------------------------------------------

    def create_knowledge_base(
        self,
        knowledge_base_id: str,
        name: str,
        embedding_model: Optional[KBEmbeddingBinding] = None,
        default_chunk_strategy: Optional[ChunkStrategy] = None,
    ) -> KnowledgeBase:
        """创建知识库（元数据落盘；集合懒创建不触，T1 后置条件）。

        - embedding_model 单值必填：None → VECTOR_KB_EMBEDDING_BINDING_REQUIRED
          （AC-40/INV-D17）；标识空白 → 同码拒绝（spec §5.5.3-2）；
        - 标识已占用 → VECTOR_KB_KNOWLEDGE_BASE_ALREADY_EXISTS（409）；
        - 标识非法（白名单）→ VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID；
        - 多对一：同模型可绑多库（AC-43，无唯一性约束）。
        """
        if embedding_model is None:
            raise WorkflowValidationError(
                VECTOR_KB_EMBEDDING_BINDING_REQUIRED,
                "未绑定 Embedding 模型：embedding_model 单值必填（AC-40）",
            )
        _require_valid_binding(embedding_model)
        _require_valid_knowledge_base_id(knowledge_base_id)
        if self._repository.exists(knowledge_base_id):
            raise InstanceStateConflictError(
                VECTOR_KB_KNOWLEDGE_BASE_ALREADY_EXISTS,
                f"知识库标识已被占用：{knowledge_base_id}",
            )
        knowledge_base = KnowledgeBase(
            knowledge_base_id=knowledge_base_id,
            name=name,
            embedding_model=embedding_model,
            default_chunk_strategy=default_chunk_strategy,
        )
        self._repository.save(knowledge_base)
        return knowledge_base

    def update_knowledge_base(
        self,
        knowledge_base_id: str,
        name: Optional[str] = None,
        default_chunk_strategy: Optional[ChunkStrategy] = None,
        embedding_model: Optional[KBEmbeddingBinding] = None,
    ) -> KnowledgeBase:
        """更新知识库（部分更新：None 字段不改变，T2）。

        - 改绑显式路径（AC-41）：embedding_model 给定且与现绑定不同 →
          维度前置校验（INV-D24）→ 落盘新绑定；
        - 相同绑定（mode + 标识一致）→ 幂等保存（无维度探测开销）；
        - 单值必填不变式：不提供解绑路径（不可清空绑定，AC-40）。
        """
        current = self._require_knowledge_base(knowledge_base_id)
        if embedding_model is not None:
            _require_valid_binding(embedding_model)
            rebinding = (
                embedding_model.mode != current.embedding_model.mode
                or embedding_model.model_identifier
                != current.embedding_model.model_identifier
            )
            if rebinding:
                self._ensure_dimension_compatible(knowledge_base_id, embedding_model)
        updated = KnowledgeBase(
            knowledge_base_id=current.knowledge_base_id,
            name=name if name is not None else current.name,
            embedding_model=(
                embedding_model
                if embedding_model is not None
                else current.embedding_model
            ),
            default_chunk_strategy=(
                default_chunk_strategy
                if default_chunk_strategy is not None
                else current.default_chunk_strategy
            ),
        )
        self._repository.save(updated)
        return updated

    # ------------------------------------------------------------------
    # Embedding 候选（零凭据，AC-44/AC-45）
    # ------------------------------------------------------------------

    def list_embedding_candidates(self) -> EmbeddingCandidatesResponse:
        """Embedding 模型候选（settings 即时派生 + 零凭据白名单）。

        派生口径（复用 model_catalog_router 范式：settings 即时派生 +
        字段白名单 + 非空过滤）：

        - settings 无 Embedding 模型标识字段（DC-10 / T7.1 字段清单均
          未含）→ 不伪造候选：candidates 恒空、total=0；
        - allow_manual_input 恒 true（手工填写标识；空候选必为 true，
          spec §5.5.3-1，不得阻断创建）；
        - 零凭据为结构性质：响应模型字段白名单
          （model_identifier/mode/label），无 base_url / 无 api_key 字段位。
        """
        return EmbeddingCandidatesResponse(
            candidates=[], total=0, allow_manual_input=True
        )

    # ------------------------------------------------------------------
    # 知识库列表 / 详情 / 删除（T2/T7.3；视图零凭据）
    # ------------------------------------------------------------------

    def knowledge_base_view(
        self, knowledge_base: KnowledgeBase
    ) -> KnowledgeBaseView:
        """实体 → 视图（零凭据 + 元数据派生计数 + 技术时间戳）。

        - embedding_model 为绑定摘要（KBEmbeddingBindingSummary，
          不含 base_url，AC-45）；
        - document_count = 元数据文档数；chunk_count = 各文档最新草稿
          分片数合计（元数据派生，不触向量存储）；
        - created_at / updated_at 经 get_timestamps（缺失 → None）。
        """
        knowledge_base_id = knowledge_base.knowledge_base_id
        documents = self._repository.list_documents(knowledge_base_id)
        chunk_count = 0
        for document in documents:
            draft = self._repository.get_draft(document.document_id)
            if draft is not None:
                chunk_count += len(draft.chunks)
        timestamps = self._repository.get_timestamps(knowledge_base_id)
        return KnowledgeBaseView(
            knowledge_base_id=knowledge_base_id,
            name=knowledge_base.name,
            embedding_model=knowledge_base.binding_summary(),
            default_chunk_strategy=knowledge_base.default_chunk_strategy,
            document_count=len(documents),
            chunk_count=chunk_count,
            created_at=timestamps[0] if timestamps is not None else None,
            updated_at=timestamps[1] if timestamps is not None else None,
        )

    def list_knowledge_bases(
        self, page: int = 1, page_size: int = 50
    ) -> KnowledgeBaseListResponse:
        """知识库列表（分页；前台候选与后台共用，AC-47）。"""
        _require_valid_pagination(page, page_size)
        entities = self._repository.list_all()
        start = (page - 1) * page_size
        return KnowledgeBaseListResponse(
            items=[
                self.knowledge_base_view(entity)
                for entity in entities[start : start + page_size]
            ],
            total=len(entities),
        )

    def get_knowledge_base(
        self, knowledge_base_id: str
    ) -> KnowledgeBaseView:
        """知识库详情视图（缺失 → 404）。"""
        knowledge_base = self._require_knowledge_base(knowledge_base_id)
        return self.knowledge_base_view(knowledge_base)

    def delete_knowledge_base(
        self, knowledge_base_id: str, purge_vectors: bool = False
    ) -> DeleteKnowledgeBaseResponse:
        """删除知识库（design §2.2.2 T2）。

        - 默认（purge_vectors=False）仅删元数据（仓储同事务级联清理
          文档/草稿/作业），向量集合保留；
        - purge_vectors=True 为**显式破坏性操作**（登记实验）：先清除
          向量集合记录（失败 → 映射错误且元数据不删，安全方向），
          再删元数据；purged_vectors 仅在清除执行完成时为 True。
        """
        self._require_knowledge_base(knowledge_base_id)
        purged = False
        if purge_vectors:
            self._purge_collection_records(knowledge_base_id)
            purged = True
        self._repository.delete(knowledge_base_id)
        return DeleteKnowledgeBaseResponse(
            knowledge_base_id=knowledge_base_id,
            purged_vectors=purged,
        )

    # ------------------------------------------------------------------
    # 导入 / 确认 / 作业（T3/T4；转发 ingestion 流水线）
    # ------------------------------------------------------------------

    def import_document(
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
        """受理导入（T3）：解析 → 分片 → 草稿 + 作业 pending（零写存储）。

        vkb_doc_annotation（CHG-03）：转发可选标注入参（既有签名零改动）。
        """
        return self._pipeline().ingest_document(
            knowledge_base_id,
            file_name,
            data,
            strategy=strategy,
            max_chunk_length=max_chunk_length,
            overlap=overlap,
            similarity_threshold=similarity_threshold,
            hierarchy=hierarchy,
            annotation_origin=annotation_origin,
            annotation_published_at=annotation_published_at,
            annotation_custom=annotation_custom,
        )

    def confirm_import(
        self,
        knowledge_base_id: str,
        document_id: str,
        job_id: str,
        snapshot_version: str,
        allow_degraded_fallback: bool = False,
    ) -> IngestionReportReport:
        """确认导入（T4）：向量化 + 委托写入（转发 ingestion）。

        成功/部分成功后回填草稿 vectorized 标注（partial 排除失败条目），
        供分片列表（T5 ChunkView）可见性使用。
        """
        report = self._pipeline().confirm_import(
            knowledge_base_id,
            document_id,
            job_id,
            snapshot_version=snapshot_version,
            allow_degraded_fallback=allow_degraded_fallback,
        )
        if report.status in ("succeeded", "partial"):
            exclude_ids = (
                {failure.chunk_id for failure in report.failed}
                if report.status == "partial"
                else None
            )
            self._mark_draft_vectorized(
                document_id,
                snapshot_version,
                None,
                exclude_ids,
                report.provenance,
                report.local_degraded,
            )
        return report

    def get_ingestion_job(
        self, knowledge_base_id: str, job_id: str
    ) -> IngestionJob:
        """摄取作业查询（kb 归属校验；未知/归属不符 → 404）。

        标识前置白名单校验（INV-D11：非法 → 422，禁路径穿越）。
        """
        _require_valid_knowledge_base_id(knowledge_base_id)
        job = self._repository.get_job(job_id)
        if job is None or job.knowledge_base_id != knowledge_base_id:
            raise ResourceNotFoundError(
                VECTOR_KB_UNKNOWN_INGESTION_JOB,
                f"摄取作业不存在或不属于该知识库：{job_id}",
            )
        return job

    # ------------------------------------------------------------------
    # 分片列表 / 调整草稿 / 确认调整（T5/T6/T7）
    # ------------------------------------------------------------------

    def list_chunks(
        self,
        knowledge_base_id: str,
        document_id: str,
        page: int = 1,
        page_size: int = 50,
        include_vector_status: bool = True,
    ) -> ChunkListResponse:
        """分片列表（T5：分页只读；vectorized/degraded 计数为全量口径）。

        - 数据源为文档最新草稿（ChunkView ≡ ChunkRecord）；
        - include_vector_status 保留参数（本期 vectorized 标注由确认
          门面回填草稿元数据承载；向量存储实时比对登记 P2）。
        """
        _require_valid_pagination(page, page_size)
        self._require_knowledge_base(knowledge_base_id)
        self._require_document(knowledge_base_id, document_id)
        draft = self._repository.get_draft(document_id)
        if draft is None:
            raise ResourceNotFoundError(
                VECTOR_KB_DOCUMENT_NOT_FOUND,
                f"文档分片草稿缺失：{document_id}",
            )
        chunks = draft.chunks
        start = (page - 1) * page_size
        return ChunkListResponse(
            items=chunks[start : start + page_size],
            total=len(chunks),
            page=page,
            page_size=page_size,
            vectorized_count=sum(
                1 for chunk in chunks if chunk.vectorized
            ),
            degraded_count=sum(
                1 for chunk in chunks if chunk.local_degraded
            ),
        )

    def apply_chunk_draft(
        self,
        knowledge_base_id: str,
        document_id: str,
        base_version: str,
        operations: List[ChunkOperation],
    ) -> ChunkDraftResponse:
        """提交调整草稿（T6）：合并/拆分/编辑 → 草稿（不生效，转发 ingestion）。"""
        return self._pipeline().apply_chunk_draft(
            knowledge_base_id, document_id, base_version, operations
        )

    def confirm_chunk_draft(
        self,
        knowledge_base_id: str,
        document_id: str,
        draft_version: str,
    ) -> ChunkDraftConfirmResponse:
        """确认调整（T7）：仅重算受影响分片（转发 ingestion）。

        成功/部分成功后回填草稿 vectorized 标注（仅 rewritten 条目；
        provenance 按绑定模式派生：hash_fallback / api / local_runtime）。
        """
        response = self._pipeline().confirm_chunk_draft(
            knowledge_base_id, document_id, draft_version
        )
        if response.rewritten:
            binding = self._require_knowledge_base(
                knowledge_base_id
            ).embedding_model
            provenance = (
                "hash_fallback"
                if response.local_degraded
                else ("api" if binding.mode == "api" else "local_runtime")
            )
            self._mark_draft_vectorized(
                document_id,
                draft_version,
                response.rewritten,
                None,
                provenance,
                response.local_degraded,
            )
        return response

    # ------------------------------------------------------------------
    # 集合诊断（T9：透传 diagnose，不抛异常）
    # ------------------------------------------------------------------

    def diagnose_collection(
        self, knowledge_base_id: str
    ) -> DiagnosticReport:
        """集合诊断（只读透传 lvd diagnose；集合不存在 → ok=False 报告）。

        - 标识非法 → 422（INV-D11 白名单，禁路径穿越）；
        - diagnose 对合法标识不抛异常（design §2.2.2 T9 异常映射：无）。
        """
        _require_valid_knowledge_base_id(knowledge_base_id)
        return self._require_vector_service().diagnose(knowledge_base_id)

    # ------------------------------------------------------------------
    # 文档标注（vkb_doc_annotation / CHG-03/CHG-04/CHG-05/CHG-20）
    # ------------------------------------------------------------------

    def bulk_annotate(
        self,
        knowledge_base_id: str,
        document_id: str,
        annotation_input: BulkAnnotateInput,
    ) -> BulkAnnotateReport:
        """一键标注所有切片（CHG-03 / DC-10 / OP-08）。

        - 目标 = 当前文档最新草稿全部切片；逐片合并（不同名保留 / 同名覆盖）；
        - 部分失败（单切片非法）→ status="partial" + 失败清单，合法切片照常写入；
        - 写回经既有 ``upsert_records``（唯一写入口，INV-19）+ 幂等（同键覆盖）。
        """
        started = time.perf_counter()
        self._require_knowledge_base(knowledge_base_id)
        self._require_document(knowledge_base_id, document_id)
        draft = self._repository.get_draft(document_id)
        if draft is None:
            raise ResourceNotFoundError(
                VECTOR_KB_DOCUMENT_NOT_FOUND,
                f"文档分片草稿缺失，无法一键标注：{document_id}",
            )
        vector_service = self._require_vector_service()
        existing = {
            record.record_id: record
            for record in self._read_collection_vectors(knowledge_base_id)
        }
        limits = _annotation_limits()
        failed: List[RecordFailure] = []
        updated_chunks: List[ChunkRecord] = []
        to_upsert: List[VectorRecord] = []
        for chunk in draft.chunks:
            source: Dict[str, JsonValue] = dict(chunk.source or {})
            old = existing.get(chunk.chunk_id)
            raw_existing = (
                (old.source or {}).get(ANNOTATIONS_KEY)
                if old is not None
                else source.get(ANNOTATIONS_KEY)
            )
            existing_ann = (
                dict(raw_existing) if isinstance(raw_existing, Mapping) else {}
            )
            origin_candidate = (
                annotation_input.origin
                if annotation_input.origin
                else existing_ann.get("origin")
            )
            try:
                merged = normalize_annotations(
                    channel="backend",
                    origin=(
                        origin_candidate
                        if isinstance(origin_candidate, str)
                        else None
                    ),
                    published_at=annotation_input.published_at,
                    custom=annotation_input.custom,
                    ingested_at=_utc_now_iso(),
                    existing=existing_ann,
                    limits=limits,
                )
            except DomainError as error:
                failed.append(
                    RecordFailure(
                        record_id=chunk.chunk_id,
                        reason=getattr(error, "message", str(error)),
                    )
                )
                updated_chunks.append(chunk)
                continue
            source[ANNOTATIONS_KEY] = merged
            updated_chunks.append(chunk.model_copy(update={"source": source}))
            if old is not None:
                to_upsert.append(old.model_copy(update={"source": source}))
        if to_upsert:
            try:
                vector_service.upsert_records(knowledge_base_id, to_upsert)
            except VectorRetrievalError as error:
                raise map_delegate_error(error) from None
        self._repository.save_draft(
            draft.model_copy(update={"chunks": updated_chunks})
        )
        self._repository.save_annotation_audit(
            AnnotationAuditRecord(
                audit_id=f"aud_{uuid.uuid4().hex[:8]}",
                document_id=document_id,
                action="bulk_annotate",
                affected_count=len(updated_chunks) - len(failed),
                executed_at=_utc_now_iso(),
                detail={"failed": len(failed)},
            )
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        return BulkAnnotateReport(
            status="partial" if failed else "succeeded",
            document_id=document_id,
            targeted_count=len(draft.chunks),
            updated_count=len(updated_chunks) - len(failed),
            failed=failed,
            duration_ms=duration_ms,
        )

    def lowercase_all(
        self,
        knowledge_base_id: str,
        document_id: str,
        request: LowercaseRequest,
    ) -> LowercaseReport:
        """一键统一小写（CHG-03 / CHG-05 / DC-09 / OP-07）。

        - 未二次确认（``confirmed=False``）→ 确定性拒绝（不可逆操作）；
        - ``scope="annotations"``（默认）→ 仅 origin+custom 值 ASCII 小写，正文逐字不变；
        - ``scope="annotations_and_text"`` → 正文小写 + 受影响切片向量重算 + 幂等更新。
        """
        started = time.perf_counter()
        if not request.confirmed:
            raise WorkflowValidationError(
                VECTOR_KB_ANNOTATION_INVALID,
                "一键统一小写为不可逆操作，须二次确认（confirmed=True）后执行",
            )
        knowledge_base = self._require_knowledge_base(knowledge_base_id)
        self._require_document(knowledge_base_id, document_id)
        draft = self._repository.get_draft(document_id)
        if draft is None:
            raise ResourceNotFoundError(
                VECTOR_KB_DOCUMENT_NOT_FOUND,
                f"文档分片草稿缺失，无法统一小写：{document_id}",
            )
        vector_service = self._require_vector_service()
        existing = {
            record.record_id: record
            for record in self._read_collection_vectors(knowledge_base_id)
        }
        converted_values = 0
        converted_chunks = 0
        needs_recompute = False
        updated_chunks: List[ChunkRecord] = []
        to_upsert: List[VectorRecord] = []
        for chunk in draft.chunks:
            source: Dict[str, JsonValue] = dict(chunk.source or {})
            raw_annotations = source.get(ANNOTATIONS_KEY)
            text = chunk.text
            changed = False
            if isinstance(raw_annotations, Mapping):
                new_annotations = dict(raw_annotations)
                origin = new_annotations.get("origin")
                if isinstance(origin, str):
                    lowered = ascii_lowercase(origin)
                    if lowered != origin:
                        new_annotations["origin"] = lowered
                        converted_values += 1
                        changed = True
                raw_custom = new_annotations.get("custom")
                if isinstance(raw_custom, Mapping):
                    new_custom: Dict[str, JsonValue] = {}
                    for name, value in raw_custom.items():
                        if isinstance(value, str):
                            lowered = ascii_lowercase(value)
                            if lowered != value:
                                converted_values += 1
                                changed = True
                            new_custom[name] = lowered
                        else:
                            new_custom[name] = value
                    new_annotations["custom"] = new_custom
                if changed:
                    source[ANNOTATIONS_KEY] = new_annotations
            if request.scope == "annotations_and_text":
                lowered_text = ascii_lowercase(text)
                if lowered_text != text:
                    text = lowered_text
                    changed = True
                    needs_recompute = True
            if changed:
                converted_chunks += 1
                updated_chunks.append(
                    chunk.model_copy(update={"source": source, "text": text})
                )
                old = existing.get(chunk.chunk_id)
                if old is not None:
                    to_upsert.append(
                        old.model_copy(update={"source": source, "text": text})
                    )
            else:
                updated_chunks.append(chunk)
        if needs_recompute and to_upsert:
            provider = self._pipeline()._resolve_provider(
                knowledge_base_id, knowledge_base.embedding_model
            )
            if provider is None:
                raise DomainError(
                    VECTOR_KB_EMBEDDING_UNAVAILABLE,
                    "正文小写需向量重算，但无可用向量化来源；已保留既有数据"
                    "（不半写入）",
                )
            to_upsert = [
                record.model_copy(update={"vector": provider.embed(record.text)})
                for record in to_upsert
            ]
        if to_upsert:
            try:
                vector_service.upsert_records(knowledge_base_id, to_upsert)
            except VectorRetrievalError as error:
                raise map_delegate_error(error) from None
        self._repository.save_draft(
            draft.model_copy(update={"chunks": updated_chunks})
        )
        executed_at = _utc_now_iso()
        self._repository.save_annotation_audit(
            AnnotationAuditRecord(
                audit_id=f"aud_{uuid.uuid4().hex[:8]}",
                document_id=document_id,
                action="lowercase",
                scope=request.scope,
                affected_count=converted_chunks,
                executed_at=executed_at,
                detail={"converted_values": converted_values},
            )
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return LowercaseReport(
            status="succeeded",
            document_id=document_id,
            scope=request.scope,
            converted_chunks=converted_chunks,
            converted_values=converted_values,
            elapsed_ms=elapsed_ms,
            executed_at=executed_at,
            failed=[],
        )

    def _read_collection_vectors(
        self, knowledge_base_id: str
    ) -> List[VectorRecord]:
        """只读复用：经既有 ``store.load_records`` 取回集合记录（含向量；DC-13）。

        - 存储句柄不可用（防御）→ 空列表（调用方按"集合无记录"处理）；
        - 零副作用只读，不改既有集合。
        """
        store = getattr(self._require_vector_service(), "_store", None)
        if store is not None and hasattr(store, "load_records"):
            return list(store.load_records(knowledge_base_id))
        return []

    def apply_annotation_draft(
        self,
        knowledge_base_id: str,
        document_id: str,
        base_version: str,
        patches: List[AnnotationPatch],
    ) -> ChunkDraftResponse:
        """提交标注编辑草稿（未确认不生效；DC-07 / Z-04 / AC-43/AC-44）。

        - 目标 = 当前文档最新草稿；按 patch 的 set/unset 增/改/删自定义标注；
        - 版本冲突 → VECTOR_KB_DRAFT_VERSION_CONFLICT；分片不存在 → VECTOR_KB_CHUNK_NOT_FOUND；
        - 未确认 → 检索/列表仍返回旧值（草稿仅元数据可见，INV-D19）。
        """
        self._require_knowledge_base(knowledge_base_id)
        self._require_document(knowledge_base_id, document_id)
        latest = self._repository.get_draft(document_id)
        if latest is None:
            raise ResourceNotFoundError(
                VECTOR_KB_DOCUMENT_NOT_FOUND,
                f"文档分片草稿缺失，无法提交标注草稿：{document_id}",
            )
        if base_version and base_version != latest.draft_version:
            raise InstanceStateConflictError(
                VECTOR_KB_DRAFT_VERSION_CONFLICT,
                f"草稿版本冲突：请求基线 {base_version}，"
                f"当前 {latest.draft_version}",
            )
        known_ids = {chunk.chunk_id for chunk in latest.chunks}
        for patch in patches:
            if patch.chunk_id not in known_ids:
                raise ResourceNotFoundError(
                    VECTOR_KB_CHUNK_NOT_FOUND,
                    f"分片不存在：{patch.chunk_id}",
                )
        limits = _annotation_limits()
        patch_by_id = {patch.chunk_id: patch for patch in patches}
        updated: List[ChunkRecord] = []
        for chunk in latest.chunks:
            patch = patch_by_id.get(chunk.chunk_id)
            if patch is None:
                updated.append(chunk)
                continue
            source: Dict[str, JsonValue] = dict(chunk.source or {})
            raw = source.get(ANNOTATIONS_KEY)
            annotations = dict(raw) if isinstance(raw, Mapping) else {}
            custom = (
                dict(annotations.get("custom"))
                if isinstance(annotations.get("custom"), Mapping)
                else {}
            )
            for name in patch.unset or []:
                custom.pop(name, None)
            if patch.set:
                custom.update(validate_custom_annotations(patch.set, limits))
            if len(custom) > limits.max_items:
                raise WorkflowValidationError(
                    VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED,
                    f"合并后自定义标注项数超上界：{len(custom)} > "
                    f"{limits.max_items}",
                )
            annotations["custom"] = custom
            source[ANNOTATIONS_KEY] = annotations
            updated.append(chunk.model_copy(update={"source": source}))
        new_version = _next_version(latest.draft_version)
        self._repository.save_draft(
            latest.model_copy(
                update={
                    "draft_version": new_version,
                    "base_version": latest.draft_version,
                    "chunks": updated,
                }
            )
        )
        return ChunkDraftResponse(
            draft_version=new_version,
            chunks=[
                ChunkDraft(
                    chunk_id=record.chunk_id,
                    index=record.index,
                    text=record.text,
                    source=record.source,
                )
                for record in updated
            ],
            notices=[],
        )

    def confirm_annotation_draft(
        self,
        knowledge_base_id: str,
        document_id: str,
        draft_version: str,
    ) -> ChunkDraftConfirmResponse:
        """确认标注编辑草稿（幂等更新，不重算向量；DC-13 / Z-04 / AC-44）。

        - 仅替换 ``source["annotations"]``（读-改-写：复用旧向量，不重算）；
        - 确认后经既有 ``upsert_records`` 同键写回（INV-D20 幂等）。
        """
        self._require_knowledge_base(knowledge_base_id)
        self._require_document(knowledge_base_id, document_id)
        draft = self._repository.get_draft(document_id, draft_version)
        if draft is None:
            raise ResourceNotFoundError(
                VECTOR_KB_DOCUMENT_NOT_FOUND,
                f"待确认标注草稿不存在：{document_id}@{draft_version}",
            )
        vector_service = self._require_vector_service()
        existing = {
            record.record_id: record
            for record in self._read_collection_vectors(knowledge_base_id)
        }
        to_upsert: List[VectorRecord] = []
        rewritten: List[str] = []
        for chunk in draft.chunks:
            old = existing.get(chunk.chunk_id)
            if old is None:
                continue
            to_upsert.append(old.model_copy(update={"source": chunk.source}))
            rewritten.append(chunk.chunk_id)
        if to_upsert:
            try:
                vector_service.upsert_records(knowledge_base_id, to_upsert)
            except VectorRetrievalError as error:
                raise map_delegate_error(error) from None
        self._repository.set_confirmed_version(document_id, draft_version)
        return ChunkDraftConfirmResponse(
            status="succeeded",
            rewritten=rewritten,
            deleted=[],
            local_degraded=False,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _require_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBase:
        """知识库存在性前置校验（缺失 → 404 语义错误码）。"""
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

    def _ensure_dimension_compatible(
        self, knowledge_base_id: str, binding: KBEmbeddingBinding
    ) -> None:
        """改绑维度前置校验（INV-D24 / R-04）。

        - 集合未创建（懒创建，无固化维度）→ 放行（首写固化维度语义）；
        - 新模型维度不可前置判定（探测 None / 诊断不可用）→ 放行，
          写入期映射兜底（确定性拒绝，不静默错写）；
        - 可判定且不符 → VECTOR_KB_DIMENSION_BINDING_CONFLICT 前置拒绝。
        """
        if self._vector_service is None:
            return
        try:
            diagnosis = self._vector_service.diagnose(knowledge_base_id)
        except Exception:
            return
        declared_dimension = diagnosis.declared_dimension
        if declared_dimension is None:
            return
        probed_dimension = self._dimension_probe(binding)
        if probed_dimension is None:
            return
        if probed_dimension != declared_dimension:
            raise InstanceStateConflictError(
                VECTOR_KB_DIMENSION_BINDING_CONFLICT,
                (
                    f"改绑模型维度冲突：新模型维度 {probed_dimension} 与"
                    f"集合固化维度 {declared_dimension} 不符"
                    "（前置拒绝，INV-D24）"
                ),
            )

    def _require_vector_service(self) -> LocalVectorDatabaseService:
        """向量服务前置（构造已保证非 None；防御性不可用拒绝）。"""
        if self._vector_service is None:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "向量存储服务不可用（未注入且缺省构造失败）",
            )
        return self._vector_service

    def _pipeline(self) -> IngestionPipeline:
        """摄取流水线惰性单例（首次导入/分片调用才构造，INV-D23）。"""
        if self._pipeline_instance is None:
            self._pipeline_instance = IngestionPipeline(
                self._repository,
                self._require_vector_service(),
                self._config,
            )
        return self._pipeline_instance

    def _mark_draft_vectorized(
        self,
        document_id: str,
        draft_version: str,
        chunk_ids: Optional[Set[str]],
        exclude_ids: Optional[Set[str]],
        provenance: str,
        local_degraded: bool,
    ) -> None:
        """确认成功后回填草稿 vectorized 标注（同版本就地覆盖）。

        - chunk_ids=None → 全量标注；给定 → 仅标注集合内条目
          （confirm_chunk_draft 仅重算 rewritten 条目）；
        - exclude_ids → 排除条目（partial 失败明细，保留原标注）；
        - 草稿缺失（防御）→ 静默跳过（标注为派生可见性，不反向阻断）。
        """
        draft = self._repository.get_draft(document_id, draft_version)
        if draft is None:
            return
        changed = False
        updated_chunks: List[ChunkRecord] = []
        for chunk in draft.chunks:
            if exclude_ids is not None and chunk.chunk_id in exclude_ids:
                updated_chunks.append(chunk)
                continue
            if chunk_ids is not None and chunk.chunk_id not in chunk_ids:
                updated_chunks.append(chunk)
                continue
            changed = True
            updated_chunks.append(
                chunk.model_copy(
                    update={
                        "vectorized": True,
                        "provenance": provenance,
                        "local_degraded": local_degraded,
                    }
                )
            )
        if changed:
            self._repository.save_draft(
                draft.model_copy(update={"chunks": updated_chunks})
            )

    def _enumerate_collection_record_ids(
        self, knowledge_base_id: str
    ) -> List[str]:
        """枚举集合记录标识（purge 载荷）。

        - 生产路径：向量服务内部存储全量枚举（store.load_records 公开
          只读；集合缺失 → 空，零副作用）；
        - 回退路径（存储句柄不可用，防御）：元数据派生——各文档最新
          草稿 chunk_id（生效基线的元数据近似）。
        """
        store = getattr(self._require_vector_service(), "_store", None)
        if store is not None and hasattr(store, "load_records"):
            return [
                record.record_id
                for record in store.load_records(knowledge_base_id)
            ]
        record_ids: List[str] = []
        for document in self._repository.list_documents(knowledge_base_id):
            draft = self._repository.get_draft(document.document_id)
            if draft is not None:
                record_ids.extend(
                    chunk.chunk_id for chunk in draft.chunks
                )
        return record_ids

    def _purge_collection_records(self, knowledge_base_id: str) -> None:
        """清除向量集合记录（显式破坏性操作；委托 lvd delete_records）。

        - 记录为空（集合未创建）→ no-op（不触存储，零副作用）；
        - 委托失败 → map_delegate_error 分层映射（元数据不删，安全方向）。
        """
        record_ids = self._enumerate_collection_record_ids(knowledge_base_id)
        if not record_ids:
            return
        try:
            self._require_vector_service().delete_records(
                knowledge_base_id, record_ids
            )
        except VectorRetrievalError as error:
            raise map_delegate_error(error) from None
