# =============================================================================
# 内置 Agent 模板常量测试（tests/unit/test_agent_templates.py）
#
# 覆盖 T17.2 / 必做守护 ⑤：数量恒 5 / template_id 集合恰 5 个固定值 /
# 逐模板五维度非空 / 预设键 ⊆ 白名单 / recipe 节点类型 ∈ 既有 NodeType /
# recipe 节点配置键 ⊆ 对应节点配置字段集 / 导入期校验通过（fail-fast）。
# 涉及不变式：AC-12、AC-13、AC-16、AC-18。
# =============================================================================

import pytest

from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    AgentNodeConfiguration,
    ControlFlowNodeConfiguration,
    LlmNodeConfiguration,
    OutputDisplayNodeConfiguration,
    TriggerNodeConfiguration,
    VectorKnowledgeBaseNodeConfiguration,
)
import orchestration_platform.market.agent_templates as agent_templates_module
from orchestration_platform.market.agent_templates import (
    AGENT_TEMPLATES,
    TEMPLATE_PRESET_ALLOWED_KEYS,
    _validate_templates,
    get_template,
    list_templates,
)

EXPECTED_TEMPLATE_IDS = (
    "plan_and_execute",
    "basic_react",
    "multi_agent",
    "reflexive_agent",
    "autonomous_loop",
)

_DOC_FIELDS = (
    "core_principle_flow",
    "application_features",
    "applicable_scenarios",
    "advantages_limitations",
    "comparison",
)


class TestTemplateConstants:
    """内置模板常量结构校验（AC-12 / AC-16）。"""

    def test_template_count_is_five(self):
        assert len(AGENT_TEMPLATES) == 5

    def test_template_ids_are_fixed_five_in_order(self):
        assert tuple(t.template_id for t in AGENT_TEMPLATES) == EXPECTED_TEMPLATE_IDS

    def test_template_ids_unique(self):
        template_ids = [t.template_id for t in AGENT_TEMPLATES]
        assert len(template_ids) == len(set(template_ids))

    def test_kind_distribution(self):
        kinds = {t.template_id: t.kind for t in AGENT_TEMPLATES}
        assert kinds["plan_and_execute"] == "recipe"
        assert kinds["basic_react"] == "single_node"
        assert kinds["multi_agent"] == "recipe"
        assert kinds["reflexive_agent"] == "recipe"
        assert kinds["autonomous_loop"] == "recipe"

    def test_all_doc_dimensions_non_empty(self):
        for template in AGENT_TEMPLATES:
            for field in _DOC_FIELDS:
                assert getattr(template.doc, field), (
                    f"{template.template_id}.{field} 不能为空"
                )

    def test_doc_content_has_no_code_blocks(self):
        for template in AGENT_TEMPLATES:
            for field in _DOC_FIELDS:
                assert "```" not in getattr(template.doc, field)

    def test_preset_keys_subset_of_whitelist(self):
        for template in AGENT_TEMPLATES:
            preset_keys = set(template.preset.model_dump().keys())
            assert preset_keys <= TEMPLATE_PRESET_ALLOWED_KEYS, (
                f"{template.template_id} 预设键越界"
            )

    def test_whitelist_has_exactly_eight_keys(self):
        assert len(TEMPLATE_PRESET_ALLOWED_KEYS) == 8

    def test_whitelist_contains_context_strategy(self):
        assert "context_strategy" in TEMPLATE_PRESET_ALLOWED_KEYS

    def test_whitelist_excludes_sandbox_policy_id(self):
        assert "sandbox_policy_id" not in TEMPLATE_PRESET_ALLOWED_KEYS

    def test_single_node_templates_have_empty_nodes_and_edges(self):
        for template in AGENT_TEMPLATES:
            if template.kind == "single_node":
                assert template.nodes == []
                assert template.edges == []


