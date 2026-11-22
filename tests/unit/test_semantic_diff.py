# =============================================================================
# 语义 diff 引擎单测（tests/unit/test_semantic_diff.py）
#
# 覆盖（arch_review_v2 6.2，CHG-29；AC-05 的 V 部分）：
#   - 三类变更各正/反例：样例在测试内直接构造 CompiledWorkflow
#     （不新增 examples/ YAML 资产）；
#   - 判定顺序锁定断言（拓扑 > 参数 > 布局，DC-12）：同一对输入只归一类；
#   - 报告契约：SemanticDiffReport{changes, summary} 可被 CLI 按
#     getattr/model_dump 消费。
# =============================================================================

from typing import Dict, List, Optional

from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.enums import (
    ControlFlowKind,
    NodeType,
    TriggerType,
)
from orchestration_platform.domain.models.node_configurations import (
    AgentNodeConfiguration,
    BranchCondition,
    ControlFlowNodeConfiguration,
    OutputDisplayNodeConfiguration,
    TriggerNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.engine.abstractions.compiled_workflow import (
    CompiledWorkflow,
    ConditionalEdgeMeta,
)
from orchestration_platform.workflow.semantic_diff import (
    DiffChange,
    DiffChangeKind,
    SemanticDiffEngine,
    SemanticDiffReport,
)

_TOPOLOGY = DiffChangeKind.TOPOLOGY_CHANGE
_PARAM = DiffChangeKind.PARAM_CHANGE
_LAYOUT = DiffChangeKind.LAYOUT_CHANGE


# =============================================================================
# 测试内样例构造（直接构造 CompiledWorkflow，不走 YAML）
# =============================================================================


def _trigger(node_id: str = "trigger_start", name: str = "手动触发") -> NodeDefinition:
    return NodeDefinition(
        node_id=node_id,
        name=name,
        node_type=NodeType.TRIGGER,
        configuration=TriggerNodeConfiguration(
            trigger_type=TriggerType.MANUAL,
            input_template={"topic": "topic"},
        ),
    )


def _agent(
    node_id: str,
    model: str = "qwen3.8-27b",
    name: str = "Agent 节点",
    prompt: str = "请根据主题生成内容。",
    output_name: Optional[str] = None,
) -> NodeDefinition:
    return NodeDefinition(
        node_id=node_id,
        name=name,
        node_type=NodeType.AGENT,
        output_name=output_name if output_name is not None else node_id,
        configuration=AgentNodeConfiguration(
            model_identifier=model,
            system_prompt=prompt,
        ),
    )


def _display(
    node_id: str = "display_result",
    title: str = "结果展示",
    name: str = "输出展示",
) -> NodeDefinition:
    return NodeDefinition(
        node_id=node_id,
        name=name,
        node_type=NodeType.OUTPUT_DISPLAY,
        configuration=OutputDisplayNodeConfiguration(
            input_reference=["draft"],
            display_title=title,
        ),
    )


def _branch(
    node_id: str = "branch_check",
    target: str = "review_loop",
    expression: str = "score < 80",
) -> NodeDefinition:
    return NodeDefinition(
        node_id=node_id,
        name="达标判定",
        node_type=NodeType.CONTROL_FLOW,
        configuration=ControlFlowNodeConfiguration(
            control_flow_kind=ControlFlowKind.CONDITIONAL_BRANCH,
            conditions=[
                BranchCondition(target_node_id=target, expression=expression)
            ],
            default_branch_target=target,
        ),
    )


def _edge(
    edge_id: str,
    source: str,
    target: str,
    condition: Optional[str] = None,
) -> EdgeDefinition:
    return EdgeDefinition(
        edge_id=edge_id,
        source_node_id=source,
        target_node_id=target,
        condition=condition,
    )


def _conditional_meta(
    source: str,
    target: str,
    expression: Optional[str],
    is_default: bool = False,
) -> ConditionalEdgeMeta:
    prefix = "default" if is_default else "cond"
    return ConditionalEdgeMeta(
        edge_id=f"{prefix}_{source}_{target}",
        source_node_id=source,
        target_node_id=target,
        expression=expression,
        is_default=is_default,
    )


def _compiled(
    nodes: List[NodeDefinition],
    edges: List[EdgeDefinition],
    entry: str,
    conditional_edges: Optional[Dict[str, List[ConditionalEdgeMeta]]] = None,
    node_order: Optional[List[str]] = None,
    definition_id: str = "wf_test",
) -> CompiledWorkflow:
    return CompiledWorkflow(
        definition_id=definition_id,
        definition_version=1,
        nodes=nodes,
        edges=edges,
        entry_node_id=entry,
        terminal_node_ids=[],
        conditional_edges=conditional_edges or {},
        node_order=(
            node_order if node_order is not None else [node.node_id for node in nodes]
        ),
    )


def _base_compiled() -> CompiledWorkflow:
    """基准链路：trigger_start → generate_draft → display_result。"""
    nodes = [_trigger(), _agent("generate_draft"), _display()]
    edges = [
        _edge("e1", "trigger_start", "generate_draft"),
        _edge("e2", "generate_draft", "display_result"),
    ]
    return _compiled(nodes, edges, "trigger_start")


def _branch_compiled(
    branch_target: str = "review_loop",
    expression: str = "score < 80",
    include_default: bool = True,
) -> CompiledWorkflow:
    """基准分支链路：trigger → generate → branch_check -条件边-> review → display。

    branch_check 的迁移以 conditional_edges 元数据为权威（声明边 e3 仅画布定位）。
    include_default=False 时仅保留单条条件边（目标重定向一一对应场景）。
    """
    nodes = [
        _trigger(),
        _agent("generate_draft"),
        _branch(target=branch_target, expression=expression),
        _agent("review_loop", model="qwen3.8-14b", name="评审节点"),
        _display(),
    ]
    edges = [
        _edge("e1", "trigger_start", "generate_draft"),
        _edge("e2", "generate_draft", "branch_check"),
        _edge("e3", "branch_check", "review_loop"),
        _edge("e4", "review_loop", "display_result"),
    ]
    metas = [
        _conditional_meta("branch_check", branch_target, expression),
    ]
    if include_default:
        metas.append(
            _conditional_meta("branch_check", branch_target, None, is_default=True)
        )
    conditional_edges = {"branch_check": metas}
    return _compiled(nodes, edges, "trigger_start", conditional_edges)


# =============================================================================
# ① topology-change 正反例
# =============================================================================


class TestTopologyChange:
    def test_entry_change_reported_as_topology(self):
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "generate_draft",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        change = report.changes[0]
        assert change.kind == _TOPOLOGY
        assert change.target == "入口节点"
        assert "入口由" in change.detail

    def test_node_added_reported_as_topology(self):
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft"), _display(), _display("display_extra")],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
                _edge("e3", "generate_draft", "display_extra"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert all(change.kind == _TOPOLOGY for change in report.changes)
        added = [change for change in report.changes if "display_extra" in change.target]
        assert len(added) == 2  # 新增节点 + 新增边
        assert any("新增节点" in change.detail for change in added)
        assert any("新增边" in change.detail for change in added)

    def test_node_removed_reported_as_topology(self):
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft")],
            [_edge("e1", "trigger_start", "generate_draft")],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert all(change.kind == _TOPOLOGY for change in report.changes)
        removed = [change for change in report.changes if "display_result" in change.target]
        assert len(removed) == 2  # 删除节点 + 删除边
        assert any("删除节点" in change.detail for change in removed)

    def test_edge_added_reported_as_topology(self):
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
                _edge("e3", "trigger_start", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        change = report.changes[0]
        assert change.kind == _TOPOLOGY
        assert change.target == "trigger_start -> display_result"
        assert "新增边" in change.detail

    def test_edge_removed_reported_as_topology(self):
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft"), _display()],
            [_edge("e1", "trigger_start", "generate_draft")],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        change = report.changes[0]
        assert change.kind == _TOPOLOGY
        assert change.target == "generate_draft -> display_result"
        assert "删除边" in change.detail

    def test_edge_redirect_detected_when_source_same_target_changed(self):
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft"), _agent("review_target"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "review_target"),
                _edge("e3", "review_target", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert all(change.kind == _TOPOLOGY for change in report.changes)
        redirect = [
            change for change in report.changes if "重定向" in change.detail
        ]
        assert len(redirect) == 1
        assert redirect[0].target == "generate_draft -> review_target"
        assert "display_result" in redirect[0].detail

    def test_condition_text_change_on_declared_edge_reported_as_topology(self):
        base = _compiled(
            [_trigger(), _agent("generate_draft"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result", condition="score >= 80"),
            ],
            "trigger_start",
        )
        head = _compiled(
            [_trigger(), _agent("generate_draft"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result", condition="score >= 90"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        change = report.changes[0]
        assert change.kind == _TOPOLOGY
        assert change.target == "generate_draft -> display_result"
        assert "条件文本变更" in change.detail
        assert "score >= 80" in change.detail
        assert "score >= 90" in change.detail

    def test_conditional_branch_expression_change_reported_as_topology(self):
        """条件边按 source+target+condition 三元组比对：表达式变更 → 拓扑。"""
        base = _branch_compiled(expression="score < 80")
        head = _branch_compiled(expression="score < 90")
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        change = report.changes[0]
        assert change.kind == _TOPOLOGY
        assert change.target == "branch_check -> review_loop"
        assert "条件文本变更" in change.detail
        assert "score < 80" in change.detail
        assert "score < 90" in change.detail

    def test_conditional_branch_target_redirect_reported_as_topology(self):
        """条件边目标重定向（source 同 target 异）→ 拓扑。

        单条条件边一一对应（include_default=False）触发重定向判定。
        """
        base = _branch_compiled(branch_target="review_loop", include_default=False)
        head = _branch_compiled(branch_target="generate_draft", include_default=False)
        report = SemanticDiffEngine().diff(base, head)
        assert all(change.kind == _TOPOLOGY for change in report.changes)
        redirect = [change for change in report.changes if "重定向" in change.detail]
        assert len(redirect) == 1
        assert redirect[0].target == "branch_check -> generate_draft"

    def test_topology_identical_yields_no_topology_change(self):
        """反例：仅参数差异时拓扑段零产出（拓扑签名字面比对）。"""
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft", model="qwen3.8-14b"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert all(change.kind != _TOPOLOGY for change in report.changes)


# =============================================================================
# ② param-change 正反例
# =============================================================================


class TestParamChange:
    def test_configuration_change_reported_as_param(self):
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft", model="qwen3.8-14b"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        change = report.changes[0]
        assert change.kind == _PARAM
        assert change.target == "generate_draft"
        assert "model_identifier" in change.detail

    def test_multiple_diff_keys_listed_per_node(self):
        base = _base_compiled()
        head = _compiled(
            [
                _trigger(),
                _agent(
                    "generate_draft",
                    model="qwen3.8-14b",
                    prompt="请根据主题生成初稿。",
                ),
                _display(),
            ],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        change = report.changes[0]
        assert change.kind == _PARAM
        assert "model_identifier" in change.detail
        assert "system_prompt" in change.detail

    def test_node_type_change_falls_into_param_bucket(self):
        """节点类型变更：configuration 判别键 node_type 差异 → 参数桶。"""
        base = _compiled(
            [_trigger(), _agent("generate_draft"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        head = _compiled(
            [_trigger(), _display("generate_draft", title="结果展示", name="输出展示"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        change = report.changes[0]
        assert change.kind == _PARAM
        assert "node_type" in change.detail

    def test_key_order_ignored_in_configuration_compare(self):
        """忽略键序：模板键序不同但内容一致 → 非参数变更（语义等价）。"""
        nodes_base = [
            _trigger(),
            NodeDefinition(
                node_id="generate_draft",
                name="Agent 节点",
                node_type=NodeType.TRIGGER,
                configuration=TriggerNodeConfiguration(
                    trigger_type=TriggerType.MANUAL,
                    input_template={"alpha": "x", "beta": "y"},
                ),
            ),
            _display(),
        ]
        nodes_head = [
            _trigger(),
            NodeDefinition(
                node_id="generate_draft",
                name="Agent 节点",
                node_type=NodeType.TRIGGER,
                configuration=TriggerNodeConfiguration(
                    trigger_type=TriggerType.MANUAL,
                    input_template={"beta": "y", "alpha": "x"},
                ),
            ),
            _display(),
        ]
        edges = [
            _edge("e1", "trigger_start", "generate_draft"),
            _edge("e2", "generate_draft", "display_result"),
        ]
        report = SemanticDiffEngine().diff(
            _compiled(nodes_base, edges, "trigger_start"),
            _compiled(nodes_head, edges, "trigger_start"),
        )
        assert report.changes == []
        assert "语义等价" in report.summary

    def test_param_change_on_multiple_nodes(self):
        base = _base_compiled()
        head = _compiled(
            [
                _trigger(),
                _agent("generate_draft", model="qwen3.8-14b"),
                _display(title="结果展示 v2"),
            ],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 2
        assert all(change.kind == _PARAM for change in report.changes)
        assert {change.target for change in report.changes} == {
            "generate_draft",
            "display_result",
        }

    def test_configuration_identical_yields_no_param_change(self):
        """反例：configuration 一致（仅画布元数据差异）→ 参数段零产出。"""
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft", name="Agent 改名"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert all(change.kind != _PARAM for change in report.changes)


# =============================================================================
# ③ layout-change 正反例
# =============================================================================


class TestLayoutChange:
    def test_node_name_change_reported_as_layout(self):
        """节点名称为画布展示元数据 → 布局桶（单条汇总）。"""
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft", name="Agent 改名"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        change = report.changes[0]
        assert change.kind == _LAYOUT
        assert change.detail == "仅布局变化，语义等价"
        assert "语义等价" in report.summary

    def test_node_order_change_reported_as_layout(self):
        """声明顺序变化（拓扑排序平局打破后的 node_order 可见形态）→ 布局桶。"""
        base = _base_compiled()
        nodes = [_display(), _agent("generate_draft"), _trigger()]
        edges = [
            _edge("e1", "trigger_start", "generate_draft"),
            _edge("e2", "generate_draft", "display_result"),
        ]
        head = _compiled(nodes, edges, "trigger_start", node_order=[
            "display_result",
            "generate_draft",
            "trigger_start",
        ])
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        assert report.changes[0].kind == _LAYOUT

    def test_edge_id_change_reported_as_layout(self):
        """edge_id 供画布定位 → 仅改 edge_id（source/target 不变）落布局桶。"""
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2_renamed", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        assert report.changes[0].kind == _LAYOUT

    def test_output_name_change_reported_as_layout(self):
        """configuration 之外的节点级注释字段（输出命名）→ 布局桶。"""
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft", output_name="draft_renamed"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        assert report.changes[0].kind == _LAYOUT

    def test_identical_workflows_report_no_changes(self):
        """反例：完全一致 → 空变更 + 语义等价汇总。"""
        report = SemanticDiffEngine().diff(_base_compiled(), _base_compiled())
        assert report.changes == []
        assert "语义等价" in report.summary


# =============================================================================
# ④ 判定顺序锁定（DC-12：拓扑 > 参数 > 布局，同一对输入只归一类）
# =============================================================================


class TestJudgmentOrderLock:
    def test_topology_takes_precedence_over_param(self):
        """拓扑 + 参数同时差异 → 仅产出拓扑变更。"""
        base = _base_compiled()
        head = _compiled(
            [
                _trigger(),
                _agent("generate_draft", model="qwen3.8-14b"),
                _agent("review_target"),
                _display(),
            ],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "review_target"),
                _edge("e3", "review_target", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert report.changes
        assert {change.kind for change in report.changes} == {_TOPOLOGY}
        assert all("配置差异键" not in change.detail for change in report.changes)

    def test_topology_takes_precedence_over_layout(self):
        """拓扑 + 布局同时差异 → 仅产出拓扑变更。"""
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft", name="Agent 改名"), _agent("review_target"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "review_target"),
                _edge("e3", "review_target", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert report.changes
        assert {change.kind for change in report.changes} == {_TOPOLOGY}
        assert all("仅布局变化" not in change.detail for change in report.changes)

    def test_param_takes_precedence_over_layout(self):
        """参数 + 布局同时差异 → 仅产出参数变更。"""
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft", model="qwen3.8-14b", name="Agent 改名"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert len(report.changes) == 1
        change = report.changes[0]
        assert change.kind == _PARAM
        assert "model_identifier" in change.detail
        assert "仅布局变化" not in change.detail

    def test_single_kind_per_report_even_with_multi_source_changes(self):
        """多点拓扑变更（入口 + 节点 + 边）→ 报告仍只含一种类别。"""
        base = _base_compiled()
        head = _compiled(
            [_trigger("trigger_main"), _agent("generate_draft"), _display()],
            [
                _edge("e1", "trigger_main", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
                _edge("e3", "generate_draft", "display_result", condition="score >= 80"),
            ],
            "trigger_main",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert report.changes
        assert {change.kind for change in report.changes} == {_TOPOLOGY}

    def test_report_contract_consumable_by_cli(self):
        """报告契约：字段可 getattr 消费、model_dump(mode="json") 可序列化、
        kind 序列化为连字符字符串（与 CLI 文本/JSON 输出对齐）。"""
        base = _base_compiled()
        head = _compiled(
            [_trigger(), _agent("generate_draft", model="qwen3.8-14b"), _display()],
            [
                _edge("e1", "trigger_start", "generate_draft"),
                _edge("e2", "generate_draft", "display_result"),
            ],
            "trigger_start",
        )
        report = SemanticDiffEngine().diff(base, head)
        assert isinstance(report, SemanticDiffReport)
        assert isinstance(report.changes[0], DiffChange)
        payload = report.model_dump(mode="json")
        assert payload["changes"][0]["kind"] == "param-change"
        assert payload["summary"]
        assert isinstance(payload["changes"][0]["target"], str)
        assert isinstance(payload["changes"][0]["detail"], str)
