# =============================================================================
# 向量知识库管理路由（api/routers/vector_kb_management_router.py）
#
# 职责：暴露知识库管理 REST 端点（vector_kb_management design §2.2.2
# T1~T9 共 12 端点 + 摄取作业查询 1 端点；全部经 VectorKbManagementService
# 门面，CHG-08 / T7.4）。
# 设计意图（design AC-39~AC-53、INV-D10 / INV-D11 / INV-D23、CHG-DEP）：
#   1. 开关门控：application_factory 按 settings.vector_kb_management_enabled
#      条件注册（关闭 → 端点 404；服务不构造、目录零创建，INV-D23）；
#   2. 零凭据：响应模型白名单（绑定摘要 KBEmbeddingBindingSummary 无
#      base_url / api_key 字段位，AC-45 / INV-D10）；
#   3. multipart 可选依赖（CHG-DEP：延迟导入 + 可选启用 + 缺失降级）：
#      文件导入端点按 python-multipart 可用性条件注册——FastAPI 在
#      路由定义期探测（缺依赖时无条件声明 File/Form 参数将使模块导入
#      崩溃 RuntimeError）；缺失 → 端点不注册（404 降级），门面
#      import_document 仍完整可用；
#   4. 错误契约：领域错误子类经应用工厂统一映射
#      （422/409/404/500 + {code, message, details}）；
#   5. 分页/标识校验以领域错误码确定性拒绝（不叠加 Query 约束，
#      保持统一错误体：VECTOR_KB_INVALID_PAGINATION / INV-D11 白名单）。
# =============================================================================

import json
from typing import List, Optional

from fastapi import APIRouter, File, Form, Query, UploadFile

from orchestration_platform.domain.errors import (
    VECTOR_KB_ANNOTATION_INVALID,
    WorkflowValidationError,
)
from orchestration_platform.integrations.local_vector_database.models import (
    DiagnosticReport,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    AnnotationDraftRequest,
    BulkAnnotateInput,
    BulkAnnotateReport,
    ChunkDraftConfirmResponse,
    ChunkDraftRequest,
    ChunkDraftResponse,
    ChunkListResponse,
    ChunkStrategy,
    ConfirmAnnotationDraftRequest,
    ConfirmChunkDraftRequest,
    ConfirmImportRequest,
    DeleteKnowledgeBaseResponse,
    EmbeddingCandidatesResponse,
    ImportDraftResponse,
    IngestionJob,
    IngestionReportReport,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseListResponse,
    KnowledgeBaseUpdateRequest,
    KnowledgeBaseView,
    LowercaseReport,
    LowercaseRequest,
)
from orchestration_platform.integrations.vector_kb_management.service import (
    VectorKbManagementService,
)

# python-multipart 可用性探测（可选依赖；缺失 → 导入端点不注册）
try:
    import python_multipart  # noqa: F401

    MULTIPART_AVAILABLE = True
except ImportError:
    MULTIPART_AVAILABLE = False

# 模块级路由器：前缀 /kb-management（/api/v1 由应用工厂统一注入）
router = APIRouter(prefix="/kb-management", tags=["向量知识库管理"])

# 服务实例持有（惰性缺省构造 + 测试注入 seam）
_service: Optional[VectorKbManagementService] = None


def set_vector_kb_management_service(
    service: Optional[VectorKbManagementService],
) -> None:
    """注入/复位服务实例（测试 seam；None → 回落惰性缺省构造）。"""
    global _service
    _service = service


def _get_service() -> VectorKbManagementService:
    """服务解析：优先注入实例，缺省惰性构造（settings 派生配置）。"""
    global _service
    if _service is None:
        _service = VectorKbManagementService()
    return _service


# ---------------------------------------------------------------------------
# T1 / T2：知识库 CRUD（视图零凭据；删除可选 purge_vectors）
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseView,
    response_model_exclude_none=True,
)
async def create_knowledge_base(
    request: KnowledgeBaseCreateRequest,
) -> KnowledgeBaseView:
    """T1：创建知识库（单值绑定必填；重复 → 409；标识非法 → 422）。"""
    service = _get_service()
    entity = service.create_knowledge_base(
        knowledge_base_id=request.knowledge_base_id,
        name=request.name,
        embedding_model=request.embedding_model,
        default_chunk_strategy=request.default_chunk_strategy,
    )
    return service.knowledge_base_view(entity)


@router.get(
    "/knowledge-bases",
    response_model=KnowledgeBaseListResponse,
    response_model_exclude_none=True,
)
async def list_knowledge_bases(
    page: int = Query(default=1),
    page_size: int = Query(default=50),
) -> KnowledgeBaseListResponse:
    """T2：知识库列表（分页；非法分页 → 422 VECTOR_KB_INVALID_PAGINATION）。"""
    return _get_service().list_knowledge_bases(page=page, page_size=page_size)


