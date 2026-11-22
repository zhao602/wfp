# =============================================================================
# 统一输入引用静态校验测试（tests/unit/test_node_input_reference_validator.py）
#
# 覆盖验收（unified_node_input 批次 4 / CHG-T03，T4.4）：
#   - 引用提取登记表：DC-06 矩阵全部提取位置可枚举（T4.1）
#   - 静态 8 规则（DC-04，T4.2）：
#     规则 1 语法非法 ERROR / 规则 2 目标不存在 ERROR（三元组，AC-10）/
#     规则 3 自引用 ERROR / 规则 4 非上游 ERROR（合法回边内豁免，FR-16）/
#     规则 5 不可引用类别 ERROR（AC-07 场景 1）/ 规则 6 类别错配 ERROR /
#     规则 7 output_schema 疑似深度错配 WARNING / 规则 8 别名冲突 ERROR（AC-01 场景 3）
#   - trigger 豁免口径（DC-06#1）：上游/自引用/存在性豁免，语法校验保留
# =============================================================================

from orchestration_platform.domain.errors import WF_VALIDATION_FAILED
from orchestration_platform.domain.models.common import InputParameter
from orchestration_platform.domain.models.enums import NodeType, Severity
from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
    build_input_position_index,
    collect_input_references,
)


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
    """构造测试用工作流定义。

    nodes: List[NodeDefinition]；edge_pairs: List[(source, target)]；
    input_names: 定义级输入参数声明名列表。
    """
    edges = [
        EdgeDefinition(edge_id=f"e{i}", source_node_id=s, target_node_id=t)
        for i, (s, t) in enumerate(edge_pairs or [])
    ]
    return WorkflowDefinition(
        definition_id="wf-ref-validation",
        name="引用校验测试",
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


def _errors(issues: list) -> list:
    return [i for i in issues if i.severity == Severity.ERROR]


class TestCollectInputReferences:
    """引用提取登记表：DC-06 矩阵逐类型提取位置（T4.1）。"""

    def test_trigger_input_template_extraction(self):
        node = _make_node(
            "trig",
            NodeType.TRIGGER,
            {"trigger_type": "manual", "input_template": {"topic": "topic", "q": "up.score"}},
        )
        assert collect_input_references(node) == [
            ("input_template.topic", "topic"),
            ("input_template.q", "up.score"),
        ]

    def test_tool_argument_mapping_extraction(self):
        node = _make_node(
            "tool_1",
            NodeType.TOOL,
            {
                "tool_identifier": "t",
                "argument_mapping": {"a": "up.x"},
                "static_arguments": {"b": 1},
            },
        )
        # static_arguments 为常量直传，不参与引用提取
        assert collect_input_references(node) == [("argument_mapping.a", "up.x")]

    def test_subworkflow_input_mapping_extraction(self):
        node = _make_node(
            "sub_1",
            NodeType.SUBWORKFLOW,
            {"subworkflow_definition_id": "sub-wf", "input_mapping": {"x": "up"}},
        )
        assert collect_input_references(node) == [("input_mapping.x", "up")]

    def test_plugin_input_mapping_extraction(self):
        node = _make_node(
            "plugin_1",
            NodeType.PLUGIN,
            {"plugin_ref": "demo-plugin", "input_mapping": {"x": "up"}},
        )
        assert collect_input_references(node) == [("input_mapping.x", "up")]

    def test_data_transform_main_and_join_extraction(self):
        node = _make_node(
            "dt",
            NodeType.DATA_TRANSFORM,
            {
                "operation": "join",
                "input_dataset_reference": "left_ds",
                "params": {"right_dataset_reference": "right_ds"},
            },
        )
        assert collect_input_references(node) == [
            ("input_dataset_reference", "left_ds"),
            ("params.right_dataset_reference", "right_ds"),
        ]

    def test_data_transform_non_join_excludes_right_reference(self):
        node = _make_node(
            "dt",
            NodeType.DATA_TRANSFORM,
            {
                "operation": "filter",
                "input_dataset_reference": "left_ds",
                "params": {"right_dataset_reference": "right_ds"},
            },
        )
        assert collect_input_references(node) == [("input_dataset_reference", "left_ds")]

    def test_output_display_extraction_non_empty_only(self):
        filled = _make_node(
            "od", NodeType.OUTPUT_DISPLAY, {"input_reference": "up"}
        )
        assert collect_input_references(filled) == [("input_reference", "up")]
        empty = _make_node("od", NodeType.OUTPUT_DISPLAY, {})
        assert collect_input_references(empty) == []

    def test_output_display_multi_reference_extraction_indexed(self):
        # optimize_output_display_node UT-20（CHG-05/DC-11）：多元素逐条带索引登记，
        # 单元素既有形态由 test_output_display_extraction_non_empty_only 逐字守护（INV-D14）。
        node = _make_node(
            "od", NodeType.OUTPUT_DISPLAY, {"input_reference": ["a", "b", "c"]}
        )
        assert collect_input_references(node) == [
            ("input_reference[0]", "a"),
            ("input_reference[1]", "b"),
            ("input_reference[2]", "c"),
        ]

    def test_agent_inputs_extraction(self):
        node = _make_node(
            "agent_1",
            NodeType.AGENT,
            {
                "model_identifier": "m",
                "inputs": [
                    {"reference": "up.a"},
                    {"reference": "up.b", "alias": "别名"},
                ],
            },
        )
        assert collect_input_references(node) == [
            ("inputs[0].reference", "up.a"),
            ("inputs[1].reference", "up.b"),
        ]

    def test_agent_inputs_none_or_empty_extraction(self):
        implicit = _make_node("agent_1", NodeType.AGENT, {"model_identifier": "m"})
        explicit_empty = _make_node(
            "agent_2", NodeType.AGENT, {"model_identifier": "m", "inputs": []}
        )
        assert collect_input_references(implicit) == []
        assert collect_input_references(explicit_empty) == []

    def test_control_flow_expression_extraction_excludes_literals(self):
        node = _make_node(
            "cf",
            NodeType.CONTROL_FLOW,
            {
                "control_flow_kind": "conditional_branch",
                "conditions": [
                    {"target_node_id": "x", "expression": "out.score >= 0.8"},
                    {"target_node_id": "y", "expression": "true"},
                ],
            },
        )
        # true/false 等字面量特判名不构成引用（与求值器 Name 分支一致）
        assert collect_input_references(node) == [
            ("conditions[0].expression", "out"),
        ]

    def test_control_flow_loop_termination_extraction(self):
        node = _make_node(
            "loop_node",
            NodeType.CONTROL_FLOW,
            {
                "control_flow_kind": "loop",
                "loop_body_entry": "body",
                "loop_termination_condition": "out.score >= 0.8 and true",
            },
        )
        assert collect_input_references(node) == [("loop_termination_condition", "out")]

    def test_dynamic_injector_expression_extraction(self):
        node = _make_node(
            "inj",
            NodeType.DYNAMIC_INJECTOR,
            {"injection_condition": "up.score >= 0.5"},
        )
        assert collect_input_references(node) == [("injection_condition", "up")]

    def test_data_source_exempt_zero_extraction(self):
        node = _make_node(
            "ds",
            NodeType.DATA_SOURCE,
            {"connection_reference": "c", "query": "SELECT 1"},
        )
        assert collect_input_references(node) == []


class TestInputReferenceEightRules:
    """静态 8 规则 pass（T4.2，DC-04）。"""

    def setup_method(self):
        self.validator = NodeConfigurationValidator()

    # ------------------------------------------------------------------
    # 规则 1：引用语法非法 → ERROR
    # ------------------------------------------------------------------
    def test_rule1_syntax_invalid_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "tool_1",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"param": "Bad-Name"}},
                ),
            ],
            [("trig", "tool_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "tool_1"
        assert "argument_mapping.param" in issue.message
        assert "Bad-Name" in issue.message

    def test_rule1_trigger_syntax_still_validated(self):
        """trigger 豁免存在性/上游，但语法校验保留（DC-06#1「参与引用语法统一」）。"""
        definition = _make_definition(
            [_trigger(input_template={"x": "Bad-Name"})],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        assert issues[0].severity == Severity.ERROR
        assert "input_template.x" in issues[0].message

    # ------------------------------------------------------------------
    # 规则 2：引用目标不存在 → ERROR（三元组定位，AC-10）
    # ------------------------------------------------------------------
    def test_rule2_missing_target_reports_error_with_triplet(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "tool_1",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"param": "nonexistent_output"}},
                ),
            ],
            [("trig", "tool_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.ERROR
        # 三元组：节点 + 字段 + 引用目标（AC-10）
        assert issue.node_id == "tool_1"
        assert "argument_mapping.param" in issue.message
        assert "nonexistent_output" in issue.message

    def test_rule2_definition_input_param_satisfies_existence(self):
        """定义级输入参数声明纳入登记表：启动入参引用合法（运行期注入）。"""
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "tool_1",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"param": "declared_param"}},
                ),
            ],
            [("trig", "tool_1")],
            input_names=["declared_param"],
        )
        assert self.validator.validate(definition) == []

    # ------------------------------------------------------------------
    # 规则 3：自引用 → ERROR
    # ------------------------------------------------------------------
    def test_rule3_self_reference_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "agent_1",
                    NodeType.AGENT,
                    {"model_identifier": "m", "inputs": [{"reference": "own_out"}]},
                    output_name="own_out",
                ),
            ],
            [("trig", "agent_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "agent_1"
        assert "自引用" in issue.message

    # ------------------------------------------------------------------
    # 规则 4：非上游引用 → ERROR；合法回边内豁免（FR-16）
    # ------------------------------------------------------------------
    def test_rule4_non_upstream_reference_reports_error(self):
        """兄弟分支引用（owner 不在可达集、亦非回边结构内）→ ERROR。"""
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "producer",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="up_out",
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"p": "up_out"}},
                ),
            ],
            [("trig", "producer"), ("trig", "consumer")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "consumer"
        assert "非上游" in issue.message

    def test_rule4_upstream_reference_passes(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "producer",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="up_out",
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"p": "up_out"}},
                ),
            ],
            [("trig", "producer"), ("producer", "consumer")],
        )
        assert self.validator.validate(definition) == []

    def test_rule4_loop_body_reference_within_back_edge_passes(self):
        """合法回边内引用循环体输出（上一轮已写入）→ 上游规则不误报（FR-16）。"""
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "draft_agent",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="draft",
                ),
                _make_node(
                    "review_loop",
                    NodeType.CONTROL_FLOW,
                    {
                        "control_flow_kind": "loop",
                        "loop_body_entry": "review_agent",
                        "loop_termination_condition": "quality_score >= 90",
                    },
                ),
                _make_node(
                    "review_agent",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="quality_score",
                ),
                _make_node(
                    "finalize_node",
                    NodeType.TOOL,
                    {"tool_identifier": "finalizer"},
                    output_name="final_draft",
                ),
            ],
            [
                ("trig", "draft_agent"),
                ("draft_agent", "review_loop"),
                ("review_loop", "review_agent"),
                ("review_agent", "review_loop"),
                ("review_loop", "finalize_node"),
            ],
        )
        assert self.validator.validate(definition) == []

    # ------------------------------------------------------------------
    # trigger 豁免口径（DC-06#1）：存在性/上游/自引用豁免
    # ------------------------------------------------------------------
    def test_trigger_reference_exempt_from_existence_and_upstream(self):
        """trigger input_template 读触发上下文（运行期启动入参）：
        未登记目标不报存在性错误；引用下游节点输出不报非上游错误。"""
        definition = _make_definition(
            [
                _trigger(input_template={"x": "topic", "y": "down_out"}),
                _make_node(
                    "downstream",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="down_out",
                ),
            ],
            [("trig", "downstream")],
        )
        assert self.validator.validate(definition) == []

    # ------------------------------------------------------------------
    # 规则 5：不可引用类别输出（referable=False）→ ERROR（AC-07 场景 1）
    # ------------------------------------------------------------------
    def test_rule5_control_flow_signal_reference_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "router",
                    NodeType.CONTROL_FLOW,
                    {
                        "control_flow_kind": "conditional_branch",
                        "conditions": [
                            {"target_node_id": "tail", "expression": "true"}
                        ],
                    },
                    output_name="cf_signal",
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"p": "cf_signal"}},
                ),
            ],
            [("trig", "router"), ("router", "consumer")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "consumer"
        assert "不可引用" in issue.message

    # ------------------------------------------------------------------
    # 规则 6：类别级错配（data_transform 主/副输入须为 dataset 类）→ ERROR
    # ------------------------------------------------------------------
    def test_rule6_object_output_into_data_transform_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "producer",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="agent_out",
                ),
                _make_node(
                    "dt",
                    NodeType.DATA_TRANSFORM,
                    {"operation": "filter", "input_dataset_reference": "agent_out"},
                ),
            ],
            [("trig", "producer"), ("producer", "dt")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "dt"
        assert "非数据集类" in issue.message
        assert "object" in issue.message

    def test_rule6_dataset_output_into_data_transform_passes(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "src",
                    NodeType.DATA_SOURCE,
                    {"connection_reference": "c", "query": "SELECT 1"},
                    output_name="raw_data",
                ),
                _make_node(
                    "dt",
                    NodeType.DATA_TRANSFORM,
                    {"operation": "filter", "input_dataset_reference": "raw_data"},
                ),
            ],
            [("trig", "src"), ("src", "dt")],
        )
        assert self.validator.validate(definition) == []

    # ------------------------------------------------------------------
    # 规则 7：output_schema 可判定疑似路径深度错配 → WARNING（OP-04）
    # ------------------------------------------------------------------
    def test_rule7_schema_depth_suspect_reports_warning(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "producer",
                    NodeType.AGENT,
                    {
                        "model_identifier": "m",
                        "output_schema": {"summary": "string"},
                    },
                    output_name="agent_out",
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {
                        "tool_identifier": "t",
                        "argument_mapping": {"p": "agent_out.summary.total"},
                    },
                ),
            ],
            [("trig", "producer"), ("producer", "consumer")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.WARNING
        assert issue.node_id == "consumer"
        assert "疑似" in issue.message

    def test_rule7_schema_declared_path_passes(self):
        """路径恰好落于 schema 声明叶子（未深入标量）→ 无问题。"""
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "producer",
                    NodeType.AGENT,
                    {
                        "model_identifier": "m",
                        "output_schema": {"summary": "string", "nested": {"total": "int"}},
                    },
                    output_name="agent_out",
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {
                        "tool_identifier": "t",
                        "argument_mapping": {
                            "p": "agent_out.summary",
                            "q": "agent_out.nested.total",
                        },
                    },
                ),
            ],
            [("trig", "producer"), ("producer", "consumer")],
        )
        assert self.validator.validate(definition) == []

    # ------------------------------------------------------------------
    # 规则 8：Agent 选择集别名冲突 → ERROR（AC-01 场景 3）
    # ------------------------------------------------------------------
    def test_rule8_duplicate_alias_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "producer",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="up_out",
                ),
                _make_node(
                    "consumer",
                    NodeType.AGENT,
                    {
                        "model_identifier": "m",
                        "inputs": [
                            {"reference": "up_out", "alias": "dup"},
                            {"reference": "up_out.score", "alias": "dup"},
                        ],
                    },
                ),
            ],
            [("trig", "producer"), ("producer", "consumer")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "consumer"
        assert "重名" in issue.message
        assert "dup" in issue.message

    def test_rule8_alias_conflicts_with_default_name_reports_error(self):
        """alias 与另一条目缺省名冲突（冲突域 = 节点内条目名集合）→ ERROR。"""
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "producer",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="up_out",
                ),
                _make_node(
                    "other_producer",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="score",
                ),
                _make_node(
                    "consumer",
                    NodeType.AGENT,
                    {
                        "model_identifier": "m",
                        "inputs": [
                            {"reference": "score", "alias": "up_out"},
                            {"reference": "up_out"},
                        ],
                    },
                ),
            ],
            [
                ("trig", "producer"),
                ("trig", "other_producer"),
                ("producer", "consumer"),
                ("other_producer", "consumer"),
            ],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        assert issues[0].severity == Severity.ERROR
        assert "重名" in issues[0].message

    # ------------------------------------------------------------------
    # 表达式引用提取进入 8 规则（DC-08：AST 首段提取）
    # ------------------------------------------------------------------
    def test_expression_reference_missing_target_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "inj",
                    NodeType.DYNAMIC_INJECTOR,
                    {"injection_condition": "unknown_name >= 1"},
                ),
            ],
            [("trig", "inj")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "inj"
        assert "injection_condition" in issue.message
        assert "unknown_name" in issue.message

    def test_expression_reference_to_upstream_output_passes(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "producer",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="up_out",
                ),
                _make_node(
                    "inj",
                    NodeType.DYNAMIC_INJECTOR,
                    {"injection_condition": "up_out.score >= 0.6"},
                ),
            ],
            [("trig", "producer"), ("producer", "inj")],
        )
        assert self.validator.validate(definition) == []


