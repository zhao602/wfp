# =============================================================================
# Agent 市场与 Tool 市场领域模型测试（tests/unit/test_agent_market_models.py）
#
# 覆盖 T17.1：模型必填校验 / status 枚举取值域 / tags 去重保序 / 预设键集合校验。
# 涉及不变式：AC-2、AC-5、INV-D10。
# =============================================================================

from enum import Enum

import pytest
from pydantic import ValidationError

from orchestration_platform.domain.models.enums import NodeType, ToolPermission
from orchestration_platform.domain.models.node_configurations import AgentNodeConfiguration
from orchestration_platform.market.models import (
    AgentMarketEntry,
    AgentTemplate,
    MarketEntryStatus,
    ParamSummary,
    TemplateDoc,
    TemplateEdgePreset,
    TemplateMetric,
    TemplateNodePreset,
    TemplatePerformanceBoundaries,
    TemplatePreset,
    TemplateToolConfig,
    ToolMarketEntry,
)
from orchestration_platform.tool_discovery.models import ToolSource


def _sample_doc() -> TemplateDoc:
    return TemplateDoc(
        core_principle_flow="核心原理与执行流程",
        application_features="应用中的特性",
        applicable_scenarios="适用场景",
        advantages_limitations="优势与局限",
        comparison="与相关方案对比",
    )


def _sample_tool_config() -> TemplateToolConfig:
    return TemplateToolConfig(
        tool_types=[ToolSource.PLATFORM],
        invocation_mode="react_loop",
        parameter_constraints="依工具 input_schema 约束传入",
        retry_and_exception_policy="失败回填观察继续",
    )


def _sample_preset() -> TemplatePreset:
    return TemplatePreset(
        system_prompt="测试提示词",
        available_tools=["get_workflow"],
        max_tool_rounds=5,
    )


class TestAgentMarketEntry:
    """Agent 市场条目模型测试（spec §9.1）。"""

    def test_requires_entry_id_and_name(self):
        with pytest.raises(ValidationError):
            AgentMarketEntry(name="条目")
        with pytest.raises(ValidationError):
            AgentMarketEntry(entry_id="entry_001")

    def test_empty_entry_id_or_name_rejected(self):
        with pytest.raises(ValidationError):
            AgentMarketEntry(entry_id="", name="条目")
        with pytest.raises(ValidationError):
            AgentMarketEntry(entry_id="entry_001", name="")

    def test_minimal_entry_defaults(self):
        entry = AgentMarketEntry(entry_id="entry_001", name="条目")
        assert entry.description == ""
        assert entry.category == "general"
        assert entry.tags == []
        assert entry.status == MarketEntryStatus.DRAFT
        assert entry.source_kind == "registered"
        assert entry.preset_ref is None
        assert entry.tool_refs == []
        assert entry.created_at is not None
        assert entry.updated_at is not None

    def test_tags_dedupe_preserve_order(self):
        entry = AgentMarketEntry(
            entry_id="entry_001",
            name="条目",
            tags=["analytics", "planning", "analytics", "general", "planning"],
        )
        assert entry.tags == ["analytics", "planning", "general"]

    def test_tool_refs_elements_must_be_non_empty(self):
        with pytest.raises(ValidationError):
            AgentMarketEntry(
                entry_id="entry_001", name="条目", tool_refs=["", "get_workflow"]
            )


class TestMarketEntryStatus:
    """市场条目状态枚举测试（spec §9.1.6 / T1.1）。"""

    def test_enum_member_values(self):
        assert {status.value for status in MarketEntryStatus} == {
            "builtin",
            "available",
            "draft",
            "pending_review",
        }

    def test_enum_has_exactly_four_members(self):
        assert len(MarketEntryStatus) == 4

    def test_enum_is_str_enum(self):
        assert issubclass(MarketEntryStatus, str)
        assert issubclass(MarketEntryStatus, Enum)
        assert MarketEntryStatus.BUILTIN == "builtin"


