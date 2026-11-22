# =============================================================================
# vkb_write_mode 执行器编排单测（tests/unit/test_vkb_write_mode_executor.py）
#
# 覆盖 design §2.6.1 第②③类（模式分派 / 文本装配 / 绑定解析 / 裁决）：
#   FR-06~FR-09/FR-24，AC-01/AC-02/AC-05/AC-13~AC-21/AC-36~AC-38/AC-56/AC-57。
# 驱动：asyncio.run（与既有执行器测试范式一致）；注入桩 TextWriter 规避触盘。
#
# 沙箱边界：本文件为交付物；运行命令见 tasks.md 附录 B。
# =============================================================================

from __future__ import annotations

import pytest

import orchestration_platform.integrations.vector_kb_management.ingestion as ing_module
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes.base import NodeExecutionContext
from orchestration_platform.engine.nodes.vector_knowledge_base_node_executor import (
    VectorKnowledgeBaseNodeExecutor,
)
from orchestration_platform.integrations.local_vector_database.models import (
    RecordFailure,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    KBEmbeddingBinding,
    TextWriteOutcome,
)

CAPTURED: dict = {}


class _FakeWriter:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def write_text(self, **kwargs) -> TextWriteOutcome:
        CAPTURED.clear()
        CAPTURED.update(kwargs)
        return TextWriteOutcome(
            status="succeeded",
            chunk_count=2,
            upserted_count=2,
            provenance="api",
            local_degraded=False,
            duration_ms=1,
            document_id=kwargs["document_id"],
        )


class _AllFailedWriter:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def write_text(self, **kwargs) -> TextWriteOutcome:
        return TextWriteOutcome(
            status="partial",
            chunk_count=1,
            upserted_count=0,
            failed=[RecordFailure(record_id="kb1:n1:0001", reason="boom")],
            provenance="api",
            local_degraded=False,
            duration_ms=1,
            document_id=kwargs["document_id"],
        )


def _ctx(node_id: str, configuration: dict, outputs: dict | None = None):
    node = NodeDefinition(
        node_id=node_id,
        node_type=NodeType.VECTOR_KNOWLEDGE_BASE,
        output_name="report",
        configuration=configuration,
    )
    state = SharedState(instance_id="i", node_outputs=dict(outputs or {}))
    return NodeExecutionContext(
        node_definition=node, shared_state=state, instance_id="i", trace_id="t"
    )


def _run(coro):
    """同步驱动协程（规避沙箱 asyncio self-pipe socketpair 拦截）。

    ``execute`` / ``_execute_write`` 内部无真正 I/O await，一次 ``send(None)``
    即可跑完并抛出 ``StopIteration``；非沙箱环境亦可改用 ``asyncio.run``。
    """
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    raise RuntimeError("coroutine did not complete in one step")


def _write_cfg(**overrides) -> dict:
    cfg = {
        "node_type": "vector_knowledge_base",
        "knowledge_base_id": "kb1",
        "mode": "write",
        "write_source_mode": "manual",
        "write_text": "hello",
        "embedding_model": {"mode": "local", "model_identifier": "m"},
    }
    cfg.update(overrides)
    return cfg


class TestModeDispatch:
    def test_write_mode_dispatches_to_write_branch(self, monkeypatch) -> None:
        monkeypatch.setattr(ing_module, "TextWriter", _FakeWriter)
        ex = VectorKnowledgeBaseNodeExecutor()
        result = _run(ex.execute(_ctx("n1", _write_cfg())))
        assert result.success is True
        assert result.output_name == "report"
        assert CAPTURED["document_id"] == "kb1:n1"

    def test_retrieve_mode_uses_existing_path(self) -> None:
        ex = VectorKnowledgeBaseNodeExecutor()
        cfg = {
            "node_type": "vector_knowledge_base",
            "knowledge_base_id": "kb1",
            "query_mode": "manual",
            "query_value": "q",
        }
        result = _run(ex.execute(_ctx("n1", cfg)))
        # retrieve 无绑定且无字段 → BINDING_REQUIRED（既有行为，分派正确）
        assert result.error_code == "VECTOR_KB_EMBEDDING_BINDING_REQUIRED"