class TestRecipeTopology:
    """配方模板节点/连线校验（AC-14 / AC-18，INV-D03）。"""

    _FIELD_SETS = {
        NodeType.AGENT: set(AgentNodeConfiguration.model_fields),
        NodeType.TRIGGER: set(TriggerNodeConfiguration.model_fields),
        NodeType.CONTROL_FLOW: set(ControlFlowNodeConfiguration.model_fields),
        NodeType.OUTPUT_DISPLAY: set(OutputDisplayNodeConfiguration.model_fields),
        NodeType.LLM: set(LlmNodeConfiguration.model_fields),
        NodeType.VECTOR_KNOWLEDGE_BASE: set(
            VectorKnowledgeBaseNodeConfiguration.model_fields
        ),
    }

    def test_recipe_node_types_are_existing_node_types(self):
        for template in AGENT_TEMPLATES:
            for node in template.nodes:
                assert node.node_type in NodeType, (
                    f"{template.template_id}:{node.local_ref} 节点类型越界"
                )

    def test_recipe_node_config_keys_within_existing_config_fields(self):
        for template in AGENT_TEMPLATES:
            for node in template.nodes:
                assert node.node_type in self._FIELD_SETS, (
                    f"{template.template_id}:{node.local_ref} 节点类型未登记字段集"
                )
                assert set(node.configuration) <= self._FIELD_SETS[node.node_type], (
                    f"{template.template_id}:{node.local_ref} 配置键越界"
                )

    def test_recipe_agent_nodes_leave_model_identifier_empty(self):
        for template in AGENT_TEMPLATES:
            for node in template.nodes:
                if node.node_type == NodeType.AGENT:
                    assert node.configuration.get("model_identifier", "") == ""

    def test_recipe_special_nodes_leave_required_ids_empty(self):
        """LLM 节点 model_identifier 与 VectorKB 节点 knowledge_base_id 由编排者补填。"""
        for template in AGENT_TEMPLATES:
            for node in template.nodes:
                if node.node_type == NodeType.LLM:
                    assert node.configuration.get("model_identifier", "") == ""
                if node.node_type == NodeType.VECTOR_KNOWLEDGE_BASE:
                    assert node.configuration.get("knowledge_base_id", "") == ""

    def test_plan_and_execute_topology(self):
        template = get_template("plan_and_execute")
        assert template is not None
        local_refs = [n.local_ref for n in template.nodes]
        assert local_refs == ["entry", "planner", "review_gate", "executor", "exit"]
        assert template.nodes[0].node_type == NodeType.TRIGGER
        assert template.nodes[1].node_type == NodeType.AGENT
        assert template.nodes[1].configuration["available_tools"] == []
        assert template.nodes[1].configuration["output_schema"] == {"plan": "array"}
        assert template.nodes[2].node_type == NodeType.CONTROL_FLOW
        assert template.nodes[2].configuration["control_flow_kind"] == "human_review_gate"
        assert "review_timeout_action" not in template.nodes[2].configuration
        assert template.nodes[3].node_type == NodeType.AGENT
        assert template.nodes[3].configuration["max_tool_rounds"] == 8
        assert template.nodes[3].configuration["available_tools"] == [
            "get_workflow",
            "validate_workflow",
            "start_workflow",
        ]
        assert template.nodes[4].node_type == NodeType.OUTPUT_DISPLAY
        edge_pairs = {(e.source_ref, e.target_ref) for e in template.edges}
        assert edge_pairs == {
            ("entry", "planner"),
            ("planner", "review_gate"),
            ("review_gate", "executor"),
            ("executor", "exit"),
        }

    def test_reflexive_agent_topology(self):
        template = get_template("reflexive_agent")
        assert template is not None
        local_refs = [n.local_ref for n in template.nodes]
        assert local_refs == ["entry", "drafter", "critic", "reviser", "exit"]
        assert template.nodes[0].node_type == NodeType.TRIGGER
        assert template.nodes[1].node_type == NodeType.AGENT
        assert template.nodes[1].configuration["output_schema"] == {"draft": "string"}
        assert template.nodes[2].node_type == NodeType.LLM
        assert template.nodes[2].configuration["output_schema"] == {
            "issues": "array",
            "verdict": "string",
        }
        assert template.nodes[3].node_type == NodeType.AGENT
        assert template.nodes[3].configuration["output_max_retries"] == 3
        assert template.nodes[3].configuration["output_schema"] == {
            "final": "string",
            "verdict": "string",
        }
        assert template.nodes[4].node_type == NodeType.OUTPUT_DISPLAY
        edge_pairs = {(e.source_ref, e.target_ref) for e in template.edges}
        assert edge_pairs == {
            ("entry", "drafter"),
            ("drafter", "critic"),
            ("critic", "reviser"),
            ("reviser", "exit"),
        }

    def test_multi_agent_topology(self):
        template = get_template("multi_agent")
        assert template is not None
        local_refs = [n.local_ref for n in template.nodes]
        assert local_refs == [
            "entry",
            "coordinator",
            "worker_retrieval",
            "vkb_retrieve",
            "worker_analysis",
            "aggregate",
            "exit",
        ]
        assert template.nodes[0].node_type == NodeType.TRIGGER
        assert template.nodes[1].node_type == NodeType.AGENT
        assert template.nodes[2].node_type == NodeType.AGENT
        assert template.nodes[2].configuration["available_tools"] == ["get_workflow"]
        assert template.nodes[3].node_type == NodeType.VECTOR_KNOWLEDGE_BASE
        assert template.nodes[3].configuration["knowledge_base_id"] == ""
        assert template.nodes[3].configuration["query_mode"] == "manual"
        assert template.nodes[3].configuration["top_k"] == 5
        assert template.nodes[4].node_type == NodeType.AGENT
        assert template.nodes[5].node_type == NodeType.CONTROL_FLOW
        assert template.nodes[5].configuration["control_flow_kind"] == "parallel_join"
        assert template.nodes[5].configuration["parallel_branches"] == [
            "worker_retrieval",
            "vkb_retrieve",
            "worker_analysis",
        ]
        assert template.nodes[5].configuration["parallel_failure_strategy"] == "fast_fail"
        assert template.nodes[6].node_type == NodeType.OUTPUT_DISPLAY
        edge_pairs = {(e.source_ref, e.target_ref) for e in template.edges}
        assert ("entry", "coordinator") in edge_pairs
        assert ("coordinator", "worker_retrieval") in edge_pairs
        assert ("coordinator", "vkb_retrieve") in edge_pairs
        assert ("coordinator", "worker_analysis") in edge_pairs
        assert ("worker_retrieval", "aggregate") in edge_pairs
        assert ("vkb_retrieve", "aggregate") in edge_pairs
        assert ("worker_analysis", "aggregate") in edge_pairs
        assert ("aggregate", "coordinator") in edge_pairs
        assert ("coordinator", "exit") in edge_pairs

    def test_autonomous_loop_topology(self):
        template = get_template("autonomous_loop")
        assert template is not None
        local_refs = [n.local_ref for n in template.nodes]
        assert local_refs == ["entry", "loop_gate", "loop_agent", "exit"]
        assert template.nodes[0].node_type == NodeType.TRIGGER
        assert template.nodes[1].node_type == NodeType.CONTROL_FLOW
        assert template.nodes[1].configuration["control_flow_kind"] == "loop"
        assert template.nodes[1].configuration["loop_body_entry"] == "loop_agent"
        assert template.nodes[1].configuration["loop_termination_condition"] == (
            "output.done == true"
        )
        assert template.nodes[1].configuration["max_iterations"] == 10
        loop_agent = template.nodes[2]
        assert loop_agent.node_type == NodeType.AGENT
        assert loop_agent.configuration["max_tool_rounds"] == 10
        assert loop_agent.configuration["context_strategy"] == "summary_compression"
        assert template.nodes[3].node_type == NodeType.OUTPUT_DISPLAY
        edge_triples = [
            (e.source_ref, e.target_ref, e.condition_label) for e in template.edges
        ]
        assert ("loop_agent", "loop_gate", "未满足退出条件") in edge_triples
        assert ("loop_gate", "exit", "满足退出条件") in edge_triples


