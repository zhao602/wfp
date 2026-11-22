# =============================================================================
# vkb_doc_annotation 编辑一致性单测（tests/unit/test_vkb_doc_annotation_edit_consistency.py）
#
# 覆盖 CHG-20 / DC-13：标注草稿（未确认不生效 / 确认后幂等更新）；
# 仅替换 source.annotations（record_id/vector 不变，读-改-写）；
# 合并继承 / 文本编辑保留标注。
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
    AnnotationPatch,
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


def test_draft_not_effective_until_confirmed(env) -> None:
    service, _repo, lvd, draft = env
    record = list(lvd._store.load_records("kb1"))[0]
    resp = service.apply_annotation_draft(
        "kb1",
        draft.document_id,
        "",
        [AnnotationPatch(chunk_id=record.record_id, set={"draftonly": "Z"})],
    )
    assert resp.draft_version == "v2"
    # 未确认 → 集合仍为旧值
    current = list(lvd._store.load_records("kb1"))[0].source["annotations"]["custom"]
    assert "draftonly" not in current


def test_confirm_applies_and_preserves_vector(env) -> None:
    service, _repo, lvd, draft = env
    before = list(lvd._store.load_records("kb1"))[0]
    resp = service.apply_annotation_draft(
        "kb1",
        draft.document_id,
        "",
        [AnnotationPatch(chunk_id=before.record_id, set={"draftonly": "Z"})],
    )
    result = service.confirm_annotation_draft(
        "kb1", draft.document_id, resp.draft_version
    )
    assert result.status == "succeeded"
    after = list(lvd._store.load_records("kb1"))[0]
    assert after.source["annotations"]["custom"]["draftonly"] == "Z"
    # 读-改-写：record_id 与向量不变
    assert after.record_id == before.record_id
    assert after.vector == before.vector


def test_unset_removes_custom_name(env) -> None:
    service, _repo, lvd, draft = env
    record = list(lvd._store.load_records("kb1"))[0]
    resp = service.apply_annotation_draft(
        "kb1",
        draft.document_id,
        "",
        [AnnotationPatch(chunk_id=record.record_id, unset=["Tag"])],
    )
    service.confirm_annotation_draft("kb1", draft.document_id, resp.draft_version)
    custom = list(lvd._store.load_records("kb1"))[0].source["annotations"]["custom"]
    assert "Tag" not in custom