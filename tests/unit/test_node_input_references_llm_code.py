# =============================================================================
# llm / code 节点输入引用接入既有八规则测试
# （tests/unit/test_node_input_references_llm_code.py，llm_and_code_nodes 任务 3.3）
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_node_input_references_llm_code.py -q
#
# 覆盖验收（AC-46 / AC-47 / AC-48 / AC-50 / CHG-05 / CHG-06）：
#   - 提取登记形态与 agent 完全一致（inputs[i].reference，AC-46 提取口径）；
#   - llm / code 引用进入既有规则：悬空（规则 2，AC-48 统一错误码 + 三元组定位）、
#     自引用（规则 3，AC-50）、非上游（规则 4）、上游引用通过；
#   - code 节点输出可引用（llm_code_nodes_v2 DC-13-4/DC-13-5 修订：v1 拟定的
#     referable=False 因「不执行」前提失效，规则 5 放行，下游可引用 {"result": ...}）；
#     控制流既有拒绝语义回归（动态化为「产出 signal」）；
#   - llm / code 选择集别名冲突（规则 8 适用域扩展，AC-46/50）。
# =============================================================================

# 沙箱限制：本机 asyncio 事件循环不可用（socketpair 被网络策略拦截，
# WinError 10051 / _ssock AttributeError），run_coro 采用手动驱动协程
# 方式（send(None) 循环），不创建事件循环，故无需 import asyncio。
# 本轮尚无异步用例，按任务 2.8 要求保持文件结构一致，供后续批次复用。

import pytest

from orchestration_platform.domain.errors import WF_VALIDATION_FAILED
from orchestration_platform.domain.models.common import InputParameter
from orchestration_platform.domain.models.enums import NodeType, Severity
from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
    collect_input_references,
)


def run_coro(coro):
    """手动驱动协程至完成（沙箱事件循环不可用时的 fallback）。

    execute 的 await 链均为 AsyncMock 或纯同步逻辑协程，无真实挂起点，
    故以 send(None) 循环驱动至 StopIteration，环境无关。
    """
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


def _make_node(
    node_id: str,
    node_type: NodeType,
    config: dict,
    output_name: str | None = None,
) -> NodeDefinition:
    """构造测试用节点（configuration 用 dict，由 NodeDefinition 判别联合解析）。"""
    return NodeDefinition(
        node_id=node_id,
        node_type=node_type,
        configuration=config,
        output_name=output_name,
    )


def _make_definition(
    nodes: list,
    edge_pairs: list | None = None,
    input_names: list | None = None,
) -> WorkflowDefinition:
    """构造测试用工作流定义（与 test_node_input_reference_validator 同范式）。"""
    edges = [
        EdgeDefinition(edge_id=f"e{i}", source_node_id=s, target_node_id=t)
        for i, (s, t) in enumerate(edge_pairs or [])
    ]
    return WorkflowDefinition(
        definition_id="wf-llm-code-refs",
        name="llm_code 引用校验测试",
        spec_version="1.0",
        nodes=list(nodes),
        edges=edges,
        inputs=[InputParameter(name=name) for name in (input_names or [])],
    )


def _trigger(node_id: str = "trig", input_template: dict | None = None) -> NodeDefinition:
    config: dict = {"trigger_type": "manual"}
    if input_template is not None:
        config["input_template"] = input_template
    return _make_node(node_id, NodeType.TRIGGER, config)


def _llm(node_id: str, config: dict, output_name: str | None = None) -> NodeDefinition:
    return _make_node(node_id, NodeType.LLM, config, output_name=output_name)


def _code(node_id: str, config: dict, output_name: str | None = None) -> NodeDefinition:
    return _make_node(node_id, NodeType.CODE, config, output_name=output_name)


def _upstream_producer(output_name: str = "up_out") -> NodeDefinition:
    """上游产出节点（agent：可引用 object 类，规则 2-4 的合法目标）。"""
    return _make_node(
        "producer", NodeType.AGENT, {"model_identifier": "m"}, output_name=output_name
    )


