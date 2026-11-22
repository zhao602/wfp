# =============================================================================
# 向量知识库管理知识库实体与仓储单元测试（tests/unit/test_vector_kb_management_repository.py）
#
# 覆盖（CHG-18 / design §2.6.3）：
#   - 知识库 CRUD 5（save+get round-trip / list_all / exists / delete 级联 /
#     缺失 None）
#   - 文档/草稿/作业存取 3（round-trip / 草稿多版本最新 / 作业 pending→终态）
#   - 幂等保存 1（同键覆盖；created_at 首写固化、updated_at 推进）
#   - 损坏 JSON 自愈 2（kb 绑定载荷 / 草稿 chunks_json + 作业 result_json）
#   - 路径安全 1（kb_id 白名单确定性拒绝 + 非法标识零落盘）
#   - 懒初始化（读操作零落点，INV-D23）+ 库文件损坏 → STORAGE_UNAVAILABLE
#   - 模型约束（id 白名单 / name 去空白非空 / embedding_model 单值必填 /
#     binding_summary 零凭据 / ChunkRecord 文本域 / 响应模型可序列化）
#
# 测试纪律：PYTHONPATH=src python -m pytest tests/unit/test_vector_kb_management_repository.py -q
# （临时 storage_root 注入（tmp_path），禁 asyncio.run；落点不越权（tmp_path 断言））
# =============================================================================

import os
import sqlite3
import time
from typing import Tuple

import pytest
from pydantic import ValidationError

from orchestration_platform.domain.errors import (
    VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID,
    VECTOR_KB_STORAGE_UNAVAILABLE,
    DomainError,
    WorkflowValidationError,
)
from orchestration_platform.integrations.local_vector_database.models import (
    RecordFailure,
)
from orchestration_platform.integrations.vector_kb_management import (
    CHUNK_TEXT_LIMIT,
    ChunkDraft,
    ChunkDraftState,
    ChunkDraftView,
    ChunkFailureView,
    ChunkRecord,
    ChunkStrategyConfig,
    ImportDraftResponse,
    IngestionJob,
    IngestionReportReport,
    IngestionResult,
    IKnowledgeBaseRepository,
    KBEmbeddingBinding,
    KBEmbeddingBindingSummary,
    KnowledgeBase,
    KnowledgeBaseDocument,
    SqliteKnowledgeBaseRepository,
)


def _binding(**overrides) -> KBEmbeddingBinding:
    params: dict = {"mode": "api", "model_identifier": "text-embedding-3-small"}
    params.update(overrides)
    return KBEmbeddingBinding(**params)


def _kb(kb_id: str = "product_docs", **overrides) -> KnowledgeBase:
    params: dict = {
        "knowledge_base_id": kb_id,
        "name": "产品文档库",
        "embedding_model": _binding(),
    }
    params.update(overrides)
    return KnowledgeBase(**params)


def _document(document_id: str = "doc_3c9e") -> KnowledgeBaseDocument:
    return KnowledgeBaseDocument(
        document_id=document_id,
        file_name="faq.md",
        format="md",
        parse_status="parsed",
        chunk_strategy=ChunkStrategyConfig(strategy="recursive"),
    )


def _draft(
    draft_version: str = "v1",
    document_id: str = "doc_3c9e",
    kb_id: str = "product_docs",
) -> ChunkDraftState:
    return ChunkDraftState(
        knowledge_base_id=kb_id,
        document_id=document_id,
        draft_version=draft_version,
        base_version="",
        chunks=[
            ChunkRecord(
                chunk_id=f"{document_id}:0001",
                index=0,
                text="第一段",
                source={"document_id": document_id, "strategy": "recursive"},
            ),
            ChunkRecord(
                chunk_id=f"{document_id}:0002",
                index=1,
                text="第二段",
                vectorized=True,
                provenance="api",
                local_degraded=False,
            ),
        ],
        notices=["示例提示"],
        strategy_effective="recursive",
    )


def _job(job_id: str = "job_7f1a", status: str = "pending") -> IngestionJob:
    return IngestionJob(
        job_id=job_id,
        knowledge_base_id="product_docs",
        document_id="doc_3c9e",
        status=status,
    )


def _corrupt_row(tmp_path, sql: str, params: Tuple) -> None:
    """白盒：直连库文件篡改载荷（模拟损坏 JSON，验证自愈）。"""
    db_path = os.path.join(str(tmp_path), "knowledge_bases.sqlite3")
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(sql, params)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 协议与懒初始化（INV-D23）
# ---------------------------------------------------------------------------


