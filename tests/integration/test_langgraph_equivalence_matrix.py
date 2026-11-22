# =============================================================================
# LangGraph 等价矩阵（tests/integration/test_langgraph_equivalence_matrix.py）
#
# 覆盖验收：arch_review_gap_b4_architecture AC-B4-03（母 tasks 3.5，DC-05 裁决 2/3，
# design §4.6）：12 范例 × 双运行时矩阵——
#   E1 编译产物对比（CompiledWorkflow 字段级 + 结构对账）；
#   E2 触发序列 + 逐节点输出快照（双侧对齐）；
#   E3 终态 node_outputs 字段值对比；
#   E4 注入节点失败错误码对比；
#   E5 同输入重复执行输出一致（幂等）。
# 口径：
#   - 确定性依赖注入：双引擎共享同一执行器注册表实例（节点语义单源，INV-B4-01）；
#     真实执行器（trigger/control_flow/output_display/data_transform，确定性）+
#     确定性桩（agent/data_source，按 node_id 固定输出）；
#   - 8 套 IN 断言 E1~E5；4 套 N/A 静态标注排除原因（spec §0.2 预扫表）；
#   - langgraph 依赖不可用 → 全矩阵 skip 登记（不伪造等价结论，DC-B4-03）。
# =============================================================================

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from orchestration_platform.domain.errors import (
    WF_LOOP_NOT_CONVERGED,
    WF_VALIDATION_FAILED,
    DomainError,
    NodeExecutionError,
)
from orchestration_platform.domain.models.enums import (
    InstanceStatus,
    NodeType,
    TriggerType,
)
from orchestration_platform.domain.models.execution_instance import (
    StartInstanceRequest,
)
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.adapter.langgraph_adapter import (
    LangGraphAdapter,
    is_langgraph_available,
)
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import (
    NodeExecutorRegistry,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.engine.nodes.control_flow_node_executor import (
    ControlFlowNodeExecutor,
)
from orchestration_platform.engine.nodes.data_transform_node_executor import (
    DataTransformNodeExecutor,
)
from orchestration_platform.engine.nodes.output_display_node_executor import (
    OutputDisplayNodeExecutor,
)
from orchestration_platform.engine.nodes.trigger_node_executor import (
    TriggerNodeExecutor,
)
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

EXAMPLES_ROOT = Path(__file__).resolve().parent.parent.parent / "examples"

requires_langgraph = pytest.mark.skipif(
    not is_langgraph_available(),
    reason="langgraph 依赖不可用：全矩阵 skip 登记（不伪造等价结论，DC-B4-03）",
)


class _MatrixCase:
    """IN 范例矩阵用例：确定性桩输出 + 预期触发序列（第三参照源）。

    expected_error_code 为 None 时预期成功收敛（E3 终态输出对比）；
    非 None 时预期确定性失败（E3 退化为错误码对比——等价性以双引擎
    同码失败证明，如 reflection_iteration 的 WF_LOOP_NOT_CONVERGED）。
    """

    def __init__(
        self,
        case_id: str,
        example_file: str,
        inputs: Dict[str, Any],
        stub_outputs: Dict[str, Any],
        expected_sequence: List[str],
        expected_error_code: Optional[str] = None,
    ) -> None:
        self.case_id = case_id
        self.example_file = example_file
        self.inputs = inputs
        self.stub_outputs = stub_outputs
        self.expected_sequence = expected_sequence
        self.expected_error_code = expected_error_code


# 8 套 IN 范例（spec §0.2 预扫表）：
# - stub_outputs 按 node_id 给出 agent/data_source 确定性桩输出
#   （真实执行器为 trigger/control_flow/output_display/data_transform——纯内存确定性）；
# - expected_sequence 为推导的确定性路径（同时锚定内置引擎与第二运行时）。
MATRIX_IN: List[_MatrixCase] = [
    _MatrixCase(
        case_id="sequential_pipeline",
        example_file="sequential_pipeline/sequential_pipeline.yaml",
        inputs={"topic": "销售数据分析"},
        stub_outputs={
            # 数据集形状 = list（assemble_single_reference expected_kind="dataset"）
            "read_data": [{"id": 1, "nickname": "u1"}, {"id": 2, "nickname": "u2"}],
            "analyze": {"model_output": "分析结论：两条用户记录"},
        },
        expected_sequence=["trigger_start", "read_data", "analyze"],
    ),
    _MatrixCase(
        case_id="conditional_loop_pipeline",
        example_file="conditional_loop_pipeline/conditional_loop_pipeline.yaml",
        inputs={"topic": "AI 平台评测", "target_score": 80},
        stub_outputs={
            "generate_draft": {"text": "draft-v0", "score": 0},
            "review_draft": {"text": "draft-final", "score": 100},
        },
        expected_sequence=[
            "trigger_start",
            "generate_draft",
            "review_loop",
            "review_draft",
            "branch_check",
            "review_loop",
            "display_result",
        ],
    ),
    _MatrixCase(
        case_id="reflection_iteration",
        example_file="reflection_iteration/reflection_iteration.yaml",
        inputs={"task": "生成周报", "quality_threshold": "0.8"},
        stub_outputs={
            "generator_agent": {"content": "周报内容 v1", "version": 1},
            "critic_agent": {"score": 0.95, "feedback": "达标", "pass": True},
        },
        # 确定性失败用例：表达式引用 critique_result.pass——pass 为 Python
        # 关键字，ast.parse 必抛 SyntaxError → 求值器抛 DomainError →
        # 分支条件被跳过落默认、循环终止条件永不可满足 → 循环走满
        # max_iterations=5 后 WF_LOOP_NOT_CONVERGED（受限求值器下的真实
        # 确定性行为；等价性以双引擎同码失败证明）
        expected_sequence=(
            ["trigger_start"]
            + ["generator_agent", "critic_agent", "quality_check", "check_iterations"] * 6
        ),
        expected_error_code=WF_LOOP_NOT_CONVERGED,
    ),
    _MatrixCase(
        case_id="drift_detection",
        example_file="drift_detection/drift_detection.yaml",
        inputs={"current_dataset_id": "cur-001", "baseline_dataset_id": "baseline-001"},
        # 受限求值器不支持 is not（IsNot 不在白名单）→ 条件求值抛错 →
        # _eval_branch 容错回落默认分支（baseline_missing_agent）——双引擎同径
        stub_outputs={
            # 数据集形状 = list（assemble_single_reference expected_kind="dataset"）
            "read_current_data": [{"feature_a": 1.0}, {"feature_a": 1.1}],
            "read_baseline_data": [{"feature_a": 0.8}],
            "drift_agent": {
                "baseline_established": True,
                "drift_detected": True,
                "drift_score": 0.5,
                "shifted_features": ["feature_a"],
                "notes": "确定性桩",
            },
            "baseline_missing_agent": {
                "baseline_established": False,
                "drift_detected": False,
                "drift_score": 0,
                "shifted_features": [],
                "notes": "基线未建立，本次不判定漂移",
            },
        },
        expected_sequence=[
            "trigger_start",
            "read_current_data",
            "baseline_check",
            "baseline_missing_agent",
            "display_result",
        ],
    ),
    _MatrixCase(
        case_id="realtime_subscription_workflow",
        example_file="realtime_subscription/realtime_subscription_workflow.yaml",
        inputs={"event_data": {"order_id": "o-001"}, "event_type": "order_created"},
        stub_outputs={
            "classify_event": {"category": "order", "priority": "high", "requires_action": True},
            "process_high_priority": {"action_taken": "escalated", "result": "已升级处理", "alert_sent": True},
            "process_normal": {"action_taken": "processed", "result": "已常规处理"},
        },
        expected_sequence=[
            "trigger_on_event",
            "classify_event",
            "route_by_category",
            "process_high_priority",
            "display_result",
        ],
    ),
    _MatrixCase(
        case_id="feature_engineering",
        example_file="feature_engineering/feature_engineering.yaml",
        inputs={"dataset_identifier": "ds-001", "feature_requirements": "按用户构造近 7 日行为统计特征"},
        stub_outputs={
            # 数据集形状 = list（assemble_single_reference expected_kind="dataset"）；
            # 第三行 user_id=None 供 handle_nulls(drop_row) 确定性剔除
            "read_upstream_data": [
                {"user_id": "u2", "active_days": 3},
                {"user_id": "u1", "active_days": 5},
                {"user_id": None, "active_days": 1},
            ],
            "feature_engineer_agent": {
                "feature_list": ["active_days"],
                "computed_features": ["active_days"],
                "pending_features": [],
                "notes": "确定性桩",
            },
        },
        expected_sequence=[
            "trigger_start",
            "read_upstream_data",
            "filter_invalid_records",
            "enrich_metadata",
            "feature_engineer_agent",
            "display_result",
        ],
    ),
    _MatrixCase(
        case_id="data_engineering_agent",
        example_file="data_engineering/data_engineering_agent.yaml",
        inputs={"query_sql": "SELECT * FROM sales_records", "analysis_goal": "销售趋势分析"},
        stub_outputs={
            # 数据集形状 = list（assemble_single_reference expected_kind="dataset"）；
            # 第三行 order_id=None 供操作链首步 handle_nulls(drop_row) 确定性剔除
            "read_from_hologres": [
                {"order_id": "o1", "amount": 10, "region": "east", "product_category": "x"},
                {"order_id": "o2", "amount": 5, "region": "east", "product_category": "x"},
                {"order_id": None, "amount": 7, "region": "west", "product_category": "y"},
            ],
            "analysis_agent": {
                "trends": ["east 区域上升"],
                "anomalies": [],
                "insights": [],
                "recommendations": [],
            },
        },
        expected_sequence=[
            "trigger_start",
            "read_from_hologres",
            "clean_and_aggregate",
            "analysis_agent",
            "display_result",
        ],
    ),
    _MatrixCase(
        case_id="data_transform_pipeline",
        example_file="data_transform_pipeline/data_transform_pipeline.yaml",
        inputs={},
        stub_outputs={
            # 数据集形状 = list（assemble_single_reference expected_kind="dataset"）；
            # 第三行 category=None 供 handle_nulls(drop_row) 确定性剔除
            "read_data": [
                {"id": 1, "category": "a", "value": 10},
                {"id": 2, "category": "a", "value": 5},
                {"id": 3, "category": None, "value": 7},
                {"id": 4, "category": "b", "value": 3},
            ],
        },
        expected_sequence=[
            "trigger_start",
            "read_data",
            "filter_data",
            "aggregate_data",
            "display_result",
        ],
    ),
]

# 4 套 N/A 范例（spec §0.2 预扫表 + DC-B4-02）：
# - hard_boundary=True：运行期子集校验拒绝（_validate_subset 抛 WF_VALIDATION_FAILED）；
# - supervisor_routing：拓扑为条件分支，但语义为监督者动态路由模式，
#   按 DC-B4-02 裁决 3 归界排除⑤（静态标注，不经矩阵执行）。
_NA_CASES = [
    {
        "case_id": "parallel_execution",
        "example_file": "parallel_execution/parallel_execution.yaml",
        "reason": "排除①：并行扇出/汇聚（CONTROL_FLOW kind=parallel_join）",
        "hard_boundary": True,
    },
    {
        "case_id": "subworkflow_nesting",
        "example_file": "subworkflow_nesting/subworkflow_nesting.yaml",
        "reason": "排除⑤：子工作流嵌套（SUBWORKFLOW 节点）",
        "hard_boundary": True,
    },
    {
        "case_id": "hierarchical_delegation",
        "example_file": "hierarchical_delegation/hierarchical_delegation.yaml",
        "reason": "排除⑤：子工作流嵌套（SUBWORKFLOW 节点 ×2）",
        "hard_boundary": True,
    },
    {
        "case_id": "supervisor_routing",
        "example_file": "supervisor_routing/supervisor_routing.yaml",
        "reason": "排除⑤：监督者动态路由模式（DC-B4-02 裁决 3 归界；拓扑为分支，语义为 agent 驱动监督者路由）",
        "hard_boundary": False,
    },
]


class _StubOutputExecutor:
    """确定性桩执行器：按 node_id 返回固定输出（agent/data_source 注入用）。

    E4 注入口径：failing_node_id 命中时返回 success=False + 注入错误码
    （双引擎经同一 NodeExecutionError 映射链，错误码可比）。
    """

    def __init__(
        self,
        node_type: NodeType,
        stub_outputs: Dict[str, Any],
        failing_node_id: Optional[str] = None,
        injected_error_code: Optional[str] = None,
    ) -> None:
        self._node_type = node_type
        self._stub_outputs = stub_outputs
        self._failing_node_id = failing_node_id
        self._injected_error_code = injected_error_code

    @property
    def node_type(self) -> NodeType:
        return self._node_type

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        node_id = context.node_definition.node_id
        if node_id == self._failing_node_id:
            return NodeExecutionResult(
                success=False,
                error_code=self._injected_error_code or "MATRIX_E4_INJECTED",
                error_message="E4 矩阵注入节点失败（确定性桩）",
            )
        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.output_name,
            output_value=self._stub_outputs.get(node_id),
        )


