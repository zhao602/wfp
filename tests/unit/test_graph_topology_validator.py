# =============================================================================
# 拓扑校验器单元测试（tests/unit/test_graph_topology_validator.py）
#
# 覆盖验收：A1（拓扑与配置校验）
# =============================================================================

import pytest

from orchestration_platform.domain.errors import WF_CYCLIC_GRAPH
from orchestration_platform.domain.validation.graph_topology_validator import (
    GraphTopologyValidator,
)
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.enums import NodeType, Severity


def _make_node(node_id: str, node_type: NodeType = NodeType.AGENT, config: dict | None = None):
    """构造测试用节点（configuration 用 dict，由 NodeDefinition model_validator 注入 node_type 判别联合）。"""
    if config is None:
        if node_type == NodeType.TRIGGER:
            config = {"trigger_type": "manual"}
        elif node_type == NodeType.AGENT:
            config = {"model_identifier": "m"}
        elif node_type == NodeType.CONTROL_FLOW:
            config = {
                "control_flow_kind": "loop",
                "loop_body_entry": "body",
                "loop_termination_condition": "true",
                "max_iterations": 5,
            }
        elif node_type == NodeType.TOOL:
            config = {"tool_identifier": "t", "tool_source": "local"}
        else:
            config = {}
    return NodeDefinition(node_id=node_id, node_type=node_type, configuration=config)


def _make_definition(
    node_specs: list,
    edge_pairs: list,
    def_id: str = "test",
) -> WorkflowDefinition:
    """构造测试用工作流定义。

    node_specs: List[(node_id, node_type)] 或 List[(node_id, node_type, config_dict)]
    edge_pairs: List[(source_node_id, target_node_id)]
    """
    nodes = []
    for spec in node_specs:
        if len(spec) == 2:
            nid, ntype = spec
            nodes.append(_make_node(nid, ntype))
        else:
            nid, ntype, cfg = spec
            nodes.append(_make_node(nid, ntype, cfg))

    edges = [
        EdgeDefinition(edge_id=f"e{i}", source_node_id=s, target_node_id=t)
        for i, (s, t) in enumerate(edge_pairs)
    ]
    return WorkflowDefinition(
        definition_id=def_id,
        name="test",
        spec_version="1.0",
        nodes=nodes,
        edges=edges,
    )


class TestGraphTopologyValidator:
    """拓扑校验器测试：环检测、连通性、入口节点。"""

    @pytest.fixture
    def validator(self):
        return GraphTopologyValidator()

    def test_sequential_graph_no_cycle(self, validator):
        """顺序图无环，校验通过。"""
        definition = _make_definition(
            [("a", NodeType.TRIGGER), ("b", NodeType.AGENT), ("c", NodeType.AGENT)],
            [("a", "b"), ("b", "c")],
        )
        issues = validator.validate(definition)
        errors = [i for i in issues if i.severity.value == "error"]
        assert len(errors) == 0

    def test_simple_cycle_detected(self, validator):
        """简单环应被检测。"""
        definition = _make_definition(
            [("a", NodeType.TRIGGER), ("b", NodeType.AGENT), ("c", NodeType.AGENT)],
            [("a", "b"), ("b", "c"), ("c", "a")],
        )
        issues = validator.validate(definition)
        errors = [i for i in issues if i.severity.value == "error"]
        assert len(errors) > 0

    def test_cycle_with_loop_node_is_valid(self, validator):
        """含循环节点的回边构成的环是合法的。"""
        definition = _make_definition(
            [
                ("trigger", NodeType.TRIGGER),
                ("loop_node", NodeType.CONTROL_FLOW),
                ("body", NodeType.AGENT),
                ("exit", NodeType.AGENT),
            ],
            [
                ("trigger", "loop_node"),
                ("loop_node", "body"),
                ("body", "loop_node"),  # 回边，但 loop_node 是循环节点
                ("loop_node", "exit"),
            ],
        )
        issues = validator.validate(definition)
        errors = [i for i in issues if i.severity.value == "error"]
        assert len(errors) == 0

    def test_disconnected_graph_warning(self, validator):
        """不连通图应产生警告或错误。"""
        definition = _make_definition(
            [
                ("a", NodeType.TRIGGER),
                ("b", NodeType.AGENT),
                ("c", NodeType.AGENT),
                ("d", NodeType.AGENT),
            ],
            [("a", "b"), ("c", "d")],
        )
        issues = validator.validate(definition)
        assert len(issues) > 0

    def test_self_loop_detected(self, validator):
        """自环应被检测。"""
        definition = _make_definition(
            [("a", NodeType.TRIGGER)],
            [("a", "a")],
        )
        issues = validator.validate(definition)
        errors = [i for i in issues if i.severity.value == "error"]
        assert len(errors) > 0