class TestWriteTextAssembly:
    def test_manual_text_passthrough(self, monkeypatch) -> None:
        monkeypatch.setattr(ing_module, "TextWriter", _FakeWriter)
        ex = VectorKnowledgeBaseNodeExecutor()
        result = _run(ex.execute(_ctx("n1", _write_cfg(write_text="原文逐字符"))))
        assert result.success is True
        assert CAPTURED["text"] == "原文逐字符"

    def test_reference_mode_resolves_upstream(self, monkeypatch) -> None:
        monkeypatch.setattr(ing_module, "TextWriter", _FakeWriter)
        ex = VectorKnowledgeBaseNodeExecutor()
        cfg = _write_cfg(
            write_source_mode="reference", write_reference="up_out"
        )
        result = _run(ex.execute(_ctx("n1", cfg, {"up_out": "上游文本"})))
        assert result.success is True
        assert CAPTURED["text"] == "上游文本"

    def test_reference_missing_config_reports_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(ing_module, "TextWriter", _FakeWriter)
        ex = VectorKnowledgeBaseNodeExecutor()
        cfg = _write_cfg(write_source_mode="reference")
        result = _run(ex.execute(_ctx("n1", cfg)))
        assert result.success is False
        assert result.error_code == "NODE_INPUT_REFERENCE_MISSING"


class TestWriteBindingResolution:
    def test_missing_binding_and_field_fails(self, monkeypatch) -> None:
        monkeypatch.setattr(ing_module, "TextWriter", _FakeWriter)
        ex = VectorKnowledgeBaseNodeExecutor()
        cfg = _write_cfg()
        cfg.pop("embedding_model")
        result = _run(ex.execute(_ctx("n1", cfg)))
        assert result.success is False
        assert result.error_code == "VECTOR_KB_EMBEDDING_BINDING_REQUIRED"

    def test_binding_priority_over_node_field(self, monkeypatch) -> None:
        monkeypatch.setattr(ing_module, "TextWriter", _FakeWriter)
        ex = VectorKnowledgeBaseNodeExecutor()
        binding = KBEmbeddingBinding(mode="local", model_identifier="bound")
        monkeypatch.setattr(ex, "_try_resolve_binding", lambda kb_id: binding)
        cfg = _write_cfg(
            embedding_model={"mode": "api", "model_identifier": "field"}
        )
        result = _run(ex.execute(_ctx("n1", cfg)))
        assert result.success is True
        assert CAPTURED["binding"].model_identifier == "bound"


class TestWriteDocumentId:
    def test_default_document_id_is_kb_colon_node(self, monkeypatch) -> None:
        monkeypatch.setattr(ing_module, "TextWriter", _FakeWriter)
        ex = VectorKnowledgeBaseNodeExecutor()
        _run(ex.execute(_ctx("node_x", _write_cfg())))
        assert CAPTURED["document_id"] == "kb1:node_x"

    def test_write_document_id_overrides(self, monkeypatch) -> None:
        monkeypatch.setattr(ing_module, "TextWriter", _FakeWriter)
        ex = VectorKnowledgeBaseNodeExecutor()
        _run(ex.execute(_ctx("n1", _write_cfg(write_document_id="custom:doc"))))
        assert CAPTURED["document_id"] == "custom:doc"


class TestWriteVerdict:
    def test_all_failed_marks_node_failure(self, monkeypatch) -> None:
        monkeypatch.setattr(ing_module, "TextWriter", _AllFailedWriter)
        ex = VectorKnowledgeBaseNodeExecutor()
        result = _run(ex.execute(_ctx("n1", _write_cfg())))
        assert result.success is False
        assert result.error_code == "VECTOR_KB_STORAGE_UNAVAILABLE"

    def test_partial_success_marks_node_success(self, monkeypatch) -> None:
        class _PartialWriter:
            def __init__(self, *a, **k) -> None:
                pass

            def write_text(self, **kwargs) -> TextWriteOutcome:
                return TextWriteOutcome(
                    status="partial",
                    chunk_count=2,
                    upserted_count=1,
                    failed=[RecordFailure(record_id="c2", reason="boom")],
                    provenance="api",
                    local_degraded=False,
                    duration_ms=1,
                    document_id=kwargs["document_id"],
                )

        monkeypatch.setattr(ing_module, "TextWriter", _PartialWriter)
        ex = VectorKnowledgeBaseNodeExecutor()
        result = _run(ex.execute(_ctx("n1", _write_cfg())))
        assert result.success is True
        assert result.output_value is not None
        assert result.output_value["status"] == "partial"