class _RecordingExecutor:
    """记录层执行器：捕获触发序列与逐节点状态视图（E2 断言数据源）。

    包装真实/桩执行器，不改其行为；双引擎共享同一注册表实例，
    故观察缓冲按引擎分段（reset_observations）。
    """

    def __init__(self, inner: Any, harness: "_MatrixHarness") -> None:
        self._inner = inner
        self._harness = harness

    @property
    def node_type(self) -> NodeType:
        return self._inner.node_type

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        self._harness.node_sequence.append(context.node_definition.node_id)
        self._harness.node_state_views.append(
            dict(context.shared_state.node_outputs)
        )
        return await self._inner.execute(context)


class _MatrixHarness:
    """确定性等价矩阵 harness（design §4.6）。

    双引擎共享同一执行器注册表实例（节点语义单源，INV-B4-01）：
    真实执行器（trigger/control_flow/output_display/data_transform——纯内存确定性）
    + 确定性桩（agent/data_source——按 node_id 固定输出）。
    """

    def __init__(
        self,
        case: _MatrixCase,
        failing_node_id: Optional[str] = None,
        injected_error_code: Optional[str] = None,
    ) -> None:
        self.case = case
        self.node_sequence: List[str] = []
        self.node_state_views: List[Dict[str, Any]] = []
        self.registry = NodeExecutorRegistry()
        self.registry.register(self._recording(TriggerNodeExecutor()))
        self.registry.register(self._recording(ControlFlowNodeExecutor()))
        self.registry.register(self._recording(OutputDisplayNodeExecutor()))
        self.registry.register(self._recording(DataTransformNodeExecutor()))
        self.registry.register(
            self._recording(
                _StubOutputExecutor(
                    NodeType.AGENT,
                    case.stub_outputs,
                    failing_node_id,
                    injected_error_code,
                )
            )
        )
        self.registry.register(
            self._recording(
                _StubOutputExecutor(
                    NodeType.DATA_SOURCE,
                    case.stub_outputs,
                    failing_node_id,
                    injected_error_code,
                )
            )
        )

    def _recording(self, inner: Any) -> _RecordingExecutor:
        return _RecordingExecutor(inner, self)

    def reset_observations(self) -> None:
        """按引擎分段清空观察缓冲（注册表实例保持共享）。"""
        self.node_sequence.clear()
        self.node_state_views.clear()

    async def run_builtin(self, definition) -> tuple:
        """内置引擎链路：GraphCompiler → Scheduler（生产语义母本）。

        返回 (compiled, final_state, error)：error 非 None 时 final_state
        为 None（调度器抛出的 DomainError 原样捕获，供失败态错误码对比）。
        """
        compiled = await GraphCompiler().compile(definition)
        scheduler = Scheduler(executor_registry=self.registry)
        initial = SharedState(
            instance_id="matrix-builtin",
            node_outputs=dict(self.case.inputs),
            current_node_id=compiled.entry_node_id,
        )
        try:
            final_state = await scheduler.execute(
                compiled, initial, "matrix-builtin", "matrix-trace-builtin"
            )
            return compiled, final_state, None
        except DomainError as error:
            return compiled, None, error

    async def run_langgraph(self, definition) -> tuple:
        """第二运行时链路：LangGraphAdapter（唯一 langgraph import 位）。"""
        adapter = LangGraphAdapter(executor_registry=self.registry)
        compiled = await adapter.compile(definition)
        instance = await adapter.start(
            StartInstanceRequest(
                definition_id=definition.definition_id,
                inputs=dict(self.case.inputs),
                trigger_type=TriggerType.MANUAL,
            )
        )
        return compiled, instance