class TestTemplatePresetValues:
    """逐模板预设取值校验（AC-13 / 设计 §3.2.6 矩阵）。"""

    def test_plan_and_execute_preset(self):
        template = get_template("plan_and_execute")
        assert template is not None
        # recipe 化后：顶层不设轮次（各节点独立预算），预设承载 planner 节点约束
        assert template.max_tool_rounds is None
        assert template.allowed_tools == []
        assert template.preset.max_tool_rounds == 2
        assert template.preset.available_tools == []
        assert template.preset.output_schema == {"plan": "array"}
        assert template.output_max_retries is None
        assert template.tool_config.invocation_mode == "recipe_nodes"

    def test_basic_react_preset_defaults_to_global_rounds(self):
        template = get_template("basic_react")
        assert template is not None
        assert template.max_tool_rounds is None
        assert template.preset.max_tool_rounds is None
        assert template.preset.available_tools == []

    def test_reflexive_agent_preset(self):
        template = get_template("reflexive_agent")
        assert template is not None
        # recipe 化后：顶层不设轮次/重试（reviser 节点级承载 output_max_retries=3）
        assert template.max_tool_rounds is None
        assert template.output_max_retries is None
        assert template.preset.max_tool_rounds == 4
        assert template.preset.output_max_retries == 3
        assert template.preset.output_schema == {"final": "string", "verdict": "string"}
        assert template.tool_config.invocation_mode == "recipe_nodes"

    def test_autonomous_loop_preset(self):
        template = get_template("autonomous_loop")
        assert template is not None
        assert template.max_tool_rounds == 10
        assert template.preset.available_tools == [
            "get_workflow",
            "get_instance",
            "start_workflow",
        ]
        assert template.preset.context_strategy == "summary_compression"


