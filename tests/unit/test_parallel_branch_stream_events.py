# =============================================================================
# 并行分支事件桥接进实例事件流测试（B13 U2 T2）
#
# 覆盖（design D2）：
#   1. 真实 NodeEventCallbackImpl + InstanceRegistry：PARALLEL_BRANCH_STARTED/
#      COMPLETED 事件进入实例流（结构：node_id=join / branch_id 顶层 /
#      payload{join_node_id, branch_entry_id, status}；STARTED 声明序、
#      每分支 STARTED 先于 COMPLETED）
#   2. 旧形态回调（无 on_parallel_branch 方法）：调度器不炸（getattr 兜底）
#   3. 治理总线早退陷阱：domain_event_publisher=None 时泳道事件仍入流
#      （桥接置于 _publish_parallel_event 早退之前）
#   4. worker 模式零代码覆盖：同一 NodeEventCallbackImpl（导入同一性断言）
# =============================================================================

from typing import List, Optional

from orchestration_platform.application.execution_runner import (
    InstanceRegistry,
    InstanceRuntime,
    NodeEventCallbackImpl,
)
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser


FANOUT_YAML = """
spec_version: "1.1"
definition_id: branch_stream_flow
name: 并行分支事件桥接测试
definition_version: 1
inputs:
  - name: query
    type: string
    required: true
    description: 查询主题
outputs:
  - name: merged
    type: object
    description: 汇总输出
nodes:
  - node_id: trigger_start
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual
      input_template:
        query: query
  - node_id: fork
    name: 分叉源
    node_type: agent
    output_name: fork_out
    configuration:
      model_identifier: stub-model
      system_prompt: >-
        分叉源
      inputs:
        - reference: query
  - node_id: b1
    name: 分支一
    node_type: agent
    output_name: b1_out
    configuration:
      model_identifier: stub-model
      system_prompt: >-
        分支一
      inputs:
        - reference: fork_out
  - node_id: b2
    name: 分支二
    node_type: agent
    output_name: b2_out
    configuration:
      model_identifier: stub-model
      system_prompt: >-
        分支二
      inputs:
        - reference: fork_out
  - node_id: join
    name: 并行汇聚
    node_type: control_flow
    configuration:
      control_flow_kind: parallel_join
      parallel_branches:
        - b1
        - b2
      wait_policy: wait_all
      parallel_failure_strategy: fast_fail
  - node_id: after_join
    name: 汇合后
    node_type: agent
    output_name: merged
    configuration:
      model_identifier: stub-model
      system_prompt: >-
        汇合后
      inputs:
        - reference: b1_out
        - reference: b2_out
edges:
  - {edge_id: e0, source_node_id: trigger_start, target_node_id: fork}
  - {edge_id: e1, source_node_id: fork, target_node_id: b1}
  - {edge_id: e2, source_node_id: fork, target_node_id: b2}
  - {edge_id: e3, source_node_id: b1, target_node_id: join}
  - {edge_id: e4, source_node_id: b2, target_node_id: join}
  - {edge_id: e5, source_node_id: join, target_node_id: after_join}
"""


class _StubTriggerExecutor:
    @property
    def node_type(self):
        return NodeType.TRIGGER

    async def execute(self, ctx):
        return NodeExecutionResult(
            success=True, output_name="trigger", output_value={"started": True}
        )


class _StubAgentExecutor:
    @property
    def node_type(self):
        return NodeType.AGENT

    async def execute(self, ctx):
        return NodeExecutionResult(
            success=True,
            output_name=ctx.node_definition.effective_output_name,
            output_value={"result": f"done_{ctx.node_definition.node_id}"},
        )


class _StubControlFlowExecutor:
    @property
    def node_type(self):
        return NodeType.CONTROL_FLOW

    async def execute(self, ctx):
        return NodeExecutionResult(success=True, output_name=None, output_value=None)


class _CaptureCheckpointRepo:
    def __init__(self):
        self.checkpoints: List[Checkpoint] = []

    async def append(self, checkpoint: Checkpoint) -> str:
        self.checkpoints.append(checkpoint)
        return checkpoint.checkpoint_id

    async def latest_for_instance(self, instance_id: str) -> Optional[Checkpoint]:
        best = None
        for cp in self.checkpoints:
            if cp.instance_id == instance_id and (
                best is None or cp.round_index > best.round_index
            ):
                best = cp
        return best


class _LegacyCallback:
    """旧形态回调：仅 Protocol 必选方法，无 on_parallel_branch。"""

    async def on_node_started(self, instance_id, node_id, trace_id=""):
        return None

    async def on_node_completed(self, instance_id, node_id, trace_id="", payload=None):
        return None

    async def on_node_failed(self, instance_id, node_id, trace_id="", error=""):
        return None


