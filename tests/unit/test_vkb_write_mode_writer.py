# =============================================================================
# vkb_write_mode 写入门面单测（tests/unit/test_vkb_write_mode_writer.py）
#
# 覆盖 design §2.6.1 第③④⑤⑥⑦⑧类（门面/分片复用/幂等/降级/报告/凭据）：
#   FR-10~FR-20，AC-22~AC-44/AC-46/AC-48~AC-50。
# 驱动：同步（TextWriter.write_text 直接调用）；真实 sqlite3 临时存储根；
#       注入桩 provider_factory（None）触发确定性降级路径。
#
# 沙箱边界：本文件为交付物；运行命令见 tasks.md 附录 B。
# =============================================================================

from __future__ import annotations

import json

import pytest

from orchestration_platform.domain.errors import (
    VECTOR_KB_EMBEDDING_SOURCE_MISSING,
    VECTOR_KB_EMBEDDING_UNAVAILABLE,
    VECTOR_KB_EMPTY_CONTENT,
    VECTOR_KB_WRITE_TEXT_TOO_LARGE,
    DomainError,
)
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

TEXT = "第一段文字。第二段有更多内容。第三段结尾。"


@pytest.fixture()
def env(tmp_path):
    kb_cfg = VectorKbManagementConfig(enabled=True, storage_root=str(tmp_path))
    lvd_cfg = LocalVectorDatabaseConfig(
        enabled=True, storage_root=str(tmp_path), default_dimension=128
    )
    svc = LocalVectorDatabaseService(lvd_cfg)
    writer = TextWriter(
        config=kb_cfg, vector_service=svc, provider_factory=lambda b, d: None
    )
    return writer, svc


def _binding(fallback: bool = True) -> KBEmbeddingBinding:
    return KBEmbeddingBinding(
        mode="api",
        model_identifier="demo",
        base_url="http://example",
        allow_degraded_fallback=fallback,
    )


class TestTextWriterBasics:
    def test_write_success_and_report(self, env) -> None:
        writer, _svc = env
        out = writer.write_text("kb1", _binding(True), TEXT, "kb1:n1")
        assert out.status == "succeeded"
        assert out.chunk_count >= 1
        assert out.upserted_count == out.chunk_count
        assert out.document_id == "kb1:n1"
        assert out.failed == []
        assert out.failed_truncated is False
        assert out.duration_ms >= 0

    def test_idempotent_same_document_id(self, env) -> None:
        writer, svc = env
        writer.write_text("kb1", _binding(True), TEXT, "kb1:n1")
        first = svc.diagnose("kb1").record_count
        writer.write_text("kb1", _binding(True), TEXT, "kb1:n1")
        second = svc.diagnose("kb1").record_count
        assert first == second
        assert first >= 1

    def test_custom_document_id_appends(self, env) -> None:
        writer, svc = env
        writer.write_text("kb1", _binding(True), TEXT, "kb1:n1")
        first = svc.diagnose("kb1").record_count
        writer.write_text("kb1", _binding(True), TEXT, "kb1:n2")
        second = svc.diagnose("kb1").record_count
        assert second > first


class TestTextWriterDegradation:
    def test_no_source_without_fallback_raises_and_writes_nothing(self, env) -> None:
        writer, svc = env
        with pytest.raises(DomainError) as exc:
            writer.write_text("kb1", _binding(False), TEXT, "kb1:n1")
        assert exc.value.code == VECTOR_KB_EMBEDDING_UNAVAILABLE
        assert svc.diagnose("kb1").record_count == 0

    def test_fallback_marks_degraded(self, env) -> None:
        writer, _svc = env
        out = writer.write_text("kb1", _binding(True), TEXT, "kb1:n1")
        assert out.local_degraded is True
        assert out.provenance == "hash_fallback"
        assert out.local_degraded_reason


class TestTextWriterPreconditions:
    def test_blank_text_rejected(self, env) -> None:
        writer, _svc = env
        with pytest.raises(DomainError) as exc:
            writer.write_text("kb1", _binding(True), "   ", "kb1:n1")
        assert exc.value.code == VECTOR_KB_EMPTY_CONTENT

    def test_over_limit_rejected(self, env) -> None:
        writer, _svc = env
        with pytest.raises(DomainError) as exc:
            writer.write_text("kb1", _binding(True), "a" * 500001, "kb1:n1")
        assert exc.value.code == VECTOR_KB_WRITE_TEXT_TOO_LARGE

    def test_semantic_without_source_reports_missing(self, env) -> None:
        writer, _svc = env
        with pytest.raises(DomainError) as exc:
            writer.write_text(
                "kb1", _binding(True), TEXT, "kb1:n1", strategy="semantic"
            )
        assert exc.value.code == VECTOR_KB_EMBEDDING_SOURCE_MISSING


class TestTextWriterStrategy:
    def test_explicit_strategy_applied(self, env) -> None:
        writer, _svc = env
        out = writer.write_text(
            "kb1", _binding(True), TEXT, "kb1:n1", strategy="fixed_length"
        )
        assert out.status == "succeeded"
        assert out.chunk_count >= 1

    def test_invalid_chunk_params_rejected(self, env) -> None:
        writer, _svc = env
        with pytest.raises(DomainError) as exc:
            writer.write_text(
                "kb1",
                _binding(True),
                TEXT,
                "kb1:n1",
                max_chunk_length=500,
                overlap=600,
            )
        assert exc.value.code == "VECTOR_KB_INVALID_CHUNK_PARAMS"


class TestTextWriterReport:
    def test_report_has_no_credentials(self, env) -> None:
        writer, _svc = env
        out = writer.write_text("kb1", _binding(True), TEXT, "kb1:n1")
        flat = json.dumps(out.model_dump(mode="json"), ensure_ascii=False)
        assert "api_key" not in flat
        assert "secret" not in flat
        assert "http://example" not in flat