def test_repository_satisfies_protocol(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    assert isinstance(repo, IKnowledgeBaseRepository)


def test_reads_do_not_create_storage(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    assert repo.get("product_docs") is None
    assert repo.list_all() == []
    assert repo.exists("product_docs") is False
    assert repo.get_timestamps("product_docs") is None
    assert repo.get_document("doc_3c9e") is None
    assert repo.list_documents("product_docs") == []
    assert repo.get_draft("doc_3c9e") is None
    assert repo.get_job("job_7f1a") is None
    assert repo.delete("product_docs") is False  # 幂等删除，不建库
    assert os.listdir(str(tmp_path)) == []  # INV-D23：读操作零落点


# ---------------------------------------------------------------------------
# 知识库 CRUD
# ---------------------------------------------------------------------------


def test_save_and_get_knowledge_base_round_trip(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    repo.save(
        KnowledgeBase(
            knowledge_base_id="product_docs",
            name="产品文档库",
            embedding_model=KBEmbeddingBinding(
                mode="api",
                model_identifier="text-embedding-3-small",
                base_url="https://embed.example.internal/v1",
                timeout_seconds=30,
                device="cpu",
                allow_degraded_fallback=True,
            ),
            default_chunk_strategy="structural",
        )
    )
    loaded = repo.get("product_docs")
    assert loaded is not None
    assert loaded.name == "产品文档库"
    assert loaded.default_chunk_strategy == "structural"
    assert loaded.embedding_model.mode == "api"
    assert loaded.embedding_model.model_identifier == "text-embedding-3-small"
    # 写入形态保留 base_url（仅响应层不回显，AC-45 由 binding_summary 承担）
    assert (
        loaded.embedding_model.base_url == "https://embed.example.internal/v1"
    )
    assert loaded.embedding_model.timeout_seconds == 30
    assert loaded.embedding_model.device == "cpu"
    assert loaded.embedding_model.allow_degraded_fallback is True


def test_list_all_orders_by_id(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    repo.save(_kb("zeta_kb"))
    repo.save(_kb("alpha_kb"))
    repo.save(_kb("mid_kb"))
    ids = [kb.knowledge_base_id for kb in repo.list_all()]
    assert ids == ["alpha_kb", "mid_kb", "zeta_kb"]


def test_exists_semantics(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    assert repo.exists("product_docs") is False
    repo.save(_kb())
    assert repo.exists("product_docs") is True
    assert repo.exists("other_kb") is False


def test_delete_cascades_documents_drafts_jobs(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    repo.save(_kb())
    repo.save_document("product_docs", _document(), text="全文内容")
    repo.save_draft(_draft())
    repo.save_job(_job())
    assert repo.delete("product_docs") is True
    assert repo.get("product_docs") is None
    assert repo.get_document("doc_3c9e") is None
    assert repo.get_draft("doc_3c9e") is None
    assert repo.get_job("job_7f1a") is None
    assert repo.delete("product_docs") is False  # 幂等：再删返回 False


def test_missing_knowledge_base_returns_none(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    repo.save(_kb())
    assert repo.get("other_kb") is None
    assert repo.get_timestamps("other_kb") is None


# ---------------------------------------------------------------------------
# 幂等保存与时间戳
# ---------------------------------------------------------------------------


def test_save_is_idempotent_same_key_overwrite(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    repo.save(_kb(name="旧名称"))
    created_before = repo.get_timestamps("product_docs")
    assert created_before is not None
    time.sleep(0.01)
    repo.save(_kb(name="新名称"))
    loaded = repo.get("product_docs")
    assert loaded is not None
    assert loaded.name == "新名称"
    timestamps = repo.get_timestamps("product_docs")
    assert timestamps is not None
    created_after, updated_after = timestamps
    assert created_after == created_before[0]  # created_at 首写固化
    assert updated_after != created_after  # updated_at 推进


# ---------------------------------------------------------------------------
# 路径安全（INV-D11）
# ---------------------------------------------------------------------------


def test_invalid_knowledge_base_id_rejected_by_repository(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    kb = _kb()
    kb.knowledge_base_id = "../evil"  # 绕过模型校验，验证仓储层双保险
    with pytest.raises(WorkflowValidationError) as exc_info:
        repo.save(kb)
    assert exc_info.value.code == VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID
    with pytest.raises(WorkflowValidationError):
        repo.get("../evil")
    with pytest.raises(WorkflowValidationError):
        repo.exists("../evil")
    with pytest.raises(WorkflowValidationError):
        repo.delete("../evil")
    with pytest.raises(WorkflowValidationError):
        repo.get_timestamps("../evil")
    with pytest.raises(WorkflowValidationError):
        repo.list_documents("../evil")
    with pytest.raises(WorkflowValidationError):
        repo.save_document("../evil", _document())
    assert os.listdir(str(tmp_path)) == []  # 非法标识零落盘


# ---------------------------------------------------------------------------
# 损坏 JSON 自愈
# ---------------------------------------------------------------------------


def test_corrupt_binding_json_self_heals(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    repo.save(_kb("kb_a"))
    repo.save(_kb("kb_b"))
    _corrupt_row(
        tmp_path,
        "UPDATE knowledge_base SET embedding_binding_json = ? "
        "WHERE knowledge_base_id = ?",
        ('{"broken', "kb_a"),
    )
    assert repo.get("kb_a") is None  # 损坏行自愈为缺失（不抛错）
    assert repo.exists("kb_a") is True  # 行存在性不受载荷损坏影响
    assert [kb.knowledge_base_id for kb in repo.list_all()] == ["kb_b"]


def test_corrupt_draft_and_job_payloads_self_heal(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    repo.save(_kb())
    repo.save_draft(_draft())
    repo.save_job(_job())
    _corrupt_row(
        tmp_path,
        "UPDATE chunk_draft SET chunks_json = ? WHERE document_id = ?",
        ('[{"chunk_id": ', "doc_3c9e"),
    )
    assert repo.get_draft("doc_3c9e") is None
    assert repo.get_job("job_7f1a") is not None  # pending 作业仍可读
    _corrupt_row(
        tmp_path,
        "UPDATE ingestion_job SET result_json = ? WHERE job_id = ?",
        ('{"chunk_count": ', "job_7f1a"),
    )
    assert repo.get_job("job_7f1a") is None  # 损坏自愈为缺失


def test_garbage_db_file_raises_storage_unavailable(tmp_path) -> None:
    db_path = os.path.join(str(tmp_path), "knowledge_bases.sqlite3")
    with open(db_path, "wb") as handle:
        handle.write(b"this is not a sqlite database at all")
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    with pytest.raises(DomainError) as exc_info:
        repo.get("product_docs")
    assert exc_info.value.code == VECTOR_KB_STORAGE_UNAVAILABLE


# ---------------------------------------------------------------------------
# 文档 / 草稿 / 作业存取
# ---------------------------------------------------------------------------


def test_document_round_trip_and_text_persisted(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    repo.save(_kb())
    document = _document()
    repo.save_document("product_docs", document, text="全文内容")
    loaded = repo.get_document("doc_3c9e")
    assert loaded is not None
    assert loaded.document_id == "doc_3c9e"
    assert loaded.file_name == "faq.md"
    assert loaded.format == "md"
    assert loaded.parse_status == "parsed"
    assert loaded.chunk_strategy.strategy == "recursive"
    assert (
        repo.list_documents("product_docs")
        == [
            document.model_copy(
                update={"knowledge_base_id": "product_docs"}
            )
        ]
    )
    # 白盒：DC-03 表结构含 text 列（解析全文随文档持久化）
    db_path = os.path.join(str(tmp_path), "knowledge_bases.sqlite3")
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT text FROM knowledge_base_document WHERE document_id = ?",
            ("doc_3c9e",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "全文内容"


def test_draft_round_trip(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    repo.save(_kb())
    draft = _draft()
    repo.save_draft(draft)
    loaded = repo.get_draft("doc_3c9e")
    assert loaded == draft
    assert loaded is not None
    assert loaded.chunks[1].vectorized is True
    assert loaded.chunks[1].provenance == "api"
    assert loaded.notices == ["示例提示"]
    assert loaded.strategy_effective == "recursive"


def test_draft_latest_version_wins(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    repo.save(_kb())
    for version in ("v1", "v9", "v10"):
        repo.save_draft(_draft(draft_version=version))
    latest = repo.get_draft("doc_3c9e")
    assert latest is not None
    assert latest.draft_version == "v10"  # 数值序（非字典序 "v10" < "v9"）
    exact = repo.get_draft("doc_3c9e", "v9")
    assert exact is not None
    assert exact.draft_version == "v9"
    assert repo.get_draft("doc_3c9e", "v2") is None


def test_job_pending_then_terminal_round_trip(tmp_path) -> None:
    repo = SqliteKnowledgeBaseRepository(str(tmp_path))
    repo.save(_kb())
    repo.save_job(_job(status="pending"))
    pending = repo.get_job("job_7f1a")
    assert pending is not None
    assert pending.status == "pending"
    assert pending.result is None
    assert pending.provenance is None
    assert pending.local_degraded is False

    terminal = IngestionJob(
        job_id="job_7f1a",
        knowledge_base_id="product_docs",
        document_id="doc_3c9e",
        status="partial",
        result=IngestionResult(
            chunk_count=3,
            upserted_count=2,
            failed=[RecordFailure(record_id="doc_3c9e:0003", reason="维度不一致")],
            duration_ms=412,
        ),
        provenance="api",
        local_degraded=False,
    )
    time.sleep(0.01)
    repo.save_job(terminal)  # 同 job_id 覆盖（幂等）
    loaded = repo.get_job("job_7f1a")
    assert loaded is not None
    assert loaded.status == "partial"
    assert loaded.result is not None
    assert loaded.result.chunk_count == 3
    assert loaded.result.upserted_count == 2
    assert loaded.result.failed == [
        RecordFailure(record_id="doc_3c9e:0003", reason="维度不一致")
    ]
    assert loaded.result.duration_ms == 412
    assert loaded.provenance == "api"


# ---------------------------------------------------------------------------
# 模型约束与零凭据（INV-D10 / INV-D17）
# ---------------------------------------------------------------------------


def test_knowledge_base_id_model_constraints() -> None:
    for bad_id in ("../evil", "UPPER", "", "a" * 129, "-hyphen"):
        with pytest.raises(ValidationError):
            _kb(bad_id)


def test_knowledge_base_model_constraints() -> None:
    with pytest.raises(ValidationError):
        _kb(name="   ")  # 去空白后为空
    with pytest.raises(ValidationError):
        _kb(name="长" * 201)  # 长度上界 200
    with pytest.raises(ValidationError):
        KnowledgeBase(
            knowledge_base_id="product_docs", name="无绑定库"
        )  # embedding_model 单值必填（INV-D17）
    with pytest.raises(ValidationError):
        _kb(embedding_model=None)  # type: ignore[arg-type]
    assert _kb(name="  前后空白  ").name == "前后空白"  # 去空白归一


def test_binding_summary_is_zero_credential() -> None:
    kb = KnowledgeBase(
        knowledge_base_id="product_docs",
        name="产品文档库",
        embedding_model=KBEmbeddingBinding(
            mode="api",
            model_identifier="text-embedding-3-small",
            base_url="https://embed.example.internal/v1",
            timeout_seconds=30,
        ),
    )
    summary = kb.binding_summary()
    assert isinstance(summary, KBEmbeddingBindingSummary)
    payload = summary.model_dump()
    assert "base_url" not in payload  # 零凭据（AC-45 / INV-D10）
    assert "api_key" not in payload
    assert payload["mode"] == "api"
    assert payload["model_identifier"] == "text-embedding-3-small"
    assert payload["timeout_seconds"] == 30


def test_chunk_record_text_bounds() -> None:
    with pytest.raises(ValidationError):
        ChunkRecord(chunk_id="doc:0001", index=0, text="")
    with pytest.raises(ValidationError):
        ChunkRecord(chunk_id="doc:0001", index=0, text="字" * (CHUNK_TEXT_LIMIT + 1))
    record = ChunkRecord(
        chunk_id="doc:0001", index=0, text="字" * CHUNK_TEXT_LIMIT
    )
    assert len(record.text) == CHUNK_TEXT_LIMIT


def test_response_models_round_trip() -> None:
    assert ChunkDraftView is ChunkDraft  # 视图别名复用单分片草稿（INV-D02）
    response = ImportDraftResponse(
        job_id="job_7f1a",
        document_id="doc_3c9e",
        parse_status="parsed",
        chunk_count=1,
        chunks=[
            ChunkDraftView(
                chunk_id="doc_3c9e:0001",
                index=0,
                text="文本",
                source={"strategy": "recursive"},
            )
        ],
        notices=[],
        strategy_effective="recursive",
    )
    assert ImportDraftResponse.model_validate_json(
        response.model_dump_json()
    ) == response
    report = IngestionReportReport(
        status="partial",
        chunk_count=3,
        upserted_count=2,
        failed=[ChunkFailureView(chunk_id="doc_3c9e:0003", reason="维度不一致")],
        provenance="api",
        local_degraded=False,
        local_degraded_reason=None,
        duration_ms=412,
    )
    assert (
        IngestionReportReport.model_validate_json(report.model_dump_json())
        == report
    )
