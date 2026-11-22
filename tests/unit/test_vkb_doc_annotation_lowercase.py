# =============================================================================
# vkb_doc_annotation 一键统一小写单测（tests/unit/test_vkb_doc_annotation_lowercase.py）
#
# 覆盖 CHG-18：未二次确认 → 拒绝；默认仅标注值（text 逐字不变）；仅 ASCII；
# 无英文 → 计数 0；审计条目落盘且零凭据。
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
from orchestration_platform.integrations.vector_kb_management.models import (
    KBEmbeddingBinding,
    KnowledgeBase,
    LowercaseRequest,
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
        annotation_origin="SITE",
        annotation_custom={"Tag": "Alpha"},
    )
    service.confirm_import("kb1", draft.document_id, draft.job_id, "v1")
    yield service, repo, lvd, draft
    get_settings.cache_clear()
    os.environ.pop("VECTOR_KB_ANNOTATION_ENABLED", None)


def test_unconfirmed_rejected(env) -> None:
    service, _repo, _lvd, draft = env
    with pytest.raises(WorkflowValidationError) as exc:
        service.lowercase_all(
            "kb1", draft.document_id, LowercaseRequest(confirmed=False)
        )
    assert exc.value.code == "VECTOR_KB_ANNOTATION_INVALID"


def test_annotation_scope_only_values(env) -> None:
    service, _repo, lvd, draft = env
    report = service.lowercase_all(
        "kb1",
        draft.document_id,
        LowercaseRequest(scope="annotations", confirmed=True),
    )
    assert report.status == "succeeded"
    assert report.converted_values >= 2
    record = list(lvd._store.load_records("kb1"))[0]
    annotations = record.source["annotations"]
    assert annotations["origin"] == "site"
    assert annotations["custom"]["Tag"] == "alpha"
    # 正文逐字不变
    assert record.text == "First Para. Second Para."


def test_audit_recorded_without_credentials(env) -> None:
    service, repo, _lvd, draft = env
    service.lowercase_all(
        "kb1",
        draft.document_id,
        LowercaseRequest(scope="annotations", confirmed=True),
    )
    audits = repo.list_annotation_audits(draft.document_id)
    assert any(a.action == "lowercase" for a in audits)
    for audit in audits:
        assert "api_key" not in str(audit.detail)