class TestAccessFunctions:
    """访问函数测试（T2.4）。"""

    def test_list_templates_returns_five_in_order(self):
        templates = list_templates()
        assert len(templates) == 5
        assert [t.template_id for t in templates] == list(EXPECTED_TEMPLATE_IDS)

    def test_get_template_found(self):
        template = get_template("basic_react")
        assert template is not None
        assert template.name == "基础 ReAct"

    def test_get_template_not_found_returns_none(self):
        assert get_template("nonexistent_template") is None


class TestImportTimeValidation:
    """导入期校验测试（T2.5 / T2.6，必做守护 ⑤，fail-fast）。"""

    def test_import_time_validation_passes(self):
        _validate_templates()

    def test_fail_fast_on_count_violation(self, monkeypatch):
        monkeypatch.setattr(
            agent_templates_module, "AGENT_TEMPLATES", AGENT_TEMPLATES[:4]
        )
        with pytest.raises(RuntimeError):
            _validate_templates()

    def test_fail_fast_on_duplicate_template_id(self, monkeypatch):
        duplicated = (AGENT_TEMPLATES[0], AGENT_TEMPLATES[0]) + AGENT_TEMPLATES[2:5]
        monkeypatch.setattr(agent_templates_module, "AGENT_TEMPLATES", duplicated)
        with pytest.raises(RuntimeError):
            _validate_templates()

    def test_fail_fast_on_empty_doc_field(self, monkeypatch):
        bad_template = AGENT_TEMPLATES[0].model_copy(
            update={
                "doc": AGENT_TEMPLATES[0].doc.model_copy(
                    update={"core_principle_flow": ""}
                )
            }
        )
        monkeypatch.setattr(
            agent_templates_module,
            "AGENT_TEMPLATES",
            (bad_template,) + AGENT_TEMPLATES[1:5],
        )
        with pytest.raises(RuntimeError):
            _validate_templates()

    def test_fail_fast_on_preset_key_violation(self, monkeypatch):
        monkeypatch.setattr(
            agent_templates_module,
            "TEMPLATE_PRESET_ALLOWED_KEYS",
            frozenset({"system_prompt"}),
        )
        with pytest.raises(RuntimeError):
            _validate_templates()

    def test_fail_fast_on_config_key_violation(self, monkeypatch):
        original = AGENT_TEMPLATES[4]
        bad_agent_node = original.nodes[2].model_copy(
            update={"configuration": {"model_identifier": "", "unknown_field": "x"}}
        )
        bad_nodes = list(original.nodes)
        bad_nodes[2] = bad_agent_node
        bad_template = original.model_copy(update={"nodes": bad_nodes})
        monkeypatch.setattr(
            agent_templates_module,
            "AGENT_TEMPLATES",
            AGENT_TEMPLATES[:4] + (bad_template,),
        )
        with pytest.raises(RuntimeError):
            _validate_templates()
