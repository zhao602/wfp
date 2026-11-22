# =============================================================================
# NL YAML 生成器归一化测试（tests/unit/test_nl_yaml_generator_normalization.py）
#
# 覆盖 DC-01/DC-02/DC-03：
#   YG-01 五要素齐备（inputs/outputs 存在，反解不被结构校验拦截）
#   YG-02 字段别名归一（model/prompt_template/tool_name/kind）
#   YG-03 枚举纠偏（manual_or_api→manual、vector_database→database、branch→conditional_branch）
#   YG-04 未知字段丢弃（不进入生成的 YAML）
#   YG-05 scheduled 缺 schedule → 回落 manual（避免配置校验失败）
#   YG-06 loop 缺 max_iterations → 补 10（禁止无界循环）
#   YG-07 生成 → 反解 → 拓扑校验全链路通过
# =============================================================================

import pytest
import yaml

from orchestration_platform.domain.models.enums import IntentType, NodeType
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.governance.nl_orchestration.models import (
    ParsedEdgeSpec,
    ParsedIntent,
    ParsedNodeSpec,
)
from orchestration_platform.governance.nl_orchestration.yaml_generator import YamlGenerator
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

_REQUIRED_TOP_LEVEL = (
    "definition_id",
    "name",
    "spec_version",
    "definition_version",
    "nodes",
    "edges",
    "inputs",
    "outputs",
)


def _intent(nodes, edges=None, name="测试工作流") -> ParsedIntent:
    return ParsedIntent(
        intent_type=IntentType.CREATE_WORKFLOW,
        workflow_name=name,
        nodes=nodes,
        edges=edges or [],
    )


def _node(node_id, node_type, configuration=None, name="") -> ParsedNodeSpec:
    return ParsedNodeSpec(
        node_id=node_id,
        name=name or node_id,
        node_type=node_type,
        configuration=configuration or {},
    )


def _doc(intent: ParsedIntent) -> dict:
    return yaml.safe_load(YamlGenerator().generate(intent))


class TestFiveElements:
    """YG-01：五要素齐备。"""

    def test_includes_inputs_and_outputs(self):
        intent = _intent([_node("start", NodeType.TRIGGER)])
        document = _doc(intent)
        assert document["inputs"] == []
        assert document["outputs"] == []
        for key in _REQUIRED_TOP_LEVEL:
            assert key in document, f"缺少顶层要素 {key}"

    def test_generated_yaml_parses_structurally(self):
        intent = _intent(
            [
                _node("start", NodeType.TRIGGER),
                _node("a1", NodeType.AGENT),
            ]
        )
        # 无 edges 时拓扑不通过，但结构层必须能反解
        assert YamlWorkflowParser().parse_document(YamlGenerator().generate(intent))


class TestFieldAliases:
    """YG-02：字段别名归一为真实字段名。"""

    def test_agent_model_and_prompt_aliases(self):
        intent = _intent(
            [
                _node("start", NodeType.TRIGGER),
                _node(
                    "a1",
                    NodeType.AGENT,
                    {"model": "gpt-4o-mini", "prompt_template": "请总结"},
                ),
            ]
        )
        config = _doc(intent)["nodes"][1]["configuration"]
        assert config["model_identifier"] == "gpt-4o-mini"
        assert config["system_prompt"] == "请总结"
        assert "model" not in config
        assert "prompt_template" not in config

    def test_tool_name_alias(self):
        intent = _intent(
            [_node("t1", NodeType.TOOL, {"tool_name": "doc_search"})]
        )
        config = _doc(intent)["nodes"][0]["configuration"]
        assert config["tool_identifier"] == "doc_search"
        assert "tool_name" not in config

    def test_control_flow_kind_alias(self):
        intent = _intent(
            [_node("c1", NodeType.CONTROL_FLOW, {"kind": "conditional_branch"})]
        )
        config = _doc(intent)["nodes"][0]["configuration"]
        assert config["control_flow_kind"] == "conditional_branch"
        assert "kind" not in config

    def test_real_field_wins_over_alias(self):
        intent = _intent(
            [
                _node(
                    "a1",
                    NodeType.AGENT,
                    {"model": "alias-model", "model_identifier": "real-model"},
                )
            ]
        )
        config = _doc(intent)["nodes"][0]["configuration"]
        assert config["model_identifier"] == "real-model"