async def _compile_doc(yaml_text: str):
    definition = YamlWorkflowParser().parse(yaml_text)
    return await GraphCompiler().compile(definition)


def _make_instance_registry(instance_id: str) -> InstanceRegistry:
    registry = InstanceRegistry()
    registry.register(InstanceRuntime(instance_id, "branch_stream_flow"))
    return registry


def _build_scheduler(callback, publisher=None) -> Scheduler:
    registry = NodeExecutorRegistry()
    registry.register(_StubTriggerExecutor())
    registry.register(_StubAgentExecutor())
    registry.register(_StubControlFlowExecutor())
    return Scheduler(
        executor_registry=registry,
        checkpoint_repository=_CaptureCheckpointRepo(),
        domain_event_publisher=publisher,
        event_callback=callback,
    )


def _branch_events(events: List[dict], event_type: str) -> List[dict]:
    return [e for e in events if e["event_type"] == event_type]


class TestParallelBranchStreamEvents:
    async def test_branch_events_enter_instance_stream(self):
        compiled = await _compile_doc(FANOUT_YAML)
        assert "join" in compiled.parallel_regions

        instance_id = "pbs-stream"
        instance_registry = _make_instance_registry(instance_id)
        scheduler = _build_scheduler(NodeEventCallbackImpl(instance_registry))

        await scheduler.execute(
            compiled,
            SharedState(
                instance_id=instance_id,
                node_outputs={"query": "topic"},
                current_node_id=compiled.entry_node_id,
            ),
            instance_id,
            "pbs-trace-1",
        )

        events = instance_registry.get_events(instance_id)
        started = _branch_events(events, "PARALLEL_BRANCH_STARTED")
        completed = _branch_events(events, "PARALLEL_BRANCH_COMPLETED")
        assert len(started) == 2
        assert len(completed) == 2

        assert [e["branch_id"] for e in started] == ["b1", "b2"]
        for event in started + completed:
            assert event["node_id"] == "join"
            assert event["instance_id"] == instance_id
            assert event["event_id"]
            assert event["timestamp"]
            assert event["payload"]["join_node_id"] == "join"
            assert event["payload"]["branch_entry_id"] == event["branch_id"]

        assert all(e["payload"]["status"] == "succeeded" for e in completed)
        for branch in ("b1", "b2"):
            started_at = next(
                i
                for i, e in enumerate(events)
                if e["event_type"] == "PARALLEL_BRANCH_STARTED"
                and e["branch_id"] == branch
            )
            completed_at = next(
                i
                for i, e in enumerate(events)
                if e["event_type"] == "PARALLEL_BRANCH_COMPLETED"
                and e["branch_id"] == branch
            )
            assert started_at < completed_at

        assert any(e["event_type"] == "NODE_STARTED" for e in events)
        assert any(e["event_type"] == "NODE_COMPLETED" for e in events)

    async def test_legacy_callback_without_on_parallel_branch_does_not_crash(self):
        compiled = await _compile_doc(FANOUT_YAML)
        scheduler = _build_scheduler(_LegacyCallback())

        final_state = await scheduler.execute(
            compiled,
            SharedState(
                instance_id="pbs-legacy",
                node_outputs={"query": "topic"},
                current_node_id=compiled.entry_node_id,
            ),
            "pbs-legacy",
            "pbs-trace-2",
        )

        assert "fork_out" in final_state.node_outputs
        assert "b1_out" in final_state.node_outputs
        assert "b2_out" in final_state.node_outputs
        assert "merged" in final_state.node_outputs

    async def test_publisher_none_branch_events_still_reach_stream(self):
        compiled = await _compile_doc(FANOUT_YAML)
        instance_id = "pbs-no-publisher"
        instance_registry = _make_instance_registry(instance_id)
        scheduler = _build_scheduler(NodeEventCallbackImpl(instance_registry))

        await scheduler.execute(
            compiled,
            SharedState(
                instance_id=instance_id,
                node_outputs={"query": "topic"},
                current_node_id=compiled.entry_node_id,
            ),
            instance_id,
            "pbs-trace-3",
        )

        events = instance_registry.get_events(instance_id)
        assert len(_branch_events(events, "PARALLEL_BRANCH_STARTED")) == 2
        assert len(_branch_events(events, "PARALLEL_BRANCH_COMPLETED")) == 2

    def test_worker_mode_shares_same_impl(self):
        from orchestration_platform.worker import execution_worker as worker_module

        assert worker_module.NodeEventCallbackImpl is NodeEventCallbackImpl
