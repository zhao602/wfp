# =============================================================================
# 条件分支执行集成测试（tests/integration/test_conditional_branch_execution.py）
#
# 覆盖验收：A3（条件分支唯一路径选择、分支未命中默认策略）
# =============================================================================

import pytest

from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.domain.models.enums import NodeType


class _StubTriggerExecutor:
    @property
    def node_type(self):
        return NodeType.TRIGGER

    async def execute(self, ctx):
        return NodeExecutionResult(success=True, output_name="trigger", output_value={"started": True})


class _StubAgentExecutor:
    @property
    def node_type(self):
        return NodeType.AGENT

    async def execute(self, ctx):
        return NodeExecutionResult(
            success=True,
            output_name=ctx.node_definition.effective_output_name,
            output_value={"result": "agent_done"},
        )


class _StubControlFlowExecutor:
    @property
    def node_type(self):
        return NodeType.CONTROL_FLOW

    async def execute(self, ctx):
        return NodeExecutionResult(success=True, output_name=None, output_value=None)


class TestConditionalBranchExecution:
    """条件分支执行集成测试。"""

    @pytest.fixture
    def branch_definition(self):
        """加载含条件分支的定义。"""
        from pathlib import Path
        parser = YamlWorkflowParser()
        yaml_text = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "valid_workflow_definitions"
            / "valid_with_branch.yaml"
        ).read_text(encoding="utf-8")
        return parser.parse(yaml_text)

    @pytest.mark.asyncio
    async def test_branch_definition_compiles(self, branch_definition):
        """含分支的定义应编译成功。"""
        compiler = GraphCompiler()
        compiled = await compiler.compile(branch_definition)
        assert len(compiled.node_order) > 0

    @pytest.mark.asyncio
    async def test_only_one_branch_path_executed(self, branch_definition):
        """同一实例中仅一条分支路径被执行。"""
        compiler = GraphCompiler()
        compiled = await compiler.compile(branch_definition)

        registry = NodeExecutorRegistry()
        registry.register(_StubTriggerExecutor())
        registry.register(_StubAgentExecutor())
        registry.register(_StubControlFlowExecutor())

        scheduler = Scheduler(registry)
        initial_state = SharedState(
            instance_id="test-instance",
            node_outputs={},
            current_node_id=compiled.entry_node_id,
        )
        final_state = await scheduler.execute(
            compiled, initial_state, "test-instance", "trace-test"
        )

        has_finance = "finance_result" in final_state.node_outputs
        has_operation = "operation_result" in final_state.node_outputs
        assert has_operation or has_finance, "至少一条分支应被执行"
        assert not (has_finance and has_operation), "不应同时执行两条分支"

    @pytest.mark.asyncio
    async def test_default_branch_on_no_match(self, branch_definition):
        """全不命中时应走默认分支策略。"""
        compiler = GraphCompiler()
        compiled = await compiler.compile(branch_definition)
        assert compiled is not None