@router.get(
    "/knowledge-bases/{knowledge_base_id}",
    response_model=KnowledgeBaseView,
    response_model_exclude_none=True,
)
async def get_knowledge_base(knowledge_base_id: str) -> KnowledgeBaseView:
    """T2：知识库详情（缺失 → 404 VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND）。"""
    return _get_service().get_knowledge_base(knowledge_base_id)


@router.put(
    "/knowledge-bases/{knowledge_base_id}",
    response_model=KnowledgeBaseView,
    response_model_exclude_none=True,
)
async def update_knowledge_base(
    knowledge_base_id: str,
    request: KnowledgeBaseUpdateRequest,
) -> KnowledgeBaseView:
    """T2：更新知识库（部分更新；改绑维度前置校验 INV-D24）。"""
    service = _get_service()
    entity = service.update_knowledge_base(
        knowledge_base_id,
        name=request.name,
        default_chunk_strategy=request.default_chunk_strategy,
        embedding_model=request.embedding_model,
    )
    return service.knowledge_base_view(entity)


@router.delete(
    "/knowledge-bases/{knowledge_base_id}",
    response_model=DeleteKnowledgeBaseResponse,
)
async def delete_knowledge_base(
    knowledge_base_id: str,
    purge_vectors: bool = Query(default=False),
) -> DeleteKnowledgeBaseResponse:
    """T2：删除知识库（默认仅元数据级联；purge_vectors=true 清向量记录）。"""
    return _get_service().delete_knowledge_base(
        knowledge_base_id, purge_vectors=purge_vectors
    )


# ---------------------------------------------------------------------------
# T3：文档导入（multipart；python-multipart 可选依赖条件注册）
# ---------------------------------------------------------------------------

if MULTIPART_AVAILABLE:

    @router.post(
        "/knowledge-bases/{knowledge_base_id}/documents",
        response_model=ImportDraftResponse,
        response_model_exclude_none=True,
    )
    async def import_document(
        knowledge_base_id: str,
        file: UploadFile = File(...),
        strategy: Optional[ChunkStrategy] = Form(default=None),
        max_chunk_length: Optional[int] = Form(default=None),
        overlap: Optional[int] = Form(default=None),
        similarity_threshold: Optional[float] = Form(default=None),
        hierarchy: Optional[List[str]] = Form(default=None),
        annotation_origin: Optional[str] = Form(default=None),
        annotation_published_at: Optional[str] = Form(default=None),
        annotation_custom: Optional[str] = Form(default=None),
    ) -> ImportDraftResponse:
        """T3：受理文档导入（解析 → 分片 → 草稿 + 作业 pending，零写存储）。

        vkb_doc_annotation（CHG-05）：追加可选标注表单字段（annotation_custom
        为 JSON 对象字符串）；后台渠道来源必填由受理期装配单点拒绝（AC-11）。
        """
        data = await file.read()
        parsed_custom: Optional[dict] = None
        if annotation_custom:
            try:
                loaded = json.loads(annotation_custom)
            except ValueError:
                raise WorkflowValidationError(
                    VECTOR_KB_ANNOTATION_INVALID,
                    "annotation_custom 非法 JSON（须为对象字符串）",
                ) from None
            if not isinstance(loaded, dict):
                raise WorkflowValidationError(
                    VECTOR_KB_ANNOTATION_INVALID,
                    "annotation_custom 须为 JSON 对象",
                )
            parsed_custom = loaded
        return _get_service().import_document(
            knowledge_base_id,
            file.filename or "upload",
            data,
            strategy=strategy,
            max_chunk_length=max_chunk_length,
            overlap=overlap,
            similarity_threshold=similarity_threshold,
            hierarchy=hierarchy,
            annotation_origin=annotation_origin,
            annotation_published_at=annotation_published_at,
            annotation_custom=parsed_custom,
        )


# ---------------------------------------------------------------------------
# T4：确认导入；摄取作业查询（kb 归属域）
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/confirm",
    response_model=IngestionReportReport,
    response_model_exclude_none=True,
)
async def confirm_import(
    knowledge_base_id: str,
    document_id: str,
    request: ConfirmImportRequest,
) -> IngestionReportReport:
    """T4：确认导入（向量化 + 委托写入；快照版本乐观并发 → 409）。"""
    return _get_service().confirm_import(
        knowledge_base_id,
        document_id,
        request.job_id,
        snapshot_version=request.snapshot_version,
        allow_degraded_fallback=request.allow_degraded_fallback,
    )


@router.get(
    "/knowledge-bases/{knowledge_base_id}/jobs/{job_id}",
    response_model=IngestionJob,
    response_model_exclude_none=True,
)
async def get_ingestion_job(
    knowledge_base_id: str, job_id: str
) -> IngestionJob:
    """摄取作业查询（未知/归属不符 → 404 VECTOR_KB_UNKNOWN_INGESTION_JOB）。"""
    return _get_service().get_ingestion_job(knowledge_base_id, job_id)


# ---------------------------------------------------------------------------
# T5 / T6 / T7：分片列表 / 调整草稿 / 确认调整
# ---------------------------------------------------------------------------


