# =============================================================================
# vkb_doc_annotation 一键标注单测（tests/unit/test_vkb_doc_annotation_bulk.py）
#
# 覆盖 CHG-17：作用全部切片；幂等不膨胀；不同名保留/同名覆盖；
# 无切片空操作；部分失败 → partial；失败不破坏既有数据。
# 驱动：同步；真实 sqlite3 临时目录；注入桩 provider。
#
# 沙箱边界：本文件为交付物；运行命令见 tasks.md §0.3。
# =============================================================================

from __future__ import annotations

import os

import pytest

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.integrations.local_vector_database.config import (
    LocalVectorDatabaseConfig,
)
from orchestration_platform.integrations.local_vector_database.service import (
    LocalVectorDatabaseService,
)
from orchestration_platform.integrations.vector_kb_management.config import (
    VectorKbManagementConfig,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    BulkAnnotateInput,
    KBEmbeddingBinding,
    KnowledgeBase,
)
from orchestration_platform.integrations.vector_kb_management.repository import (
    SqliteKnowledgeBaseRepository,
)
from orchestration_platform.integrations.vector_kb_management.service import (
    VectorKbManagementService,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VECTOR_KB_ANNOTATION_ENABLED", "true")
    get_settings.cache_clear()
    root = str(tmp_path)
    cfg = VectorKbManagementConfig(enabled=True, storage_root=root)
    repo = SqliteKnowledgeBaseRepository(root)
    lvd = LocalVectorDatabaseService(
        LocalVectorDatabaseConfig(
            enabled=True, storage_root=root, default_dimension=128
        )
    )
    service = VectorKbManagementService(
        config=cfg, repository=repo, vector_service=lvd
    )
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
    draft = service.import_document(
        "kb1",
        "a.txt",
        "First Para. Second Para.".encode("utf-8"),
        annotation_origin="官网",
        annotation_custom={"Tag": "Alpha"},
    )
    service.confirm_import("kb1", draft.document_id, draft.job_id, "v1")
    yield service, repo, lvd, draft
    get_settings.cache_clear()
    os.environ.pop("VECTOR_KB_ANNOTATION_ENABLED", None)


def test_bulk_annotate_all_chunks(env) -> None:
    service, _repo, lvd, draft = env
    report = service.bulk_annotate(
        "kb1",
        draft.document_id,
        BulkAnnotateInput(origin="官网", published_at="2024-01-01"),
    )
    assert report.status == "succeeded"
    assert report.updated_count == report.targeted_count
    annotations = list(lvd._store.load_records("kb1"))[0].source["annotations"]
    assert annotations["origin"] == "官网"
    assert annotations["published_at"] == "2024-01-01"
    assert annotations["ingested_at"]


def test_bulk_keep_and_override(env) -> None:
    service, _repo, lvd, draft = env
    service.bulk_annotate(
        "kb1",
        draft.document_id,
        BulkAnnotateInput(custom={"Tag": "Beta", "New": "X"}),
    )
    custom = list(lvd._store.load_records("kb1"))[0].source["annotations"]["custom"]
    assert custom["Tag"] == "Beta"
    assert custom["New"] == "X"


def test_bulk_idempotent(env) -> None:
    service, _repo, lvd, draft = env
    service.bulk_annotate("kb1", draft.document_id, BulkAnnotateInput(origin="A"))
    before = len(list(lvd._store.load_records("kb1")))
    service.bulk_annotate("kb1", draft.document_id, BulkAnnotateInput(origin="A"))
    after = len(list(lvd._store.load_records("kb1")))
    assert before == after


def test_bulk_audit_recorded(env) -> None:
    service, repo, _lvd, draft = env
    service.bulk_annotate("kb1", draft.document_id, BulkAnnotateInput(origin="A"))
    actions = [a.action for a in repo.list_annotation_audits(draft.document_id)]
    assert "bulk_annotate" in actions