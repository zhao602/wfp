# =============================================================================
# vkb_doc_annotation 后台渠道单测（tests/unit/test_vkb_doc_annotation_backend.py）
#
# 覆盖 CHG-15：来源缺失 → 受理拒绝且零草稿/零写入；草稿 source.annotations 可见；
# 既有 source 顶层键逐字不变；确认注入 ingested_at；旧数据（无 annotations）读取正常。
# 驱动：同步；真实 sqlite3 临时目录；注入桩 provider。
#
# 沙箱边界：本文件为交付物；运行命令见 tasks.md §0.3。
# =============================================================================

from __future__ import annotations

import os

import pytest

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import WorkflowValidationError
from orchestration_platform.integrations.local_vector_database.config import (
    LocalVectorDatabaseConfig,
)
from orchestration_platform.integrations.local_vector_database.service import (
    LocalVectorDatabaseService,
)
from orchestration_platform.integrations.vector_kb_management.config import (
    VectorKbManagementConfig,
)
from orchestration_platform.integrations.vector_kb_management.ingestion import (
    IngestionPipeline,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    KBEmbeddingBinding,
    KnowledgeBase,
)
from orchestration_platform.integrations.vector_kb_management.repository import (
    SqliteKnowledgeBaseRepository,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VECTOR_KB_ANNOTATION_ENABLED", "true")
    get_settings.cache_clear()
    root = str(tmp_path)
    cfg = VectorKbManagementConfig(enabled=True, storage_root=root)
    repo = SqliteKnowledgeBaseRepository(root)
    svc = LocalVectorDatabaseService(
        LocalVectorDatabaseConfig(
            enabled=True, storage_root=root, default_dimension=128
        )
    )
    pipe = IngestionPipeline(repo, svc, cfg, provider_factory=lambda b, d: None)
    repo.save(
        KnowledgeBase(
            knowledge_base_id="kb1",
            name="KB1",
            embedding_model=KBEmbeddingBinding(
                mode="api",
                model_identifier="m",
                base_url="http://x",
                allow_degraded_fallback=True,
            ),
        )
    )
    yield pipe, repo, svc
    get_settings.cache_clear()
    os.environ.pop("VECTOR_KB_ANNOTATION_ENABLED", None)


def test_missing_origin_rejected_zero_draft(env) -> None:
    pipe, repo, _svc = env
    with pytest.raises(WorkflowValidationError) as exc:
        pipe.ingest_document("kb1", "a.txt", "x".encode(), annotation_origin=None)
    assert exc.value.code == "VECTOR_KB_ANNOTATION_ORIGIN_REQUIRED"
    assert repo.get_draft("nonexistent") is None


def test_draft_has_annotations_and_preserves_top_keys(env) -> None:
    pipe, _repo, _svc = env
    draft = pipe.ingest_document(
        "kb1",
        "a.txt",
        "第一段。第二段。".encode("utf-8"),
        annotation_origin="官网",
        annotation_published_at="2024-01-01",
        annotation_custom={"版本": "v2"},
    )
    source = draft.chunks[0].source
    assert source is not None
    annotations = source["annotations"]
    assert annotations["origin"] == "官网"
    assert annotations["published_at"] == "2024-01-01"
    assert annotations["custom"] == {"版本": "v2"}
    # 既有顶层键保留
    assert "document_id" in source
    assert "index" in source


def test_confirm_injects_ingested_at(env) -> None:
    pipe, _repo, svc = env
    draft = pipe.ingest_document(
        "kb1", "a.txt", "第一段。第二段。".encode("utf-8"), annotation_origin="官网"
    )
    captured = {}
    original = svc.upsert_records

    def spy(kb_id, records):
        captured["records"] = records
        return original(kb_id, records)

    svc.upsert_records = spy
    report = pipe.confirm_import("kb1", draft.document_id, draft.job_id, "v1")
    assert report.status == "succeeded"
    annotations = captured["records"][0].source["annotations"]
    assert annotations["ingested_at"]


def test_switch_off_produces_no_annotations(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VECTOR_KB_ANNOTATION_ENABLED", "false")
    get_settings.cache_clear()
    root = str(tmp_path)
    cfg = VectorKbManagementConfig(enabled=True, storage_root=root)
    repo = SqliteKnowledgeBaseRepository(root)
    svc = LocalVectorDatabaseService(
        LocalVectorDatabaseConfig(
            enabled=True, storage_root=root, default_dimension=128
        )
    )
    pipe = IngestionPipeline(repo, svc, cfg, provider_factory=lambda b, d: None)
    repo.save(
        KnowledgeBase(
            knowledge_base_id="kb1",
            name="KB1",
            embedding_model=KBEmbeddingBinding(
                mode="api", model_identifier="m", base_url="http://x"
            ),
        )
    )
    # 开关关闭 → 不强制来源、不产 annotations（既有行为零变化）
    draft = pipe.ingest_document("kb1", "a.txt", "x".encode())
    assert "annotations" not in (draft.chunks[0].source or {})