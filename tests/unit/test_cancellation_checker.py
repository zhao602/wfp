# =============================================================================
# M3 scheduler 协作式取消检查器测试
# （tests/unit/test_cancellation_checker.py）
#
# 职责：验证 P5 实例级取消协作式兜底（roadmap design D2）：
#       cancellation_checker 缺省 None 零行为（HC-01）；节点边界轮询
#       True 即 CancelledError 终止；与 run_and_record CancelledError
#       分支集成落 cancelled 终态（worker 协作路径全链路）。
# 设计意图：
#   1. 四节点顺序 fixture（trigger/data_source/agent/tool 计数桩）——
#      节点边界语义可精确断言（前 N 节点已执行、后续节点未执行）；
#   2. 抢占式 task.cancel 主路径由 test_instance_cancel_results.py /
#      test_worker_cancel_discovery.py 覆盖，本文件锁协作式兜底行为。
# =============================================================================

import asyncio
from pathlib import Path

import pytest

from orchestration_platform.application.execution_runner import (
    InstanceRegistry,
    InstanceRuntime,
    run_and_record,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import (
    NodeExecutorRegistry,
)
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "valid_workflow_definitions"
    / "valid_sequential_four_nodes.yaml"
)


class _CountingStubExecutor:
    """计数桩执行器：记录节点调用（节点边界断言用）。"""

    def __init__(self, node_type: NodeType) -> None:
        self._node_type = node_type
        self.node_calls: dict = {}

    @property
    def node_type(self) -> NodeType:
        return self._node_type

    async def execute(self, context) -> NodeExecutionResult:
        node_id = context.node_definition.node_id
        self.node_calls[node_id] = self.node_calls.get(node_id, 0) + 1
        return NodeExecutionResult(
            success=True,
            output_name=getattr(
                context.node_definition, "effective_output_name", None
            ),
            output_value={"stub": node_id},
            metadata={},
        )


def _build_registry() -> NodeExecutorRegistry:
    """四类节点全计数桩注册表（零外部调用）。"""
    registry = NodeExecutorRegistry()
    for node_type in (
        NodeType.TRIGGER,
        NodeType.DATA_SOURCE,
        NodeType.AGENT,
        NodeType.TOOL,
    ):
        registry.register(_CountingStubExecutor(node_type))
    return registry


async def _compile_four_node():
    definition = YamlWorkflowParser().parse(_FIXTURE.read_text(encoding="utf-8"))
    return await GraphCompiler().compile(definition)


def _executed_nodes(registry: NodeExecutorRegistry) -> list:
    """收集已执行节点 id（按注册表桩计数）。"""
    executed: list = []
    for node_type in (
        NodeType.TRIGGER,
        NodeType.DATA_SOURCE,
        NodeType.AGENT,
        NodeType.TOOL,
    ):
        executed.extend(registry.get_executor(node_type).node_calls.keys())
    return executed


# =============================================================================
# A. 缺省零行为（HC-01）
# =============================================================================


class TestDefaultZeroBehavior:

    async def test_none_checker_runs_to_completion(self):
        """缺省 None：四节点全执行，零行为（既有调用点等价）。"""
        registry = _build_registry()
        scheduler = Scheduler(executor_registry=registry)
        compiled = await _compile_four_node()

        final_state = await scheduler.execute(
            compiled,
            SharedState(instance_id="inst-cc-ok", node_outputs={"topic": "t"}),
            "inst-cc-ok",
            "trace-cc",
        )
        assert final_state is not None
        assert len(_executed_nodes(registry)) == 4, "四节点全部执行"


# =============================================================================
# B. 节点边界取消语义（design D2）
# =============================================================================


class TestNodeBoundaryCancellation:

    async def test_immediate_true_cancels_before_first_node(self):
        """开局即 True：首节点前终止，零节点执行。"""
        registry = _build_registry()
        scheduler = Scheduler(
            executor_registry=registry, cancellation_checker=lambda: True
        )
        compiled = await _compile_four_node()

        with pytest.raises(asyncio.CancelledError):
            await scheduler.execute(
                compiled,
                SharedState(
                    instance_id="inst-cc-now", node_outputs={"topic": "t"}
                ),
                "inst-cc-now",
                "trace-cc",
            )
        assert _executed_nodes(registry) == []

    async def test_flip_after_two_nodes_cancels_at_boundary(self):
        """第 2 节点后翻 True：前 2 节点已执行、后 2 节点不再执行。"""
        registry = _build_registry()
        calls = {"n": 0}

        # 包装桩：执行后计数（先捕获原方法，避免重赋值自递归）
        for node_type in (
            NodeType.TRIGGER,
            NodeType.DATA_SOURCE,
            NodeType.AGENT,
            NodeType.TOOL,
        ):
            stub = registry.get_executor(node_type)
            original_execute = stub.execute

            async def _wrapped(context, _original=original_execute):
                result = await _original(context)
                calls["n"] += 1
                return result

            stub.execute = _wrapped  # type: ignore[method-assign]

        scheduler = Scheduler(
            executor_registry=registry,
            cancellation_checker=lambda: calls["n"] >= 2,
        )
        compiled = await _compile_four_node()

        with pytest.raises(asyncio.CancelledError):
            await scheduler.execute(
                compiled,
                SharedState(
                    instance_id="inst-cc-mid", node_outputs={"topic": "t"}
                ),
                "inst-cc-mid",
                "trace-cc",
            )
        executed = _executed_nodes(registry)
        assert len(executed) == 2, f"恰前 2 节点执行（实际 {executed}）"
        assert "trigger_manual" in executed
        assert "read_dataset" in executed


# =============================================================================
# C. run_and_record 集成（协作取消 → cancelled 终态全链路）
# =============================================================================


class TestRunAndRecordIntegration:

    async def test_cooperative_cancel_lands_cancelled_terminal(self):
        """协作取消经 run_and_record CancelledError 分支：registry 落
        cancelled 终态 + INSTANCE_CANCELLED 事件 + 重新抛出。"""
        registry = InstanceRegistry()
        rt = InstanceRuntime(
            instance_id="inst-cc-rr", definition_id="sequential_four_nodes"
        )
        registry.register(rt)
        executor_registry = _build_registry()
        flag = {"cancel": False}

        # 首节点完成后翻标志：节点边界取消确定性触发（零 sleep 竞态）
        stub = executor_registry.get_executor(NodeType.TRIGGER)
        original_execute = stub.execute

        async def _flip_after_first(context):
            result = await original_execute(context)
            flag["cancel"] = True
            return result

        stub.execute = _flip_after_first  # type: ignore[method-assign]

        scheduler = Scheduler(
            executor_registry=executor_registry,
            cancellation_checker=lambda: flag["cancel"],
        )
        compiled = await _compile_four_node()

        with pytest.raises(asyncio.CancelledError):
            await run_and_record(
                scheduler,
                compiled,
                SharedState(
                    instance_id="inst-cc-rr", node_outputs={"topic": "t"}
                ),
                "inst-cc-rr",
                "trace-cc-rr",
                registry,
            )

        rt_after = registry.get("inst-cc-rr")
        assert rt_after.status == "cancelled"
        assert rt_after.finished_at is not None
        event_types = [e["event_type"] for e in rt_after.events]
        assert "INSTANCE_CANCELLED" in event_types