def _inputs_config() -> list:
    """与 test_node_input_reference_validator.test_agent_inputs_extraction 同构的
    选择集（含别名条目），供 llm / code 提取形态对比复用。"""
    return [
        {"reference": "up.a"},
        {"reference": "up.b", "alias": "别名"},
    ]


class TestCollectInputReferencesLlmCode:
    """提取登记：llm / code 与 agent 形态完全一致（任务 3.3，AC-46）。"""

    def test_llm_inputs_extraction_same_shape_as_agent(self):
        agent_node = _make_node(
            "agent_1",
            NodeType.AGENT,
            {"model_identifier": "m", "inputs": _inputs_config()},
        )
        llm_node = _llm(
            "llm_1",
            {"model_identifier": "m", "inputs": _inputs_config()},
        )
        expected = [
            ("inputs[0].reference", "up.a"),
            ("inputs[1].reference", "up.b"),
        ]
        assert collect_input_references(llm_node) == expected
        assert collect_input_references(llm_node) == collect_input_references(agent_node)

    def test_code_inputs_extraction_same_shape_as_agent(self):
        agent_node = _make_node(
            "agent_1",
            NodeType.AGENT,
            {"model_identifier": "m", "inputs": _inputs_config()},
        )
        code_node = _code(
            "code_1",
            {"code": "value = 1", "inputs": _inputs_config()},
        )
        expected = [
            ("inputs[0].reference", "up.a"),
            ("inputs[1].reference", "up.b"),
        ]
        assert collect_input_references(code_node) == expected
        assert collect_input_references(code_node) == collect_input_references(agent_node)

    @pytest.mark.parametrize(
        "node_type, base_config",
        [
            (NodeType.LLM, {"model_identifier": "m"}),
            (NodeType.CODE, {"code": "value = 1"}),
        ],
    )
    def test_inputs_none_or_empty_zero_extraction(self, node_type, base_config):
        implicit = _make_node(f"{node_type.value}_1", node_type, dict(base_config))
        explicit_empty = _make_node(
            f"{node_type.value}_2", node_type, {**base_config, "inputs": []}
        )
        assert collect_input_references(implicit) == []
        assert collect_input_references(explicit_empty) == []