class TestTemplatePresetKeys:
    """预设键集合校验（INV-D10：不新增 Agent 配置字段，排除 sandbox_policy_id）。"""

    def test_preset_keys_subset_of_agent_node_configuration_fields(self):
        preset_fields = set(TemplatePreset.model_fields)
        agent_config_fields = set(AgentNodeConfiguration.model_fields)
        assert preset_fields <= agent_config_fields
        assert "sandbox_policy_id" not in preset_fields


class TestTemplatePresetContextStrategy:
    """context_strategy 字段测试（差异化扩展：映射 context_engine 四种窗口策略）。"""

    def test_default_is_none(self):
        preset = TemplatePreset()
        assert preset.context_strategy is None

    def test_all_four_strategy_values_accepted(self):
        for value in (
            "sliding_window",
            "summary_compression",
            "importance_ranking",
            "structural_lossless",
        ):
            preset = TemplatePreset(context_strategy=value)
            assert preset.context_strategy == value

    def test_invalid_strategy_value_rejected(self):
        with pytest.raises(ValidationError):
            TemplatePreset(context_strategy="unknown_strategy")

    def test_field_exists_on_agent_node_configuration(self):
        """INV-D04 不变式：预设新字段必须同步存在于 AgentNodeConfiguration。"""
        assert "context_strategy" in AgentNodeConfiguration.model_fields
        agent_config = AgentNodeConfiguration(model_identifier="m")
        assert agent_config.context_strategy is None


class TestTemplateDoc:
    """五维度文档模型测试（spec §9.4 / AC-16）。"""

    _FULL_DOC = {
        "core_principle_flow": "核心原理",
        "application_features": "特性",
        "applicable_scenarios": "场景",
        "advantages_limitations": "优劣势",
        "comparison": "对比",
    }

    def test_missing_field_rejected(self):
        for field in list(self._FULL_DOC):
            incomplete = {k: v for k, v in self._FULL_DOC.items() if k != field}
            with pytest.raises(ValidationError):
                TemplateDoc(**incomplete)

    def test_empty_field_rejected(self):
        for field in list(self._FULL_DOC):
            with_empty = dict(self._FULL_DOC)
            with_empty[field] = ""
            with pytest.raises(ValidationError):
                TemplateDoc(**with_empty)


class TestTemplateToolConfig:
    """工具调用配置模型测试（spec §9.5 / T1.3）。"""

    def test_invocation_mode_domain_is_two_values(self):
        for mode in ("react_loop", "recipe_nodes"):
            config = TemplateToolConfig(
                tool_types=[ToolSource.PLATFORM],
                invocation_mode=mode,
                parameter_constraints="约束",
                retry_and_exception_policy="策略",
            )
            assert config.invocation_mode == mode
        with pytest.raises(ValidationError):
            TemplateToolConfig(
                tool_types=[ToolSource.PLATFORM],
                invocation_mode="unknown_mode",
                parameter_constraints="约束",
                retry_and_exception_policy="策略",
            )

    def test_tool_types_must_be_non_empty(self):
        with pytest.raises(ValidationError):
            TemplateToolConfig(
                tool_types=[],
                invocation_mode="react_loop",
                parameter_constraints="约束",
                retry_and_exception_policy="策略",
            )


