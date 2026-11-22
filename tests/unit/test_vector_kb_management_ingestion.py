# =============================================================================
# 向量知识库管理导入流水线测试（tests/unit/test_vector_kb_management_ingestion.py）
#
# 覆盖（CHG-19 / design T4.4、AC-01/AC-03/AC-06/AC-07/AC-09/AC-18/AC-38）：
#   - 两段式：受理产草稿零写存储 + 确认委托真实 LocalVectorDatabaseService
#     写入（INV-D19 / INV-D20）；
#   - 幂等重复确认（覆盖更新不新增重复）；
#   - 部分成功报告（失败明细原样上报，AC-09）；
#   - 无来源显式失败（EMBEDDING_UNAVAILABLE，保留旧数据不半写入，INV-D21）；
#   - 降级标注（hash_fallback + local_degraded + 原因，INV-D08）；
#   - 失败保留旧记录（重确认可恢复）；
#   - 委托层错误码分层映射（4 码映射 + 其余原样透传，T4.3 / DR-1 裁决）。
# 测试纪律：PYTHONPATH=src python -m pytest 本文件 -q；临时 storage_root
# 注入；禁 asyncio.run（全同步 API）；无网络/无真实 embedding（注入式
# provider seam，NV-02/NV-03 替代口径）。
# =============================================================================

from typing import List, Optional

import pytest