def _parse_example(example_file: str):
    yaml_path = EXAMPLES_ROOT / example_file
    assert yaml_path.exists(), f"范例文件不存在: {yaml_path}"
    return YamlWorkflowParser().parse(yaml_path.read_text(encoding="utf-8"))


class TestLangGraphEquivalenceMatrix:
    """12 范例 × 双运行时等价矩阵（E1~E5）。"""

    @requires_langgraph
    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", MATRIX_IN, ids=lambda c: c.case_id)
    async def test_in_examples_e1_to_e5(self, case: _MatrixCase):
        """8 套 IN 范例：E1 编译对比 / E2 序列+快照 / E3 终态（或错误码）/ E5 幂等。

        成功收敛用例：E3 = instance.outputs == final.node_outputs；
        确定性失败用例（expected_error_code 非 None）：E3 退化为错误码对比
        ——双引擎同码失败即等价（如 reflection_iteration 的 WF_LOOP_NOT_CONVERGED）。
        """
        definition = _parse_example(case.example_file)
        harness = _MatrixHarness(case)

        # ---- 内置引擎（语义母本）----
        compiled_builtin, final_builtin, builtin_error = await harness.run_builtin(
            definition
        )
        assert harness.node_sequence == case.expected_sequence, (
            f"内置引擎触发序列与预期不符：{harness.node_sequence}"
        )
        builtin_sequence = list(harness.node_sequence)
        builtin_views = [dict(v) for v in harness.node_state_views]

        # ---- 第二运行时（LangGraph）----
        harness.reset_observations()
        compiled_lg, instance_lg = await harness.run_langgraph(definition)

        # E1 编译产物对比（字段级：同一编译器双跑产物相等）+ 结构对账
        assert compiled_lg == compiled_builtin
        assert compiled_lg.entry_node_id == compiled_builtin.entry_node_id
        assert compiled_lg.node_order == compiled_builtin.node_order
        assert compiled_lg.terminal_node_ids == compiled_builtin.terminal_node_ids

        # E2 触发序列 + 逐节点输出快照（双侧对齐 + 第三参照源）
        assert harness.node_sequence == builtin_sequence
        assert harness.node_state_views == builtin_views
        assert harness.node_sequence == case.expected_sequence

        if case.expected_error_code is not None:
            # 确定性失败用例：双引擎同码失败即等价（E3 错误码对比）
            assert builtin_error is not None
            assert builtin_error.code == case.expected_error_code
            assert final_builtin is None
            assert instance_lg.status == InstanceStatus.FAILED
            assert instance_lg.error is not None
            assert instance_lg.error.get("code") == case.expected_error_code
        else:
            # 成功收敛用例：E3 终态 node_outputs 字段值对比
            assert builtin_error is None
            assert final_builtin is not None
            assert final_builtin.node_outputs, "内置引擎终态输出不应为空"
            assert instance_lg.status == InstanceStatus.SUCCEEDED
            assert instance_lg.outputs == final_builtin.node_outputs

        # E5 幂等：同输入重复执行输出一致（确定性桩，白名单外零漂移）
        harness.reset_observations()
        _, instance_lg_repeat = await harness.run_langgraph(definition)
        if case.expected_error_code is not None:
            assert instance_lg_repeat.status == InstanceStatus.FAILED
            assert instance_lg_repeat.error is not None
            assert instance_lg_repeat.error.get("code") == case.expected_error_code
        else:
            assert instance_lg_repeat.status == InstanceStatus.SUCCEEDED
            assert instance_lg_repeat.outputs == instance_lg.outputs
        assert harness.node_sequence == case.expected_sequence

    @requires_langgraph
    @pytest.mark.asyncio
    async def test_e4_error_code_parity(self):
        """E4：注入节点失败，双引擎错误码一致（同 NodeExecutionError 映射链）。"""
        case = next(c for c in MATRIX_IN if c.case_id == "conditional_loop_pipeline")
        injected_code = "MATRIX_E4_INJECTED"
        definition = _parse_example(case.example_file)
        harness = _MatrixHarness(
            case, failing_node_id="generate_draft", injected_error_code=injected_code
        )

        # 内置引擎：Scheduler.execute 抛 NodeExecutionError（同码回退链）
        compiled = await GraphCompiler().compile(definition)
        scheduler = Scheduler(executor_registry=harness.registry)
        initial = SharedState(
            instance_id="matrix-e4-builtin",
            node_outputs=dict(case.inputs),
            current_node_id=compiled.entry_node_id,
        )
        with pytest.raises(NodeExecutionError) as exc_info:
            await scheduler.execute(compiled, initial, "matrix-e4-builtin", "matrix-trace-e4")
        assert exc_info.value.code == injected_code

        # 第二运行时：instance FAILED + 同错误码
        harness.reset_observations()
        _, instance_lg = await harness.run_langgraph(definition)
        assert instance_lg.status == InstanceStatus.FAILED
        assert instance_lg.error is not None
        assert instance_lg.error.get("code") == injected_code

    @pytest.mark.asyncio
    @pytest.mark.parametrize("na_case", _NA_CASES, ids=lambda c: c["case_id"])
    async def test_na_examples_out_of_subset(self, na_case):
        """4 套 N/A 范例：静态标注排除原因；硬边界项运行期子集校验拒绝。"""
        definition = _parse_example(na_case["example_file"])
        compiled = await GraphCompiler().compile(definition)

        if na_case["hard_boundary"]:
            # 运行期安全网：子集校验拒绝（WF_VALIDATION_FAILED，不静默误执行）
            adapter = LangGraphAdapter(executor_registry=NodeExecutorRegistry())
            with pytest.raises(DomainError) as exc_info:
                adapter._validate_subset(compiled)
            assert exc_info.value.code == WF_VALIDATION_FAILED
            assert na_case["case_id"] in {
                "parallel_execution",
                "subworkflow_nesting",
                "hierarchical_delegation",
            }
        else:
            # supervisor_routing：DC-B4-02 裁决归界（静态标注，不经矩阵执行）
            assert na_case["case_id"] == "supervisor_routing"
            node_types = {n.node_type for n in compiled.nodes}
            assert NodeType.CONTROL_FLOW in node_types
