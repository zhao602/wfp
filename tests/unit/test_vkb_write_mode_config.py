# =============================================================================
# vkb_write_mode 配置与兼容契约单测（tests/unit/test_vkb_write_mode_config.py）
#
# 覆盖 design §2.6.1 第①类：配置模型与旧 YAML 兼容
# （FR-01/FR-02/FR-03/FR-05，AC-01~AC-08/AC-10/AC-12）；
# 附加对称转换往返（OP-11 / design §2.2.2(5)）。
# 驱动：直接 pydantic 解析（同步，无需事件循环）。
#
# 沙箱边界：本文件为交付物；运行命令见 tasks.md 附录 B。
# =============================================================================

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from orchestration_platform.domain.models.node_configurations import (
    ModelLoadingConfig,
    NodeConfiguration,
    WriteChunkParams,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    KBEmbeddingBinding,
)
from orchestration_platform.integrations.vector_kb_management.service import (
    binding_to_model_config,
    model_config_to_binding,
)

_adapter: TypeAdapter = TypeAdapter(NodeConfiguration)


def _parse(payload: dict):
    return _adapter.validate_python(payload)


class TestConfigModelCompat:
    """① 配置模型与旧 YAML 兼容（AC-01~AC-08）。"""

    def test_mode_default_is_retrieve(self) -> None:
        cfg = _parse(
            {"node_type": "vector_knowledge_base", "knowledge_base_id": "kb1"}
        )
        assert cfg.mode == "retrieve"

    def test_legacy_yaml_without_write_fields_loads(self) -> None:
        # 旧 YAML 零迁移：缺 mode/写入字段照常解析
        cfg = _parse(
            {
                "node_type": "vector_knowledge_base",
                "knowledge_base_id": "kb_old",
                "query_mode": "manual",
                "query_value": "查询文本",
                "top_k": 3,
            }
        )
        assert cfg.mode == "retrieve"
        assert cfg.write_source_mode == "manual"
        assert cfg.write_text is None
        assert cfg.write_reference is None
        assert cfg.write_chunk_strategy is None
        assert cfg.write_chunk_params is None
        assert cfg.write_document_id is None

    def test_existing_retrieve_defaults_unchanged(self) -> None:
        cfg = _parse(
            {"node_type": "vector_knowledge_base", "knowledge_base_id": "kb1"}
        )
        assert cfg.query_mode == "manual"
        assert cfg.top_k == 5
        assert cfg.embedding_model is None
        assert cfg.rerank_model is None

    def test_write_mode_full_fields(self) -> None:
        cfg = _parse(
            {
                "node_type": "vector_knowledge_base",
                "knowledge_base_id": "kb1",
                "mode": "write",
                "write_source_mode": "manual",
                "write_text": "hello",
                "write_chunk_strategy": "recursive",
                "write_chunk_params": {"max_chunk_length": 800, "overlap": 100},
                "write_document_id": "kb1:node1",
            }
        )
        assert cfg.mode == "write"
        assert cfg.write_chunk_strategy == "recursive"
        assert cfg.write_chunk_params is not None
        assert cfg.write_chunk_params.max_chunk_length == 800
        assert cfg.write_document_id == "kb1:node1"

    def test_invalid_mode_rejected_at_parse(self) -> None:
        with pytest.raises(ValidationError):
            _parse(
                {
                    "node_type": "vector_knowledge_base",
                    "knowledge_base_id": "kb1",
                    "mode": "hips",
                }
            )

    def test_invalid_write_source_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _parse(
                {
                    "node_type": "vector_knowledge_base",
                    "knowledge_base_id": "kb1",
                    "mode": "write",
                    "write_source_mode": "auto",
                }
            )

    def test_write_chunk_params_value_domain(self) -> None:
        # 取值域由 pydantic 约束（跨字段约束由门面 validate_chunk_params 拦截）
        with pytest.raises(ValidationError):
            WriteChunkParams(max_chunk_length=10)
        with pytest.raises(ValidationError):
            WriteChunkParams(max_chunk_length=3000)
        with pytest.raises(ValidationError):
            WriteChunkParams(similarity_threshold=1.5)
        ok = WriteChunkParams(max_chunk_length=500, overlap=50)
        assert ok.max_chunk_length == 500


class TestModelConfigSymmetry:
    """对称转换往返（OP-11 / design §2.2.2(5)）。"""

    def test_binding_to_config_round_trip(self) -> None:
        binding = KBEmbeddingBinding(
            mode="api",
            model_identifier="m1",
            base_url="http://example/v1",
            timeout_seconds=10,
            device="cpu",
            allow_degraded_fallback=True,
        )
        cfg = binding_to_model_config(binding)
        back = model_config_to_binding(cfg)
        assert back.mode == binding.mode
        assert back.model_identifier == binding.model_identifier
        assert back.base_url == binding.base_url
        assert back.timeout_seconds == binding.timeout_seconds
        assert back.allow_degraded_fallback == binding.allow_degraded_fallback

    def test_model_config_to_binding_normalizes_device(self) -> None:
        cfg = ModelLoadingConfig(mode="local", model_identifier="m", device="cpu")
        binding = model_config_to_binding(cfg)
        assert binding.device == "cpu"
        assert binding.model_identifier == "m"