from orchestration_platform.domain.errors import (
    VECTOR_DB_DIMENSION_MISMATCH,
    VECTOR_DB_IMPORT_FILE_UNREADABLE,
    VECTOR_DB_INVALID_COLLECTION_ID,
    VECTOR_DB_INVALID_VECTOR,
    VECTOR_DB_RECORD_LIMIT_EXCEEDED,
    VECTOR_DB_STORAGE_UNAVAILABLE,
    VECTOR_KB_CHUNK_LIMIT_EXCEEDED,
    VECTOR_KB_DIMENSION_BINDING_CONFLICT,
    VECTOR_KB_DRAFT_VERSION_CONFLICT,
    VECTOR_KB_EMBEDDING_SOURCE_MISSING,
    VECTOR_KB_EMBEDDING_UNAVAILABLE,
    VECTOR_KB_INVALID_CHUNK_PARAMS,
    VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID,
    VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND,
    VECTOR_KB_STORAGE_UNAVAILABLE,
    VECTOR_KB_UNKNOWN_INGESTION_JOB,
    VECTOR_KB_UNSUPPORTED_FORMAT,
    DomainError,
    InstanceStateConflictError,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.integrations.local_vector_database.config import (
    LocalVectorDatabaseConfig,
)
from orchestration_platform.integrations.local_vector_database.embedding import (
    EmbeddingProvider,
    PROVENANCE_CALLER_SUPPLIED,
    PROVENANCE_HASH_FALLBACK,
)
from orchestration_platform.integrations.local_vector_database.service import (
    LocalVectorDatabaseService,
)
from orchestration_platform.integrations.vector_kb_management import (
    IngestionPipeline,
    KBEmbeddingBinding,
    KnowledgeBase,
    SqliteKnowledgeBaseRepository,
    VectorKbManagementConfig,
    map_delegate_error,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    VectorRetrievalError,
)

CONTENT = "第一段介绍产品定位。\n\n第二段说明使用方法。\n\n第三段列出注意事项。"


class _RecordingProvider(EmbeddingProvider):
    """确定性测试来源（向量由文本长度派生；记录 embed 调用）。"""

    provenance = PROVENANCE_CALLER_SUPPLIED
    dimension = 8

    def __init__(self) -> None:
        self.embedded: List[str] = []

    def embed(self, text: str) -> List[float]:
        self.embedded.append(text)
        return [float((len(text) % 8) + 1)] * 8


class _SwitchableProvider(EmbeddingProvider):
    """可切换失败来源（模拟来源中途不可达，验证失败不破坏）。"""

    provenance = PROVENANCE_CALLER_SUPPLIED
    dimension = 8

    def __init__(self) -> None:
        self.fail = False

    def embed(self, text: str) -> List[float]:
        if self.fail:
            raise RuntimeError("模拟 embedding 来源不可达")
        return [1.0] * 8


class _PoisonProvider(EmbeddingProvider):
    """含异常分片来源（POISON 分片返回空向量 → 委托层单条失败）。"""

    provenance = PROVENANCE_CALLER_SUPPLIED
    dimension = 8

    def embed(self, text: str) -> List[float]:
        if "POISON" in text:
            return []  # 空向量 → VECTOR_DB_INVALID_VECTOR → RecordFailure
        return [1.0] * 8


def _factory_of(provider: Optional[EmbeddingProvider]):
    """固定返回注入 provider 的工厂（None → 模拟无来源）。"""

    def factory(binding, declared_dimension):
        return provider

    return factory


def _make_pipeline(tmp_path, provider=None, config=None):
    """构造真实仓储 + 真实 LocalVectorDatabaseService 的流水线（临时落点）。"""
    repository = SqliteKnowledgeBaseRepository(str(tmp_path / "kb_meta"))
    vector_service = LocalVectorDatabaseService(
        config=LocalVectorDatabaseConfig(
            storage_root=str(tmp_path / "vectors")
        )
    )
    if config is None:
        config = VectorKbManagementConfig(
            storage_root=str(tmp_path / "kb_meta")
        )
    pipeline = IngestionPipeline(
        repository=repository,
        vector_service=vector_service,
        config=config,
        provider_factory=_factory_of(provider),
    )
    return pipeline, repository, vector_service


def _create_kb(
    repository,
    kb_id="kb_docs",
    allow_fallback=False,
    default_strategy="paragraph",
):
    knowledge_base = KnowledgeBase(
        knowledge_base_id=kb_id,
        name="测试知识库",
        embedding_model=KBEmbeddingBinding(
            mode="local",
            model_identifier="test-model",
            allow_degraded_fallback=allow_fallback,
        ),
        default_chunk_strategy=default_strategy,
    )
    repository.save(knowledge_base)
    return knowledge_base


def _ingest(pipeline, kb_id="kb_docs", content=CONTENT, **kwargs):
    file_name = kwargs.pop("file_name", "faq.md")
    return pipeline.ingest_document(
        knowledge_base_id=kb_id,
        file_name=file_name,
        data=content.encode("utf-8"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 两段式（受理 → 草稿；确认 → 写入）
# ---------------------------------------------------------------------------


def test_ingest_produces_draft_without_touching_vector_store(tmp_path) -> None:
    """受理产草稿：作业 pending + 集合零写入（AC-38 / INV-D19）。"""
    pipeline, repository, vector_service = _make_pipeline(tmp_path)
    _create_kb(repository)
    response = _ingest(pipeline)
    assert response.parse_status == "parsed"
    assert response.strategy_effective == "paragraph"
    assert response.chunk_count == 3
    assert [chunk.chunk_id for chunk in response.chunks] == [
        f"{response.document_id}:0001",
        f"{response.document_id}:0002",
        f"{response.document_id}:0003",
    ]
    job = repository.get_job(response.job_id)
    assert job is not None and job.status == "pending"
    draft = repository.get_draft(response.document_id)
    assert draft is not None and draft.draft_version == "v1"
    assert len(draft.chunks) == 3
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.record_count == 0
    assert diagnosis.integrity_check == "collection_missing"


def test_confirm_import_writes_vectors_via_real_delegate(tmp_path) -> None:
    """确认导入：向量化 + 委托真实 upsert_records，报告 succeeded（AC-06/07）。"""
    provider = _RecordingProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    _create_kb(repository)
    response = _ingest(pipeline)
    report = pipeline.confirm_import(
        "kb_docs", response.document_id, response.job_id
    )
    assert report.status == "succeeded"
    assert report.chunk_count == 3
    assert report.upserted_count == 3
    assert report.failed == []
    assert report.provenance == PROVENANCE_CALLER_SUPPLIED
    assert report.local_degraded is False
    assert report.local_degraded_reason is None
    assert len(provider.embedded) == 3
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.ok is True
    assert diagnosis.record_count == 3
    job = repository.get_job(response.job_id)
    assert job is not None and job.status == "succeeded"
    assert job.provenance == PROVENANCE_CALLER_SUPPLIED
    assert job.result is not None and job.result.upserted_count == 3


def test_reconfirm_is_idempotent_overwrite_without_duplicates(tmp_path) -> None:
    """重复确认 → record_id=chunk_id 覆盖更新，不新增重复（INV-D20）。"""
    provider = _RecordingProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    _create_kb(repository)
    response = _ingest(pipeline)
    first = pipeline.confirm_import(
        "kb_docs", response.document_id, response.job_id
    )
    assert first.status == "succeeded"
    second = pipeline.confirm_import(
        "kb_docs", response.document_id, response.job_id
    )
    assert second.status == "succeeded"
    assert second.upserted_count == 3
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.record_count == 3  # 覆盖更新，条目数不变


def test_partial_failure_reported_with_chunk_level_detail(tmp_path) -> None:
    """写入失败条目 → partial + 失败明细原样上报（AC-09）。"""
    pipeline, repository, vector_service = _make_pipeline(
        tmp_path, _PoisonProvider()
    )
    _create_kb(repository)
    content = "正常段落一。\n\nPOISON 异常段落。\n\n正常段落二。"
    response = _ingest(pipeline, content=content)
    report = pipeline.confirm_import(
        "kb_docs", response.document_id, response.job_id
    )
    assert report.status == "partial"
    assert report.chunk_count == 3
    assert report.upserted_count == 2
    assert len(report.failed) == 1
    assert report.failed[0].chunk_id == f"{response.document_id}:0002"
    assert report.failed[0].reason
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.record_count == 2
    job = repository.get_job(response.job_id)
    assert job is not None and job.status == "partial"


def test_no_source_without_fallback_fails_explicitly(tmp_path) -> None:
    """无来源且未开兜底 → EMBEDDING_UNAVAILABLE，作业 failed，零写入。"""
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider=None)
    _create_kb(repository)
    response = _ingest(pipeline)
    with pytest.raises(DomainError) as info:
        pipeline.confirm_import("kb_docs", response.document_id, response.job_id)
    assert info.value.code == VECTOR_KB_EMBEDDING_UNAVAILABLE
    job = repository.get_job(response.job_id)
    assert job is not None and job.status == "failed"
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.record_count == 0


def test_degraded_fallback_annotates_hash_provenance(tmp_path) -> None:
    """开兜底 → 哈希兜底 + local_degraded=True + 原因（INV-D08 伪语义标注）。"""
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider=None)
    _create_kb(repository, allow_fallback=True)
    response = _ingest(pipeline)
    report = pipeline.confirm_import("kb_docs", response.document_id, response.job_id)
    assert report.status == "succeeded"
    assert report.provenance == PROVENANCE_HASH_FALLBACK
    assert report.local_degraded is True
    assert report.local_degraded_reason
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.record_count == 3
    job = repository.get_job(response.job_id)
    assert job is not None
    assert job.local_degraded is True
    assert job.provenance == PROVENANCE_HASH_FALLBACK


def test_embedding_failure_preserves_old_records_and_reconfirms(tmp_path) -> None:
    """向量化失败保留旧记录（INV-D21）；failed 作业可重确认恢复。"""
    provider = _SwitchableProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    _create_kb(repository)
    first = _ingest(pipeline)
    pipeline.confirm_import("kb_docs", first.document_id, first.job_id)
    assert vector_service.diagnose("kb_docs").record_count == 3
    second = _ingest(pipeline, content="另一份文档第一段。\n\n另一份文档第二段。")
    provider.fail = True
    with pytest.raises(DomainError) as info:
        pipeline.confirm_import(
            "kb_docs", second.document_id, second.job_id
        )
    assert info.value.code == VECTOR_KB_EMBEDDING_UNAVAILABLE
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.record_count == 3  # 旧记录保留，新文档零写入
    failed_job = repository.get_job(second.job_id)
    assert failed_job is not None and failed_job.status == "failed"
    provider.fail = False
    recovered = pipeline.confirm_import(
        "kb_docs", second.document_id, second.job_id
    )
    assert recovered.status == "succeeded"
    assert vector_service.diagnose("kb_docs").record_count == 5


# ---------------------------------------------------------------------------
# semantic 受理分支（EMBEDDING_SOURCE_MISSING / 注入式来源）
# ---------------------------------------------------------------------------


def test_semantic_ingest_without_source_rejected(tmp_path) -> None:
    """semantic 无真实来源 → 受理即显式失败并引导 recursive（AC-27）。"""
    pipeline, repository, _ = _make_pipeline(tmp_path, provider=None)
    _create_kb(repository)
    with pytest.raises(WorkflowValidationError) as info:
        _ingest(pipeline, strategy="semantic")
    assert info.value.code == VECTOR_KB_EMBEDDING_SOURCE_MISSING


def test_semantic_ingest_with_injected_provider(tmp_path) -> None:
    """semantic 注入式来源 → 受理产出草稿（切分逻辑可实测，NV-03）。"""
    provider = _RecordingProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    _create_kb(repository)
    response = _ingest(
        pipeline,
        content="这是第一句。这是第二句。这是第三句。",
        strategy="semantic",
    )
    assert response.strategy_effective == "semantic"
    assert response.chunk_count >= 1
    assert provider.embedded
    assert vector_service.diagnose("kb_docs").record_count == 0


# ---------------------------------------------------------------------------
# 委托层错误码分层映射（T4.3）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_code,expected_code",
    [
        (
            VECTOR_DB_INVALID_COLLECTION_ID,
            VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID,
        ),
        (VECTOR_DB_DIMENSION_MISMATCH, VECTOR_KB_DIMENSION_BINDING_CONFLICT),
        (VECTOR_DB_RECORD_LIMIT_EXCEEDED, VECTOR_KB_CHUNK_LIMIT_EXCEEDED),
        (VECTOR_DB_STORAGE_UNAVAILABLE, VECTOR_KB_STORAGE_UNAVAILABLE),
    ],
)
def test_map_delegate_error_maps_four_codes(source_code, expected_code) -> None:
    error = map_delegate_error(VectorRetrievalError(source_code, "委托失败"))
    assert isinstance(error, DomainError)
    assert error.code == expected_code
    assert "委托失败" in error.message