# =============================================================================
# 形态 B（input_position 引用）索引与规则（upstream_input_ref 批次 3，
# T3.1/T3.2/T3.4，design DC-04 规则 2′~7′ / §2.5 候选生成矩阵）
# =============================================================================


class TestBuildInputPositionIndex:
    """输入位置索引构建（T3.1，DC-04）：node_id → { 位置名 → 形态 A 绑定 }。"""

    def test_trigger_input_template_positions(self):
        node = _make_node(
            "trig",
            NodeType.TRIGGER,
            {
                "trigger_type": "manual",
                "input_template": {"topic": "topic", "q": "up.score"},
            },
        )
        index = build_input_position_index(_make_definition([node]))
        assert index["trig"] == {"topic": "topic", "q": "up.score"}

    def test_tool_positions_and_static_arguments_excluded(self):
        node = _make_node(
            "tool_1",
            NodeType.TOOL,
            {
                "tool_identifier": "t",
                "argument_mapping": {"a": "up.x", "b": "up.y"},
                "static_arguments": {"c": 1},
            },
        )
        index = build_input_position_index(_make_definition([node]))
        # static_arguments 为常量直传，不产生可寻址输入位置（矩阵 §2.5，OP-02②）
        assert index["tool_1"] == {"a": "up.x", "b": "up.y"}

    def test_subworkflow_and_plugin_positions(self):
        sub = _make_node(
            "sub_1",
            NodeType.SUBWORKFLOW,
            {"subworkflow_definition_id": "sub-wf", "input_mapping": {"x": "up"}},
        )
        plugin = _make_node(
            "plugin_1",
            NodeType.PLUGIN,
            {"plugin_ref": "demo-plugin", "input_mapping": {"y": "up"}},
        )
        index = build_input_position_index(_make_definition([sub, plugin]))
        assert index["sub_1"] == {"x": "up"}
        assert index["plugin_1"] == {"y": "up"}

    def test_data_transform_join_main_and_right_positions(self):
        node = _make_node(
            "dt",
            NodeType.DATA_TRANSFORM,
            {
                "operation": "join",
                "input_dataset_reference": "left_ds",
                "params": {"right_dataset_reference": "right_ds"},
            },
        )
        index = build_input_position_index(_make_definition([node]))
        assert index["dt"] == {
            "input_dataset_reference": "left_ds",
            "params.right_dataset_reference": "right_ds",
        }

    def test_data_transform_non_join_excludes_right_position(self):
        node = _make_node(
            "dt",
            NodeType.DATA_TRANSFORM,
            {
                "operation": "filter",
                "input_dataset_reference": "left_ds",
                "params": {"right_dataset_reference": "right_ds"},
            },
        )
        index = build_input_position_index(_make_definition([node]))
        assert index["dt"] == {"input_dataset_reference": "left_ds"}

    def test_output_display_single_and_multi_indexed_positions(self):
        single = _make_node("od1", NodeType.OUTPUT_DISPLAY, {"input_reference": "up"})
        multi = _make_node(
            "od2", NodeType.OUTPUT_DISPLAY, {"input_reference": ["a", "b", "c"]}
        )
        index = build_input_position_index(_make_definition([single, multi]))
        assert index["od1"] == {"input_reference": "up"}
        assert index["od2"] == {
            "input_reference[0]": "a",
            "input_reference[1]": "b",
            "input_reference[2]": "c",
        }

    def test_agent_position_requires_alias(self):
        node = _make_node(
            "agent_1",
            NodeType.AGENT,
            {
                "model_identifier": "m",
                "inputs": [
                    {"reference": "up.a"},
                    {"reference": "up.b", "alias": "别名"},
                ],
            },
        )
        index = build_input_position_index(_make_definition([node]))
        # 缺 alias 条目的缺省名随引用漂移，非稳定寻址键，不入索引（矩阵 §2.5）
        assert index["agent_1"] == {"别名": "up.b"}

    def test_index_excludes_empty_invalid_and_form_b_bindings(self):
        node = _make_node(
            "tool_1",
            NodeType.TOOL,
            {
                "tool_identifier": "t",
                "argument_mapping": {
                    "a": "up.x",  # 形态 A：入索引
                    "b": "",  # 空绑定：不入
                    "c": "Bad-Name",  # 语法非法：不入
                    "d": "up::x",  # 形态 B 绑定：不入（不可一次解析，规则 5′ 报告）
                },
            },
        )
        index = build_input_position_index(_make_definition([node]))
        assert index["tool_1"] == {"a": "up.x"}

    def test_unregistered_types_produce_no_positions(self):
        """矩阵 ❌ 行与未含行类型（§2.5）：control_flow / dynamic_injector /
        data_source / llm / code / vkb 不产生输入位置。"""
        nodes = [
            _make_node(
                "cf",
                NodeType.CONTROL_FLOW,
                {
                    "control_flow_kind": "conditional_branch",
                    "conditions": [{"target_node_id": "x", "expression": "true"}],
                },
            ),
            _make_node(
                "inj",
                NodeType.DYNAMIC_INJECTOR,
                {"injection_condition": "up.score >= 0.5"},
            ),
            _make_node(
                "ds",
                NodeType.DATA_SOURCE,
                {"connection_reference": "c", "query": "SELECT 1"},
            ),
            _make_node(
                "llm",
                NodeType.LLM,
                {"model_identifier": "m", "inputs": [{"reference": "up.a"}]},
            ),
            _make_node(
                "code",
                NodeType.CODE,
                {"code": "print(1)", "inputs": [{"reference": "up.a"}]},
            ),
            _make_node(
                "vkb",
                NodeType.VECTOR_KNOWLEDGE_BASE,
                {"knowledge_base_id": "kb"},
            ),
        ]
        index = build_input_position_index(_make_definition(nodes))
        assert all(index[node.node_id] == {} for node in nodes)

    def test_index_deterministic_and_declaration_ordered(self):
        nodes = [
            _trigger(input_template={"topic": "topic"}),
            _make_node(
                "tool_1",
                NodeType.TOOL,
                {"tool_identifier": "t", "argument_mapping": {"a": "up.x", "b": "up.y"}},
            ),
        ]
        definition = _make_definition(nodes)
        first = build_input_position_index(definition)
        second = build_input_position_index(definition)
        assert first == second
        # 节点声明顺序 + 字段声明顺序（重复位置名后者覆盖，与运行期 dict 装配一致）
        assert list(first.keys()) == ["trig", "tool_1"]
        assert list(first["tool_1"].keys()) == ["a", "b"]


