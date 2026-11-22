# =============================================================================
# vkb_write_mode 分片复用单测（tests/unit/test_vkb_write_mode_chunking.py）
#
# 覆盖 design §2.6.1 第④类：分片复用与策略/参数（FR-10/FR-11/FR-12，
# AC-22~AC-29）+ 单点复用符号检查（INV-17）。
# 驱动：同步；真实 sqlite3 临时存储根；注入桩 provider_factory。
#
# 沙箱边界：本文件为交付物；运行命令见 tasks.md 附录 B。
# =============================================================================

from __future__ import annotations

import inspect

import pytest

from orchestration_platform.integrations.local_vector_database.config import (
    LocalVectorDatabaseConfig,
)
from orchestration_platform.integrations.local_vector_database.service import (
    LocalVectorDatabaseService,
)
from orchestration_platform.integrations.vector_kb_management import ingestion
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
def writer(tmp_path) -> TextWriter:
    kb_cfg = VectorKbManagementConfig(enabled=True, storage_root=str(tmp_path))
    lvd_cfg = LocalVectorDatabaseConfig(
        enabled=True, storage_root=str(tmp_path), default_dimension=128
    )
    svc = LocalVectorDatabaseService(lvd_cfg)
    return TextWriter(
        config=kb_cfg, vector_service=svc, provider_factory=lambda b, d: None
    )


def _binding() -> KBEmbeddingBinding:
    return KBEmbeddingBinding(
        mode="api",
        model_identifier="m",
        base_url="http://x",
        allow_degraded_fallback=True,
    )


class TestChunkingReuse:
    def test_default_strategy_produces_chunks(self, writer) -> None:
        out = writer.write_text(
            "kb1", _binding(), "第一段。第二段内容。", "kb1:n1"
        )
        assert out.chunk_count >= 1

    def test_fixed_length_smaller_limit_more_chunks(self, writer) -> None:
        out = writer.write_text(
            "kb1",
            _binding(),
            "a" * 300,
            "kb1:n1",
            strategy="fixed_length",
            max_chunk_length=50,
            overlap=0,
        )
        assert out.chunk_count >= 6

    def test_paragraph_strategy_splits_paragraphs(self, writer) -> None:
        text = "para one\n\npara two\n\npara three"
        out = writer.write_text(
            "kb1", _binding(), text, "kb1:n1", strategy="paragraph"
        )
        assert out.chunk_count >= 3

    def test_symbol_reuse_no_duplicate_impl(self) -> None:
        # INV-17 单点复用：门面仅调用既有工厂/协议，不复制分片/写入实现
        src = inspect.getsource(ingestion.TextWriter)
        assert "build_chunker" in src
        assert "chunk_detailed" in src
        assert "upsert_records" in src
        for forbidden in ("class ParagraphChunker", "def _build_drafts"):
            assert forbidden not in src