class TestAgentTemplateTopology:
    """AgentTemplate kind 拓扑与可空整数校验测试（T1.4 / T1.5）。"""

    def _base_kwargs(self, kind, nodes, edges):
        return {
            "template_id": "test_template",
            "name": "测试模板",
            "kind": kind,
            "preset": _sample_preset(),
            "system_prompt": "测试提示词",
            "allowed_tools": ["get_workflow"],
            "doc": _sample_doc(),
            "tool_config": _sample_tool_config(),
            "nodes": nodes,
            "edges": edges,
        }

    def test_recipe_requires_at_least_two_nodes(self):
        nodes = [
            TemplateNodePreset(
                local_ref="entry", node_type=NodeType.TRIGGER, name="入口", configuration={}
            )
        ]
        with pytest.raises(ValidationError):
            AgentTemplate(**self._base_kwargs("recipe", nodes, []))

    def test_recipe_rejects_duplicate_local_ref(self):
        nodes = [
            TemplateNodePreset(
                local_ref="entry", node_type=NodeType.TRIGGER, name="入口", configuration={}
            ),
            TemplateNodePreset(
                local_ref="entry", node_type=NodeType.AGENT, name="智能体", configuration={}
            ),
        ]
        with pytest.raises(ValidationError):
            AgentTemplate(**self._base_kwargs("recipe", nodes, []))

    def test_recipe_rejects_edge_ref_outside_nodes(self):
        nodes = [
            TemplateNodePreset(
                local_ref="entry", node_type=NodeType.TRIGGER, name="入口", configuration={}
            ),
            TemplateNodePreset(
                local_ref="exit",
                node_type=NodeType.OUTPUT_DISPLAY,
                name="输出",
                configuration={},
            ),
        ]
        edges = [TemplateEdgePreset(source_ref="entry", target_ref="unknown_ref")]
        with pytest.raises(ValidationError):
            AgentTemplate(**self._base_kwargs("recipe", nodes, edges))

    def test_single_node_rejects_nodes_or_edges(self):
        nodes = [
            TemplateNodePreset(
                local_ref="entry", node_type=NodeType.TRIGGER, name="入口", configuration={}
            )
        ]
        with pytest.raises(ValidationError):
            AgentTemplate(**self._base_kwargs("single_node", nodes, []))
        edges = [TemplateEdgePreset(source_ref="a", target_ref="b")]
        with pytest.raises(ValidationError):
            AgentTemplate(**self._base_kwargs("single_node", [], edges))

    def test_single_node_valid_without_nodes_and_edges(self):
        template = AgentTemplate(**self._base_kwargs("single_node", [], []))
        assert template.nodes == []
        assert template.edges == []

    def test_nullable_int_fields_default_none(self):
        template = AgentTemplate(**self._base_kwargs("single_node", [], []))
        assert template.max_tool_rounds is None
        assert template.output_max_retries is None

    def test_max_tool_rounds_must_be_positive(self):
        kwargs = self._base_kwargs("single_node", [], [])
        kwargs["max_tool_rounds"] = 0
        with pytest.raises(ValidationError):
            AgentTemplate(**kwargs)

    def test_allowed_tools_dedupe(self):
        kwargs = self._base_kwargs("single_node", [], [])
        kwargs["allowed_tools"] = ["get_workflow", "get_instance", "get_workflow"]
        template = AgentTemplate(**kwargs)
        assert template.allowed_tools == ["get_workflow", "get_instance"]


class TestToolMarketEntry:
    """Tool 市场条目与参数摘要模型测试（spec §9.3 / T1.6）。"""

    def test_reuses_existing_enums(self):
        entry = ToolMarketEntry(
            tool_identifier="get_workflow",
            name="获取工作流",
            source=ToolSource.PLATFORM,
            permission=ToolPermission.READONLY,
        )
        assert entry.source == ToolSource.PLATFORM
        assert entry.permission == ToolPermission.READONLY
        assert entry.version == "1.0.0"
        assert entry.tags == []
        assert entry.input_schema_summary == []
        assert entry.output_schema_summary == []

    def test_requires_identifier_and_name(self):
        with pytest.raises(ValidationError):
            ToolMarketEntry(name="获取工作流", source=ToolSource.PLATFORM)
        with pytest.raises(ValidationError):
            ToolMarketEntry(tool_identifier="get_workflow", source=ToolSource.PLATFORM)

    def test_param_summary_has_no_credential_or_example_fields(self):
        assert set(ParamSummary.model_fields) == {"param", "type"}
        summary = ParamSummary(param="workflow_id", type="string")
        assert summary.param == "workflow_id"
        assert summary.type == "string"

    def test_metric_collectable_domain(self):
        assert (
            TemplateMetric(metric="tool_rounds_used", collectable="runtime").collectable
            == "runtime"
        )
        with pytest.raises(ValidationError):
            TemplateMetric(metric="bad_metric", collectable="unknown")

    def test_performance_boundaries_defaults(self):
        boundaries = TemplatePerformanceBoundaries()
        assert boundaries.max_tool_rounds is None
        assert boundaries.tool_timeout_seconds == 60
        assert boundaries.output_max_retries is None
