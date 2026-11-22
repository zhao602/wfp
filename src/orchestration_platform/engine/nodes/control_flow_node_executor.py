# =============================================================================
# 控制流节点执行器（engine/nodes/control_flow_node_executor.py）
#
# 职责：覆盖条件分支、循环、并行汇聚、人工审核门四类子类型。
# 设计意图（对应 spec 5.3.1.13-14、design 2.5.3）：
#   1. 控制流节点禁止执行任何业务动作（无模型/工具/数据源调用）；
#   2. 条件分支/循环的求值由调度器承担，执行器仅返回控制信号；
#   3. 人工审核门挂起实例等待人工决策。
# =============================================================================

from orchestration_platform.domain.models.enums import (
    ControlFlowKind,
    NodeType,
)
from orchestration_platform.domain.models.node_configurations import ControlFlowNodeConfiguration
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)


class ControlFlowNodeExecutor:
    """控制流节点执行器：分支/循环/并行汇聚/人工审核门。

    控制流节点不执行业务动作——条件求值与循环控制由调度器承担，
    执行器仅返回成功标记与控制信号。
    """

    @property
    def node_type(self) -> NodeType:
        return NodeType.CONTROL_FLOW

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行控制流节点：按子类型返回控制信号。"""
        config: ControlFlowNodeConfiguration = context.node_definition.configuration

        if config.control_flow_kind == ControlFlowKind.CONDITIONAL_BRANCH:
            return self._execute_branch(context)

        if config.control_flow_kind == ControlFlowKind.LOOP:
            return self._execute_loop(context)

        if config.control_flow_kind == ControlFlowKind.PARALLEL_JOIN:
            return self._execute_parallel_join(context)

        if config.control_flow_kind == ControlFlowKind.HUMAN_REVIEW_GATE:
            return self._execute_review_gate(context)

        return NodeExecutionResult(
            success=True,
            output_name=None,
            output_value=None,
        )

    def _execute_branch(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """条件分支：求值由调度器承担，执行器仅标记成功。"""
        return NodeExecutionResult(
            success=True,
            output_name=None,
            output_value=None,
            metadata={"control_flow_kind": "conditional_branch"},
        )

    def _execute_loop(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """循环：计数与终止判定由调度器承担，执行器仅标记成功。"""
        return NodeExecutionResult(
            success=True,
            output_name=None,
            output_value=None,
            metadata={"control_flow_kind": "loop"},
        )

    def _execute_parallel_join(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """并行汇聚：等待策略与失败语义由调度器承担，执行器仅返回标记。"""
        config: ControlFlowNodeConfiguration = context.node_definition.configuration
        return NodeExecutionResult(
            success=True,
            output_name=None,
            output_value=None,
            metadata={
                "control_flow_kind": "parallel_join",
                "failure_strategy": config.parallel_failure_strategy.value,
                # B6 等待轴（Q2 用户裁决 2026-11-22：与失败轴正交）
                "wait_policy": config.wait_policy.value,
            },
        )

    def _execute_review_gate(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """人工审核门：挂起实例等待人工决策。

        执行器返回特殊标记，调度器据此将实例状态置为 WAITING_REVIEW。
        """
        config: ControlFlowNodeConfiguration = context.node_definition.configuration
        return NodeExecutionResult(
            success=True,
            output_name=None,
            output_value=None,
            metadata={
                "control_flow_kind": "human_review_gate",
                "review_instructions": config.review_instructions,
                "timeout_action": config.review_timeout_action,
                "suspend": True,
            },
        )