def test_map_delegate_error_passes_through_unmapped_codes() -> None:
    """未映射 VECTOR_DB_* 原样透传（DR-1 裁决：不新增 EMBEDDING_INVALID_VECTOR）。"""
    error = map_delegate_error(
        VectorRetrievalError(VECTOR_DB_INVALID_VECTOR, "向量非法")
    )
    assert error.code == VECTOR_DB_INVALID_VECTOR
    error = map_delegate_error(
        VectorRetrievalError(VECTOR_DB_IMPORT_FILE_UNREADABLE, "文件不可读")
    )
    assert error.code == VECTOR_DB_IMPORT_FILE_UNREADABLE


# ---------------------------------------------------------------------------
# 受理/确认分支确定性（活动图分支）
# ---------------------------------------------------------------------------


def test_ingest_unknown_knowledge_base_rejected(tmp_path) -> None:
    pipeline, repository, _ = _make_pipeline(tmp_path)
    _create_kb(repository)
    with pytest.raises(ResourceNotFoundError) as info:
        _ingest(pipeline, kb_id="kb_missing")
    assert info.value.code == VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND


def test_ingest_invalid_knowledge_base_id_rejected(tmp_path) -> None:
    pipeline, repository, _ = _make_pipeline(tmp_path)
    _create_kb(repository)
    with pytest.raises(WorkflowValidationError) as info:
        _ingest(pipeline, kb_id="../escape")
    assert info.value.code == VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID


def test_ingest_unsupported_format_rejected(tmp_path) -> None:
    pipeline, repository, _ = _make_pipeline(tmp_path)
    _create_kb(repository)
    with pytest.raises(WorkflowValidationError) as info:
        _ingest(pipeline, file_name="archive.zip")
    assert info.value.code == VECTOR_KB_UNSUPPORTED_FORMAT


def test_ingest_invalid_chunk_params_rejected(tmp_path) -> None:
    pipeline, repository, _ = _make_pipeline(tmp_path)
    _create_kb(repository)
    with pytest.raises(WorkflowValidationError) as info:
        _ingest(pipeline, max_chunk_length=500, overlap=500)
    assert info.value.code == VECTOR_KB_INVALID_CHUNK_PARAMS


def test_ingest_chunk_limit_precheck_rejected(tmp_path) -> None:
    """分片总量预检：超上界 → CHUNK_LIMIT_EXCEEDED（§2.3.3 导入前预检）。"""
    config = VectorKbManagementConfig(
        storage_root=str(tmp_path / "kb_meta"), max_chunks_per_kb=2
    )
    pipeline, repository, _ = _make_pipeline(tmp_path, config=config)
    _create_kb(repository)
    with pytest.raises(WorkflowValidationError) as info:
        _ingest(pipeline)
    assert info.value.code == VECTOR_KB_CHUNK_LIMIT_EXCEEDED