class TestInputReferenceRulesLlmCode:
    """llm / code 引用进入既有八规则（任务 3.3，AC-46/47/48/50）。"""

    def setup_method(self):
        self.validator = NodeConfigurationValidator()

    # ------------------------------------------------------------------
    # 规则 2：悬空引用 → ERROR（AC-48：统一错误码 + 三元组定位）
    # ------------------------------------------------------------------
    def test_rule2_llm_dangling_reference_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _llm(
                    "llm_1",
                    {
                        "model_identifier": "m",
                        "inputs": [{"reference": "nonexistent_output"}],
                    },
                ),
            ],
            [("trig", "llm_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "llm_1"
        assert "inputs[0].reference" in issue.message
        assert "nonexistent_output" in issue.message

    def test_rule2_code_dangling_reference_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _code(
                    "code_1",
                    {
                        "code": "value = 1",
                        "inputs": [{"reference": "nonexistent_output"}],
                    },
                ),
            ],
            [("trig", "code_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "code_1"
        assert "inputs[0].reference" in issue.message
        assert "nonexistent_output" in issue.message

    # ------------------------------------------------------------------
    # 规则 3：自引用 → ERROR（AC-50）
    # ------------------------------------------------------------------
    def test_rule3_llm_self_reference_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _llm(
                    "llm_1",
                    {
                        "model_identifier": "m",
                        "inputs": [{"reference": "own_out"}],
                    },
                    output_name="own_out",
                ),
            ],
            [("trig", "llm_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "llm_1"
        assert "自引用" in issue.message

    def test_rule3_code_self_reference_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _code(
                    "code_1",
                    {
                        "code": "value = 1",
                        "inputs": [{"reference": "own_out"}],
                    },
                    output_name="own_out",
                ),
            ],
            [("trig", "code_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "code_1"
        assert "自引用" in issue.message

    # ------------------------------------------------------------------
    # 规则 4：非上游 → ERROR；上游引用通过
    # ------------------------------------------------------------------
    def test_rule4_llm_non_upstream_reference_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _upstream_producer(),
                _llm(
                    "llm_1",
                    {
                        "model_identifier": "m",
                        "inputs": [{"reference": "up_out"}],
                    },
                ),
            ],
            [("trig", "producer"), ("trig", "llm_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "llm_1"
        assert "非上游" in issue.message

    def test_rule4_llm_upstream_reference_passes(self):
        definition = _make_definition(
            [
                _trigger(),
                _upstream_producer(),
                _llm(
                    "llm_1",
                    {
                        "model_identifier": "m",
                        "inputs": [{"reference": "up_out"}],
                    },
                ),
            ],
            [("trig", "producer"), ("producer", "llm_1")],
        )
        assert self.validator.validate(definition) == []

    def test_rule4_code_non_upstream_reference_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _upstream_producer(),
                _code(
                    "code_1",
                    {
                        "code": "value = 1",
                        "inputs": [{"reference": "up_out"}],
                    },
                ),
            ],
            [("trig", "producer"), ("trig", "code_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "code_1"
        assert "非上游" in issue.message

    # ------------------------------------------------------------------
    # 规则 5：code 输出可引用（llm_code_nodes_v2 DC-13-4/DC-13-5 修订，放行）；
    # 控制流既有拒绝语义回归（动态化为「产出 signal」）
    # ------------------------------------------------------------------
    def test_rule5_code_output_reference_allowed(self):
        """v2 修订：code 产出命名输出 {"result": ...}，下游引用放行（全链路场景 8.3 前置）。"""
        definition = _make_definition(
            [
                _trigger(),
                _code("code_1", {"code": "value = 1"}, output_name="code_out"),
                _llm(
                    "llm_1",
                    {
                        "model_identifier": "m",
                        "inputs": [{"reference": "code_out"}],
                    },
                ),
            ],
            [("trig", "code_1"), ("code_1", "llm_1")],
        )
        assert self.validator.validate(definition) == []

    def test_rule5_control_flow_signal_kind_dynamic_message(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "router",
                    NodeType.CONTROL_FLOW,
                    {
                        "control_flow_kind": "conditional_branch",
                        "conditions": [
                            {"target_node_id": "llm_1", "expression": "true"}
                        ],
                    },
                    output_name="cf_signal",
                ),
                _llm(
                    "llm_1",
                    {
                        "model_identifier": "m",
                        "inputs": [{"reference": "cf_signal"}],
                    },
                ),
            ],
            [("trig", "router"), ("router", "llm_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "llm_1"
        assert "不可引用" in issue.message
        assert "产出 signal" in issue.message

    # ------------------------------------------------------------------
    # 规则 8：llm / code 选择集别名冲突（适用域扩展，AC-46/50）
    # ------------------------------------------------------------------
    def test_rule8_llm_duplicate_alias_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _upstream_producer(),
                _llm(
                    "llm_1",
                    {
                        "model_identifier": "m",
                        "inputs": [
                            {"reference": "up_out", "alias": "dup"},
                            {"reference": "up_out.score", "alias": "dup"},
                        ],
                    },
                ),
            ],
            [("trig", "producer"), ("producer", "llm_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "llm_1"
        assert "重名" in issue.message
        assert "大模型节点" in issue.message
        assert "dup" in issue.message

    def test_rule8_code_duplicate_alias_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _upstream_producer(),
                _code(
                    "code_1",
                    {
                        "code": "value = 1",
                        "inputs": [
                            {"reference": "up_out", "alias": "dup"},
                            {"reference": "up_out.score", "alias": "dup"},
                        ],
                    },
                ),
            ],
            [("trig", "producer"), ("producer", "code_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "code_1"
        assert "重名" in issue.message
        assert "代码节点" in issue.message
        assert "dup" in issue.message