class TestInputPositionReferenceRules:
    """形态 B 引用规则 2′~7′（T3.2/T3.4，design DC-04 规则表）。"""

    def setup_method(self):
        self.validator = NodeConfigurationValidator()

    # ------------------------------------------------------------------
    # 规则 2′：来源节点不存在 → ERROR
    # ------------------------------------------------------------------
    def test_rule2p_missing_scope_node_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "tool_1",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"p": "ghost_node::y"}},
                ),
            ],
            [("trig", "tool_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "tool_1"
        assert "ghost_node" in issue.message
        assert "不在本定义的节点登记内" in issue.message

    # ------------------------------------------------------------------
    # 规则 3′：引用自身输入位置 → ERROR
    # ------------------------------------------------------------------
    def test_rule3p_self_position_reference_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "tool_1",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"p": "tool_1::x"}},
                ),
            ],
            [("trig", "tool_1")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "tool_1"
        assert "自引用" in issue.message

    # ------------------------------------------------------------------
    # 规则 4′：来源节点非上游 → ERROR；合法回边内豁免
    # ------------------------------------------------------------------
    def test_rule4p_non_upstream_scope_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node("mid", NodeType.AGENT, {"model_identifier": "m"}),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"q": "mid::p"}},
                ),
            ],
            [("trig", "mid"), ("trig", "consumer")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "consumer"
        assert "非上游输入位置" in issue.message

    def test_rule4p_upstream_scope_passes(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "up",
                    NodeType.DATA_SOURCE,
                    {"connection_reference": "c", "query": "SELECT 1"},
                    output_name="raw_data",
                ),
                _make_node(
                    "mid",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"table": "raw_data"}},
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {"tool_identifier": "t2", "argument_mapping": {"q": "mid::table"}},
                ),
            ],
            [("trig", "up"), ("up", "mid"), ("mid", "consumer")],
        )
        assert self.validator.validate(definition) == []

    def test_rule4p_scope_within_back_edge_passes(self):
        """合法回边内引用循环体节点的输入位置（上一轮已写入）→ 规则 4′ 不误报。"""
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "draft_agent",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="draft",
                ),
                _make_node(
                    "review_loop",
                    NodeType.CONTROL_FLOW,
                    {
                        "control_flow_kind": "loop",
                        "loop_body_entry": "review_agent",
                        "loop_termination_condition": "quality_score >= 90",
                    },
                ),
                _make_node(
                    "review_agent",
                    NodeType.AGENT,
                    {
                        "model_identifier": "m",
                        "inputs": [{"reference": "draft", "alias": "p"}],
                    },
                    output_name="quality_score",
                ),
                _make_node(
                    "finalize_node",
                    NodeType.TOOL,
                    {
                        "tool_identifier": "finalizer",
                        "argument_mapping": {"q": "review_agent::p"},
                    },
                ),
            ],
            [
                ("trig", "draft_agent"),
                ("draft_agent", "review_loop"),
                ("review_loop", "review_agent"),
                ("review_agent", "review_loop"),
                ("review_loop", "finalize_node"),
            ],
        )
        assert self.validator.validate(definition) == []

    # ------------------------------------------------------------------
    # trigger 豁免口径（DC-06#1 同构）：2′/3′/4′ 豁免，语法仍由规则 1 把关
    # ------------------------------------------------------------------
    def test_trigger_form_b_reference_exempt_from_scope_rules(self):
        definition = _make_definition(
            [
                _trigger(
                    input_template={
                        "ctx": "valid_a",
                        "self": "trig::ctx",
                        "ghost": "ghost_node::y",
                    }
                ),
            ],
        )
        assert self.validator.validate(definition) == []

    # ------------------------------------------------------------------
    # 规则 5′：位置绑定不可一次解析（无该位置名 / 绑定为空 / 绑定为形态 B）→ ERROR
    # ------------------------------------------------------------------
    def test_rule5p_missing_position_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "up",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="up_out",
                ),
                _make_node(
                    "mid",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"a": "up_out"}},
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {"tool_identifier": "t2", "argument_mapping": {"q": "mid::y"}},
                ),
            ],
            [("trig", "up"), ("up", "mid"), ("mid", "consumer")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "consumer"
        assert "'y'" in issue.message
        assert "不可一次解析" in issue.message

    def test_rule5p_empty_binding_reports_error(self):
        """绑定为空：位置不入索引 → 引用侧不可一次解析
        （绑定侧空引用由其自身的规则 1 报告，非重复计数）。"""
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "up",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="up_out",
                ),
                _make_node(
                    "mid",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"a": ""}},
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {"tool_identifier": "t2", "argument_mapping": {"q": "mid::a"}},
                ),
            ],
            [("trig", "up"), ("up", "mid"), ("mid", "consumer")],
        )
        issues = self.validator.validate(definition)

        consumer_issues = [i for i in issues if i.node_id == "consumer"]
        assert len(consumer_issues) == 1
        assert consumer_issues[0].severity == Severity.ERROR
        assert "不可一次解析" in consumer_issues[0].message

    def test_rule5p_form_b_binding_reports_error(self):
        """绑定为形态 B（不可一次解析，须运行期递归装配）→ 引用侧 ERROR
        （绑定侧由其自身的规则 5′ 报告，非重复计数）。"""
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "up",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="up_out",
                ),
                _make_node(
                    "mid",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"a": "up::x"}},
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {"tool_identifier": "t2", "argument_mapping": {"q": "mid::a"}},
                ),
            ],
            [("trig", "up"), ("up", "mid"), ("mid", "consumer")],
        )
        issues = self.validator.validate(definition)

        consumer_issues = [i for i in issues if i.node_id == "consumer"]
        assert len(consumer_issues) == 1
        assert consumer_issues[0].severity == Severity.ERROR
        assert "不可一次解析" in consumer_issues[0].message

    # ------------------------------------------------------------------
    # 规则 6′（其一）：绑定来源不可引用类别（referable=False）→ ERROR
    # ------------------------------------------------------------------
    def test_rule6p_non_referable_binding_owner_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "router",
                    NodeType.CONTROL_FLOW,
                    {
                        "control_flow_kind": "conditional_branch",
                        "conditions": [{"target_node_id": "mid", "expression": "true"}],
                    },
                    output_name="cf_signal",
                ),
                _make_node(
                    "mid",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"a": "cf_signal"}},
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {"tool_identifier": "t2", "argument_mapping": {"q": "mid::a"}},
                ),
            ],
            [("trig", "router"), ("router", "mid"), ("mid", "consumer")],
        )
        issues = self.validator.validate(definition)

        # mid 自身引用 control_flow 信号由规则 5 报告；此处断言引用侧规则 6′
        consumer_issues = [i for i in issues if i.node_id == "consumer"]
        assert len(consumer_issues) == 1
        issue = consumer_issues[0]
        assert issue.severity == Severity.ERROR
        assert "不可引用类别" in issue.message

    # ------------------------------------------------------------------
    # 规则 6′（其二）：data_transform 主/副输入位置绑定非数据集 → ERROR
    # ------------------------------------------------------------------
    def test_rule6p_dataset_contract_mismatch_reports_error(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "producer",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="agent_out",
                ),
                _make_node(
                    "mid",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"holder": "agent_out"}},
                ),
                _make_node(
                    "dt",
                    NodeType.DATA_TRANSFORM,
                    {"operation": "filter", "input_dataset_reference": "mid::holder"},
                ),
            ],
            [("trig", "producer"), ("producer", "mid"), ("mid", "dt")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "dt"
        assert "非数据集类" in issue.message
        assert "object" in issue.message

    def test_rule6p_dataset_contract_passes(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "src",
                    NodeType.DATA_SOURCE,
                    {"connection_reference": "c", "query": "SELECT 1"},
                    output_name="raw_data",
                ),
                _make_node(
                    "mid",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"holder": "raw_data"}},
                ),
                _make_node(
                    "dt",
                    NodeType.DATA_TRANSFORM,
                    {"operation": "filter", "input_dataset_reference": "mid::holder"},
                ),
            ],
            [("trig", "src"), ("src", "mid"), ("mid", "dt")],
        )
        assert self.validator.validate(definition) == []

    # ------------------------------------------------------------------
    # 规则 7′：绑定值内疑似路径深度错配 → WARNING（绑定路径 + 引用路径合并对照）
    # ------------------------------------------------------------------
    def test_rule7p_combined_path_suspect_reports_warning(self):
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "producer",
                    NodeType.AGENT,
                    {"model_identifier": "m", "output_schema": {"summary": "string"}},
                    output_name="agent_out",
                ),
                _make_node(
                    "tool_a",
                    NodeType.TOOL,
                    {
                        "tool_identifier": "t",
                        "argument_mapping": {"x": "agent_out.summary"},
                    },
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {
                        "tool_identifier": "t2",
                        "argument_mapping": {"p": "tool_a::x.total"},
                    },
                ),
            ],
            [("trig", "producer"), ("producer", "tool_a"), ("tool_a", "consumer")],
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.WARNING
        assert issue.node_id == "consumer"
        assert "疑似" in issue.message

    # ------------------------------------------------------------------
    # 顶层键不劫持（INV-E03 / FR-07）：命名输出 x 与来源节点位置名 x 并存
    # ------------------------------------------------------------------
    def test_top_level_named_output_not_hijacked_by_position(self):
        """顶层键 x 解析为命名输出（producer 的 x），不被来源节点的位置名 x 劫持；
        形态 B mid::x 显式寻址位置（绑定同一目标）同样通过。"""
        definition = _make_definition(
            [
                _trigger(),
                _make_node(
                    "producer",
                    NodeType.AGENT,
                    {"model_identifier": "m"},
                    output_name="x",
                ),
                _make_node(
                    "mid",
                    NodeType.TOOL,
                    {"tool_identifier": "t", "argument_mapping": {"x": "x"}},
                ),
                _make_node(
                    "consumer",
                    NodeType.TOOL,
                    {
                        "tool_identifier": "t2",
                        "argument_mapping": {"p": "x", "q": "mid::x"},
                    },
                ),
            ],
            [("trig", "producer"), ("producer", "mid"), ("mid", "consumer")],
        )
        assert self.validator.validate(definition) == []