def test_ingest_system_default_strategy_is_recursive(tmp_path) -> None:
    """知识库未设默认策略且请求未指定 → 系统默认 recursive（OP-09）。"""
    pipeline, repository, _ = _make_pipeline(tmp_path, _RecordingProvider())
    _create_kb(repository, default_strategy=None)
    response = _ingest(pipeline)
    assert response.strategy_effective == "recursive"
    assert response.chunk_count >= 1


def test_ingest_strategy_defaults_to_knowledge_base_default(tmp_path) -> None:
    """策略缺省链：请求未指定 → 知识库默认（再缺省 → 系统 recursive）。"""
    pipeline, repository, _ = _make_pipeline(tmp_path, _RecordingProvider())
    _create_kb(repository, default_strategy="fixed_length")
    response = _ingest(pipeline)
    assert response.strategy_effective == "fixed_length"


def test_confirm_snapshot_version_conflict_rejected(tmp_path) -> None:
    """乐观并发：snapshot_version 与草稿不符 → 409 冲突（DC-05/DC-06）。"""
    pipeline, repository, _ = _make_pipeline(tmp_path, _RecordingProvider())
    _create_kb(repository)
    response = _ingest(pipeline)
    with pytest.raises(InstanceStateConflictError) as info:
        pipeline.confirm_import(
            "kb_docs",
            response.document_id,
            response.job_id,
            snapshot_version="v9",
        )
    assert info.value.code == VECTOR_KB_DRAFT_VERSION_CONFLICT


def test_confirm_snapshot_version_match_accepted(tmp_path) -> None:
    pipeline, repository, _ = _make_pipeline(tmp_path, _RecordingProvider())
    _create_kb(repository)
    response = _ingest(pipeline)
    report = pipeline.confirm_import(
        "kb_docs",
        response.document_id,
        response.job_id,
        snapshot_version="v1",
    )
    assert report.status == "succeeded"


def test_confirm_unknown_job_rejected(tmp_path) -> None:
    pipeline, repository, _ = _make_pipeline(tmp_path, _RecordingProvider())
    _create_kb(repository)
    response = _ingest(pipeline)
    with pytest.raises(ResourceNotFoundError) as info:
        pipeline.confirm_import(
            "kb_docs", response.document_id, "job_missing"
        )
    assert info.value.code == VECTOR_KB_UNKNOWN_INGESTION_JOB


def test_confirm_job_document_mismatch_rejected(tmp_path) -> None:
    """作业归属不符（kb/文档）→ UNKNOWN_INGESTION_JOB（确定性拒绝）。"""
    pipeline, repository, _ = _make_pipeline(tmp_path, _RecordingProvider())
    _create_kb(repository)
    first = _ingest(pipeline)
    second = _ingest(pipeline, content="另一份文档。")
    with pytest.raises(ResourceNotFoundError) as info:
        pipeline.confirm_import(
            "kb_docs", second.document_id, first.job_id
        )
    assert info.value.code == VECTOR_KB_UNKNOWN_INGESTION_JOB