class TestReferenceArcCycleDetection:
    """引用弧合并环检测（unified_node_input T4.3/T4.4，FR-16、AC-08）。

    引用弧（输入引用 owner -> referencer）与连线弧合并后参与 DFS；
    合法回边结构内的引用环豁免，非法引用环报 WF_CYCLIC_GRAPH。
    """

    @pytest.fixture
    def validator(self):
        return GraphTopologyValidator()

    @staticmethod
    def _node_with_output(node_id, node_type, config, output_name):
        return NodeDefinition(
            node_id=node_id,
            node_type=node_type,
            configuration=config,
            output_name=output_name,
        )

    def test_reference_cycle_detected(self, validator):
        """非法引用环：a/b 互相引用对方输出且无合法回边结构 → WF_CYCLIC_GRAPH ERROR，
        消息含「由输入引用构成」前缀、环序列与引用弧描述（design DC-06#3、§9.4.3）。"""
        nodes = [
            _make_node("trig", NodeType.TRIGGER),
            self._node_with_output(
                "a",
                NodeType.TOOL,
                {"tool_identifier": "ta", "argument_mapping": {"p": "b_out"}},
                "a_out",
            ),
            self._node_with_output(
                "b",
                NodeType.TOOL,
                {"tool_identifier": "tb", "argument_mapping": {"q": "a_out"}},
                "b_out",
            ),
        ]
        edges = [
            EdgeDefinition(edge_id="e0", source_node_id="trig", target_node_id="a"),
            EdgeDefinition(edge_id="e1", source_node_id="a", target_node_id="b"),
        ]
        definition = WorkflowDefinition(
            definition_id="wf-ref-cycle",
            name="引用环检测",
            spec_version="1.0",
            nodes=nodes,
            edges=edges,
        )

        issues = validator.validate(definition)
        cycle_issues = [i for i in issues if i.code == WF_CYCLIC_GRAPH]

        assert len(cycle_issues) == 1
        issue = cycle_issues[0]
        assert issue.severity == Severity.ERROR
        assert "由输入引用构成" in issue.message
        assert "b -> a -> b" in issue.message
        assert "argument_mapping.p" in issue.message

    def test_reference_within_legal_back_edge_passes(self, validator):
        """合法回边内引用循环体输出（loop_termination_condition 引用 loop 体内 worker 输出）
        → 合法回边结构豁免，校验通过（AC-08 场景 2、FR-16 验收 2）。"""
        nodes = [
            _make_node("trig", NodeType.TRIGGER),
            self._node_with_output(
                "producer", NodeType.AGENT, {"model_identifier": "m"}, "seed_out"
            ),
            self._node_with_output(
                "loop_node",
                NodeType.CONTROL_FLOW,
                {
                    "control_flow_kind": "loop",
                    "loop_body_entry": "worker",
                    "loop_termination_condition": "worker_out.score >= 0.8",
                    "max_iterations": 5,
                },
                None,
            ),
            self._node_with_output(
                "worker", NodeType.AGENT, {"model_identifier": "m"}, "worker_out"
            ),
            self._node_with_output(
                "exit_node", NodeType.AGENT, {"model_identifier": "m"}, "exit_out"
            ),
        ]
        edges = [
            EdgeDefinition(edge_id="e0", source_node_id="trig", target_node_id="producer"),
            EdgeDefinition(edge_id="e1", source_node_id="producer", target_node_id="loop_node"),
            EdgeDefinition(edge_id="e2", source_node_id="loop_node", target_node_id="worker"),
            EdgeDefinition(edge_id="e3", source_node_id="worker", target_node_id="loop_node"),
            EdgeDefinition(edge_id="e4", source_node_id="loop_node", target_node_id="exit_node"),
        ]
        definition = WorkflowDefinition(
            definition_id="wf-ref-loop",
            name="合法回边引用",
            spec_version="1.0",
            nodes=nodes,
            edges=edges,
        )

        issues = validator.validate(definition)
        errors = [i for i in issues if i.severity == Severity.ERROR]
        assert errors == []