@router.get(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunks",
    response_model=ChunkListResponse,
    response_model_exclude_none=True,
)
async def list_chunks(
    knowledge_base_id: str,
    document_id: str,
    page: int = Query(default=1),
    page_size: int = Query(default=50),
    include_vector_status: bool = Query(default=True),
) -> ChunkListResponse:
    """T5：分片列表（分页只读；vectorized/degraded 计数为全量口径）。"""
    return _get_service().list_chunks(
        knowledge_base_id,
        document_id,
        page=page,
        page_size=page_size,
        include_vector_status=include_vector_status,
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunk-drafts",
    response_model=ChunkDraftResponse,
    response_model_exclude_none=True,
)
async def apply_chunk_draft(
    knowledge_base_id: str,
    document_id: str,
    request: ChunkDraftRequest,
) -> ChunkDraftResponse:
    """T6：提交调整草稿（merge/split/edit → 草稿不生效，检索零变化）。"""
    return _get_service().apply_chunk_draft(
        knowledge_base_id,
        document_id,
        request.base_version,
        request.operations,
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunk-drafts/confirm",
    response_model=ChunkDraftConfirmResponse,
    response_model_exclude_none=True,
)
async def confirm_chunk_draft(
    knowledge_base_id: str,
    document_id: str,
    request: ConfirmChunkDraftRequest,
) -> ChunkDraftConfirmResponse:
    """T7：确认调整（仅重算受影响分片；幂等更新/删除，INV-D20/INV-D21）。"""
    return _get_service().confirm_chunk_draft(
        knowledge_base_id, document_id, request.draft_version
    )


# ---------------------------------------------------------------------------
# T8 / T9：Embedding 候选（零凭据）；集合诊断（只读透传）
# ---------------------------------------------------------------------------


@router.get(
    "/embedding-models",
    response_model=EmbeddingCandidatesResponse,
    response_model_exclude_none=True,
)
async def list_embedding_models() -> EmbeddingCandidatesResponse:
    """T8：Embedding 模型候选（settings 即时派生；空候选 → 允许手工填写）。"""
    return _get_service().list_embedding_candidates()


@router.get(
    "/knowledge-bases/{knowledge_base_id}/diagnosis",
    response_model=DiagnosticReport,
    response_model_exclude_none=True,
)
async def diagnose_collection(knowledge_base_id: str) -> DiagnosticReport:
    """T9：集合诊断（只读透传 diagnose；集合缺失 → ok=False 报告，不抛）。"""
    return _get_service().diagnose_collection(knowledge_base_id)


# ---------------------------------------------------------------------------
# 文档标注端点（vkb_doc_annotation / CHG-05 / CHG-13；独立路由器独立门控）
# ---------------------------------------------------------------------------

# 独立路由器（复用 /kb-management 前缀）：由 application_factory 按
# settings.vector_kb_annotation_enabled 条件注册（未启用 → 404，AC-61）。
# 既有 router 与其端点**零改动**（G-02/G-04）。
annotation_router = APIRouter(prefix="/kb-management", tags=["向量知识库标注"])


@annotation_router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/annotate",
    response_model=BulkAnnotateReport,
    response_model_exclude_none=True,
)
async def bulk_annotate(
    knowledge_base_id: str,
    document_id: str,
    request: BulkAnnotateInput,
) -> BulkAnnotateReport:
    """一键标注当前文档全部切片（AC-22；部分失败 → partial + 失败清单）。"""
    return _get_service().bulk_annotate(knowledge_base_id, document_id, request)


@annotation_router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/lowercase",
    response_model=LowercaseReport,
    response_model_exclude_none=True,
)
async def lowercase_all(
    knowledge_base_id: str,
    document_id: str,
    request: LowercaseRequest,
) -> LowercaseReport:
    """一键统一小写（不可逆；须 confirmed=True，AC-29）。"""
    return _get_service().lowercase_all(knowledge_base_id, document_id, request)


@annotation_router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/annotation-draft",
    response_model=ChunkDraftResponse,
    response_model_exclude_none=True,
)
async def apply_annotation_draft(
    knowledge_base_id: str,
    document_id: str,
    request: AnnotationDraftRequest,
) -> ChunkDraftResponse:
    """提交标注编辑草稿（未确认不生效，AC-43）。"""
    return _get_service().apply_annotation_draft(
        knowledge_base_id, document_id, request.base_version, request.patches
    )


@annotation_router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}"
    "/annotation-draft/confirm",
    response_model=ChunkDraftConfirmResponse,
    response_model_exclude_none=True,
)
async def confirm_annotation_draft(
    knowledge_base_id: str,
    document_id: str,
    request: ConfirmAnnotationDraftRequest,
) -> ChunkDraftConfirmResponse:
    """确认标注编辑草稿（幂等更新，AC-44）。"""
    return _get_service().confirm_annotation_draft(
        knowledge_base_id, document_id, request.draft_version
    )
