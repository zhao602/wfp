# =============================================================================
# 控制流节点执行器单元测试（tests/unit/test_control_flow_node_executor.py）
#
# 覆盖验收：A3（条件分支唯一路径选择、循环终止与最大迭代上限）
# =============================================================================

import pytest

from orchestration_platform.engine.nodes.control_flow_node_executor import (
    ControlFlowNodeExecutor,
)
from orchestration_platform.engine.nodes.base import NodeExecutionContext
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.enums import NodeType


class TestControlFlowNodeExecutor:
    """控制流节点执行器测试。"""

    @pytest.fixture
    def executor(self):
        return ControlFlowNodeExecutor()

    def _make_branch_node(self, conditions, default_target="default_tgt"):
        """构造条件分支节点定义。"""
        from orchestration_platform.domain.models.node_configurations import (
            ControlFlowNodeConfiguration,
        )
        from orchestration_platform.domain.models.enums import ControlFlowKind

        config = ControlFlowNodeConfiguration(
            node_type=NodeType.CONTROL_FLOW,
            control_flow_kind=ControlFlowKind.CONDITIONAL_BRANCH,
            conditions=conditions,
            default_branch_target=default_target,
        )
        return NodeDefinition(
            node_id="branch_node",
            name="分支节点",
            node_type=NodeType.CONTROL_FLOW,
            configuration=config,
        )

    def _make_loop_node(self, body_entry, termination_condition, max_iter=5):
        """构造循环节点定义。"""
        from orchestration_platform.domain.models.node_configurations import (
            ControlFlowNodeConfiguration,
        )
        from orchestration_platform.domain.models.enums import ControlFlowKind

        config = ControlFlowNodeConfiguration(
            node_type=NodeType.CONTROL_FLOW,
            control_flow_kind=ControlFlowKind.LOOP,
            loop_body_entry=body_entry,
            loop_termination_condition=termination_condition,
            max_iterations=max_iter,
        )
        return NodeDefinition(
            node_id="loop_node",
            name="循环节点",
            node_type=NodeType.CONTROL_FLOW,
            configuration=config,
        )

    @pytest.mark.asyncio
    async def test_conditional_branch_selects_matching_path(self, executor):
        """条件分支执行器应标记成功并返回控制流类型（求值由调度器承担）。"""
        from orchestration_platform.domain.models.node_configurations import BranchCondition

        node = self._make_branch_node([
            BranchCondition(target_node_id="path_a", expression="value > 10", label="A"),
            BranchCondition(target_node_id="path_b", expression="value <= 10", label="B"),
        ])
        context = NodeExecutionContext(
            node_definition=node,
            shared_state=SharedState(instance_id="test-instance", node_outputs={"value": 15}),
            instance_id="test-instance",
            trace_id="test-trace",
            round_index=0,
        )
        result = await executor.execute(context)
        assert result.success
        assert result.output_value is None
        assert result.metadata.get("control_flow_kind") == "conditional_branch"

    @pytest.mark.asyncio
    async def test_conditional_branch_default_when_no_match(self, executor):
        """全不命中时执行器仍标记成功（默认分支选择由调度器承担）。"""
        from orchestration_platform.domain.models.node_configurations import BranchCondition

        node = self._make_branch_node([
            BranchCondition(target_node_id="path_a", expression="value > 100"),
        ], default_target="default_tgt")
        context = NodeExecutionContext(
            node_definition=node,
            shared_state=SharedState(instance_id="test-instance", node_outputs={"value": 5}),
            instance_id="test-instance",
            trace_id="test-trace",
            round_index=0,
        )
        result = await executor.execute(context)
        assert result.success
        assert result.output_value is None
        assert result.metadata.get("control_flow_kind") == "conditional_branch"

    @pytest.mark.asyncio
    async def test_loop_termination_condition_met(self, executor):
        """循环终止条件满足时执行器标记成功（终止判定由调度器承担）。"""
        node = self._make_loop_node(
            body_entry="body_node",
            termination_condition="score >= 80",
            max_iter=5,
        )
        context = NodeExecutionContext(
            node_definition=node,
            shared_state=SharedState(instance_id="test-instance", node_outputs={"score": 85}),
            instance_id="test-instance",
            trace_id="test-trace",
            round_index=3,
        )
        result = await executor.execute(context)
        assert result.success
        assert result.output_value is None
        assert result.metadata.get("control_flow_kind") == "loop"

    @pytest.mark.asyncio
    async def test_loop_max_iterations_enforced(self, executor):
        """达到最大迭代次数时执行器标记成功（迭代计数由调度器承担）。"""
        node = self._make_loop_node(
            body_entry="body_node",
            termination_condition="score >= 100",
            max_iter=5,
        )
        context = NodeExecutionContext(
            node_definition=node,
            shared_state=SharedState(instance_id="test-instance", node_outputs={"score": 50}),
            instance_id="test-instance",
            trace_id="test-trace",
            round_index=5,
        )
        result = await executor.execute(context)
        assert result.success
        assert result.output_value is None
        assert result.metadata.get("control_flow_kind") == "loop"