class TestEnumNormalization:
    """YG-03：枚举纠偏。"""

    @pytest.mark.parametrize(
        "raw,extra,expected",
        [
            ("manual_or_api", {}, "manual"),
            ("cron", {"schedule": "* * * * *"}, "scheduled"),
            ("webhook", {"event_source": "order_created"}, "data_event"),
        ],
    )
    def test_trigger_type_synonyms(self, raw, extra, expected):
        intent = _intent(
            [_node("start", NodeType.TRIGGER, {"trigger_type": raw, **extra})]
        )
        config = _doc(intent)["nodes"][0]["configuration"]
        assert config["trigger_type"] == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [("vector_database", "database"), ("postgres", "database"), ("http", "rest_api")],
    )
    def test_source_type_synonyms(self, raw, expected):
        intent = _intent(
            [
                _node(
                    "d1",
                    NodeType.DATA_SOURCE,
                    {"source_type": raw, "connection_reference": "conn", "query": "SELECT 1"},
                )
            ]
        )
        config = _doc(intent)["nodes"][0]["configuration"]
        assert config["source_type"] == expected

    def test_control_flow_kind_synonyms(self):
        intent = _intent(
            [_node("c1", NodeType.CONTROL_FLOW, {"control_flow_kind": "branch"})]
        )
        assert (
            _doc(intent)["nodes"][0]["configuration"]["control_flow_kind"]
            == "conditional_branch"
        )

    def test_illegal_enum_falls_back_to_default(self):
        intent = _intent(
            [_node("start", NodeType.TRIGGER, {"trigger_type": "totally_unknown"})]
        )
        assert (
            _doc(intent)["nodes"][0]["configuration"]["trigger_type"] == "manual"
        )


class TestUnknownFieldDropping:
    """YG-04：未知字段不进入 YAML。"""

    def test_unknown_fields_dropped(self):
        intent = _intent(
            [_node("a1", NodeType.AGENT, {"model_identifier": "m", "bogus": 1})]
        )
        config = _doc(intent)["nodes"][0]["configuration"]
        assert "bogus" not in config


class TestGuardrails:
    """YG-05 / YG-06：前置条件兜底。"""

    def test_scheduled_without_schedule_falls_back_to_manual(self):
        intent = _intent(
            [_node("start", NodeType.TRIGGER, {"trigger_type": "scheduled"})]
        )
        assert (
            _doc(intent)["nodes"][0]["configuration"]["trigger_type"] == "manual"
        )

    def test_data_event_without_event_source_falls_back_to_manual(self):
        intent = _intent(
            [_node("start", NodeType.TRIGGER, {"trigger_type": "data_event"})]
        )
        assert (
            _doc(intent)["nodes"][0]["configuration"]["trigger_type"] == "manual"
        )

    def test_loop_gets_bounded_max_iterations(self):
        intent = _intent(
            [_node("c1", NodeType.CONTROL_FLOW, {"control_flow_kind": "loop"})]
        )
        assert _doc(intent)["nodes"][0]["configuration"]["max_iterations"] == 10


class TestEndToEndChain:
    """YG-07：生成 → 反解 → 拓扑校验全链路。"""

    def test_chain_is_valid(self):
        intent = _intent(
            [
                _node("start", NodeType.TRIGGER, {"trigger_type": "manual"}),
                _node(
                    "retrieve",
                    NodeType.TOOL,
                    {"tool_name": "doc_search", "argument_mapping": {"q": "start.query"}},
                ),
                _node(
                    "summarize",
                    NodeType.AGENT,
                    {"model": "gpt-4o-mini", "prompt_template": "总结检索结果"},
                ),
                _node("show", NodeType.OUTPUT_DISPLAY, {"input_reference": "summarize"}),
            ],
            [
                ParsedEdgeSpec(source_node_id="start", target_node_id="retrieve"),
                ParsedEdgeSpec(source_node_id="retrieve", target_node_id="summarize"),
                ParsedEdgeSpec(source_node_id="summarize", target_node_id="show"),
            ],
        )
        definition = YamlWorkflowParser().parse(YamlGenerator().generate(intent))
        report = GraphCompiler().validate_topology(definition)
        assert report.valid, [issue.message for issue in report.issues]
        assert [node.node_type for node in definition.nodes] == [
            NodeType.TRIGGER,
            NodeType.TOOL,
            NodeType.AGENT,
            NodeType.OUTPUT_DISPLAY,
        ]