class TestInputPositionArcCycleDetection:
    """形态 B（input_position）引用弧合并环检测（upstream_input_ref T3.3/T3.4，
    design DC-05 / FR-15）：scope_node_id → 引用节点，与既有引用弧同向同构；
    自引用边不纳入；合法回边（环上含循环节点）豁免零改动。
    """

    @pytest.fixture
    def validator(self):
        return GraphTopologyValidator()

    def test_input_position_reference_cycle_detected(self, validator):
        """非法形态 B 引用环：a 引 b::y 且 b 引 a::x，互引构成不含循环节点的环
        → WF_CYCLIC_GRAPH ERROR，消息含「由输入引用构成」、环序列与引用原文。"""
        nodes = [
            _make_node("trig", NodeType.TRIGGER),
            _make_node(
                "a",
                NodeType.TOOL,
                {
                    "tool_identifier": "ta",
                    "tool_source": "local",
                    "argument_mapping": {"p": "b::y"},
                },
            ),
            _make_node(
                "b",
                NodeType.TOOL,
                {
                    "tool_identifier": "tb",
                    "tool_source": "local",
                    "argument_mapping": {"q": "a::x"},
                },
            ),
        ]
        edges = [
            EdgeDefinition(edge_id="e0", source_node_id="trig", target_node_id="a"),
            EdgeDefinition(edge_id="e1", source_node_id="a", target_node_id="b"),
        ]
        definition = WorkflowDefinition(
            definition_id="wf-formb-cycle",
            name="形态 B 引用环",
            spec_version="1.0",
            nodes=nodes,
            edges=edges,
        )

        issues = validator.validate(definition)
        cycle_issues = [i for i in issues if i.code == WF_CYCLIC_GRAPH]

        assert len(cycle_issues) == 1
        issue = cycle_issues[0]
        assert issue.severity == Severity.ERROR
        assert "由输入引用构成" in issue.message
        assert "b -> a -> b" in issue.message
        assert "b::y" in issue.message
        assert "a::x" in issue.message

    def test_input_position_reference_within_loop_passes(self, validator):
        """循环节点体内顺向形态 B 引用（worker 引 helper::x）不构成非法环
        → 无 WF_CYCLIC_GRAPH（FR-15 验收 2）。"""
        nodes = [
            _make_node("trig", NodeType.TRIGGER),
            _make_node(
                "loop_node",
                NodeType.CONTROL_FLOW,
                {
                    "control_flow_kind": "loop",
                    "loop_body_entry": "helper",
                    "loop_termination_condition": "true",
                    "max_iterations": 5,
                },
            ),
            _make_node(
                "helper",
                NodeType.TOOL,
                {
                    "tool_identifier": "th",
                    "tool_source": "local",
                    "argument_mapping": {"x": "seed"},
                },
            ),
            _make_node(
                "worker",
                NodeType.TOOL,
                {
                    "tool_identifier": "tw",
                    "tool_source": "local",
                    "argument_mapping": {"q": "helper::x"},
                },
            ),
            _make_node("exit", NodeType.AGENT, {"model_identifier": "m"}),
        ]
        edges = [
            EdgeDefinition(edge_id="e0", source_node_id="trig", target_node_id="loop_node"),
            EdgeDefinition(edge_id="e1", source_node_id="loop_node", target_node_id="helper"),
            EdgeDefinition(edge_id="e2", source_node_id="helper", target_node_id="worker"),
            EdgeDefinition(edge_id="e3", source_node_id="worker", target_node_id="loop_node"),
            EdgeDefinition(edge_id="e4", source_node_id="loop_node", target_node_id="exit"),
        ]
        definition = WorkflowDefinition(
            definition_id="wf-formb-loop",
            name="循环内形态 B 引用",
            spec_version="1.0",
            nodes=nodes,
            edges=edges,
        )

        issues = validator.validate(definition)
        cycle_issues = [i for i in issues if i.code == WF_CYCLIC_GRAPH]
        assert cycle_issues == []
        errors = [i for i in issues if i.severity == Severity.ERROR]
        assert errors == []
