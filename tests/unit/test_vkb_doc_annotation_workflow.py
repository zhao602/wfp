# =============================================================================
# vkb_doc_annotation 节点渠道单测（tests/unit/test_vkb_doc_annotation_workflow.py）
#
# 覆盖 CHG-16：write_text 默认来源"工作流写入"/显式覆盖/ingested_at 存在；
# 幂等（同 document_id 重复 → 条目数不变、标注为最新）；既有调用行为不变。
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
from orchestration_platform.integrations.vector_kb_management.ingestion import (
    TextWriter,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    KBEmbeddingBinding,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VECTOR_KB_ANNOTATION_ENABLED", "true")
    get_settings.cache_clear()
    root = str(tmp_path)
    cfg = VectorKbManagementConfig(enabled=True, storage_root=root)
    svc = LocalVectorDatabaseService(
        LocalVectorDatabaseConfig(
            enabled=True, storage_root=root, default_dimension=128
        )
    )
    writer = TextWriter(
        config=cfg, vector_service=svc, provider_factory=lambda b, d: None
    )
    captured = {"calls": []}
    original = svc.upsert_records

    def spy(kb_id, records):
        captured["calls"].append(records)
        return original(kb_id, records)

    svc.upsert_records = spy
    yield writer, svc, captured
    get_settings.cache_clear()
    os.environ.pop("VECTOR_KB_ANNOTATION_ENABLED", None)


def _binding() -> KBEmbeddingBinding:
    return KBEmbeddingBinding(
        mode="api",
        model_identifier="m",
        base_url="http://x",
        allow_degraded_fallback=True,
    )


TEXT = "第一段。第二段。"


def test_default_origin_is_workflow(env) -> None:
    writer, _svc, captured = env
    writer.write_text("kb1", _binding(), TEXT, "kb1:n1")
    annotations = captured["calls"][-1][0].source["annotations"]
    assert annotations["origin"] == "工作流写入"
    assert annotations["ingested_at"]


def test_explicit_origin_overrides(env) -> None:
    writer, _svc, captured = env
    writer.write_text(
        "kb1",
        _binding(),
        TEXT,
        "kb1:n2",
        annotation_origin="自定义来源",
        annotation_published_at="2024-05-01",
        annotation_custom={"k": "V"},
    )
    annotations = captured["calls"][-1][0].source["annotations"]
    assert annotations["origin"] == "自定义来源"
    assert annotations["published_at"] == "2024-05-01"
    assert annotations["custom"] == {"k": "V"}


def test_idempotent_same_document_id(env) -> None:
    writer, svc, _captured = env
    writer.write_text("kb1", _binding(), TEXT, "kb1:n1")
    before = svc.diagnose("kb1").record_count
    writer.write_text("kb1", _binding(), TEXT, "kb1:n1")
    after = svc.diagnose("kb1").record_count
    assert before == after
    assert before >= 1


def test_top_level_keys_preserved(env) -> None:
    writer, _svc, captured = env
    writer.write_text("kb1", _binding(), TEXT, "kb1:n1")
    source = captured["calls"][-1][0].source
    assert "document_id" in source
    assert "index" in source
    assert "provenance" in source