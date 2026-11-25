# =============================================================================
# 失败节点输入快照测试（B13 U3 T6）
#
# 覆盖（design D6）：
#   1. 调度器失败路径：run_log_recorder 缺席时现场补采快照，经
#      NodeEventCallbackImpl.on_node_failed_with_input 进入实例事件流
#      （NODE_FAILED payload：error + input{...}，下游节点不再执行）；
#   2. 超限截断：超过 log_max_payload_bytes 的快照退化为带
#      ...[TRUNCATED] 标记的前缀字符串（Truncator 语义）；
#   3. 脱敏红线：字段名黑名单命中（password）→ [REDACTED]，其余字段
#      原样保留，入参不被就地修改（RunLogRedactor 副本语义）；
#   4. input_snapshot=None：payload 不含 input 键（旧语义等价）；
#   5. 旧形态回调兜底：无 on_node_failed_with_input 方法时回落
#      on_node_failed（getattr 兜底），error 参数不丢。
# =============================================================================

from typing import List, Optional, Tuple

import pytest

from orchestration_platform.application.execution_runner import (
    InstanceRegistry,
    InstanceRuntime,
    NodeEventCallbackImpl,
)
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import NodeExecutionError
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser


FAIL_YAML = """
spec_version: "1.1"
definition_id: failed_input_flow
name: 失败输入快照测试
definition_version: 1
inputs:
  - name: query
    type: string
    required: true
    description: 查询主题
outputs:
  - name: doomed_out
    type: object
    description: 失败节点输出（结构声明，失败时不产出）
nodes:
  - node_id: trigger_start
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual
      input_template:
        query: query
  - node_id: doomed
    name: 注定失败
    node_type: agent
    output_name: doomed_out
    configuration:
      model_identifier: stub-model
      system_prompt: >-
        注定失败
      inputs:
        - reference: query
  - node_id: after
    name: 失败后
    node_type: agent
    output_name: after_out
    configuration:
      model_identifier: stub-model
      system_prompt: >-
        失败后
      inputs:
        - reference: query
edges:
  - {edge_id: e0, source_node_id: trigger_start, target_node_id: doomed}
  - {edge_id: e1, source_node_id: doomed, target_node_id: after}
"""


class _StubTriggerExecutor:
    @property
    def node_type(self):
        return NodeType.TRIGGER

    async def execute(self, ctx):
        return NodeExecutionResult(
            success=True, output_name="trigger", output_value={"started": True}
        )


class _FailingAgentExecutor:
    """指定 node_id 失败（error_message 固定「stub 崩溃」）、其余成功的 agent 执行器。"""

    def __init__(self, fail_node_id: str):
        self._fail_node_id = fail_node_id

    @property
    def node_type(self):
        return NodeType.AGENT

    async def execute(self, ctx):
        if ctx.node_definition.node_id != self._fail_node_id:
            return NodeExecutionResult(
                success=True,
                output_name=ctx.node_definition.effective_output_name,
                output_value={"result": "done"},
            )
        return NodeExecutionResult(
            success=False,
            output_name=None,
            output_value=None,
            error_message="stub 崩溃",
        )


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


class _RecordingLegacyCallback:
    """旧形态回调：仅 Protocol 必选方法（无 on_node_failed_with_input），记录失败调用。"""

    def __init__(self):
        self.failed_calls: List[Tuple[str, str]] = []

    async def on_node_started(self, instance_id, node_id, trace_id=""):
        return None

    async def on_node_completed(self, instance_id, node_id, trace_id="", payload=None):
        return None

    async def on_node_failed(self, instance_id, node_id, trace_id="", error=""):
        self.failed_calls.append((node_id, error))
        return None


async def _compile_doc(yaml_text: str):
    definition = YamlWorkflowParser().parse(yaml_text)
    return await GraphCompiler().compile(definition)


def _make_instance_registry(instance_id: str) -> InstanceRegistry:
    registry = InstanceRegistry()
    registry.register(InstanceRuntime(instance_id, "failed_input_flow"))
    return registry


def _build_scheduler(callback) -> Scheduler:
    registry = NodeExecutorRegistry()
    registry.register(_StubTriggerExecutor())
    registry.register(_FailingAgentExecutor("doomed"))
    return Scheduler(
        executor_registry=registry,
        checkpoint_repository=_CaptureCheckpointRepo(),
        domain_event_publisher=None,
        event_callback=callback,
    )


def _events_of(events: List[dict], event_type: str) -> List[dict]:
    return [e for e in events if e["event_type"] == event_type]


class TestNodeFailedInputSnapshot:
    async def test_scheduler_failure_emits_input_snapshot(self):
        compiled = await _compile_doc(FAIL_YAML)

        instance_id = "nfis-snapshot"
        instance_registry = _make_instance_registry(instance_id)
        scheduler = _build_scheduler(NodeEventCallbackImpl(instance_registry))

        # 线性流 fast-fail：失败回调先入流，随后 execute 抛 NodeExecutionError
        with pytest.raises(NodeExecutionError, match="doomed"):
            await scheduler.execute(
                compiled,
                SharedState(
                    instance_id=instance_id,
                    node_outputs={"query": "topic"},
                    current_node_id=compiled.entry_node_id,
                ),
                instance_id,
                "nfis-trace-1",
            )

        events = instance_registry.get_events(instance_id)
        failed = _events_of(events, "NODE_FAILED")
        assert len(failed) == 1
        event = failed[0]
        assert event["node_id"] == "doomed"
        assert event["payload"]["error"] == "stub 崩溃"
        # run_log_recorder 缺席：调度器失败路径现场补采（node_outputs 整表快照）
        assert event["payload"]["input"] == {
            "query": "topic",
            "trigger": {"started": True},
        }
        # 失败后下游不再执行
        assert [e["node_id"] for e in _events_of(events, "NODE_COMPLETED")] == [
            "trigger_start"
        ]
        assert "after" not in [
            e["node_id"] for e in _events_of(events, "NODE_STARTED")
        ]

    async def test_oversize_snapshot_truncated_with_marker(self):
        instance_id = "nfis-truncated"
        registry = _make_instance_registry(instance_id)
        callback = NodeEventCallbackImpl(registry)

        max_bytes = get_settings().log_max_payload_bytes
        # 空格分隔短 token：避开值模式脱敏干扰（长随机串疑似密钥）与
        # URL 凭据模式的类字符长串二次方回溯（redactor 既有特性）
        snapshot = {"blob": "ab " * (max_bytes // 2 + 2048)}
        await callback.on_node_failed_with_input(
            instance_id, "doomed", "nfis-trace", "stub 崩溃",
            input_snapshot=snapshot,
        )

        events = registry.get_events(instance_id)
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["error"] == "stub 崩溃"
        truncated_input = payload["input"]
        assert isinstance(truncated_input, str)
        assert truncated_input.endswith("...[TRUNCATED]")
        assert len(truncated_input.encode("utf-8")) <= max_bytes
        assert len(truncated_input) > max_bytes // 2

    async def test_snapshot_redacts_sensitive_fields(self):
        instance_id = "nfis-redacted"
        registry = _make_instance_registry(instance_id)
        callback = NodeEventCallbackImpl(registry)

        snapshot = {"password": "super-secret", "note": "keep"}
        await callback.on_node_failed_with_input(
            instance_id, "doomed", "nfis-trace", "stub 崩溃",
            input_snapshot=snapshot,
        )

        payload = registry.get_events(instance_id)[0]["payload"]
        # 字段名黑名单红线（不可关）：password → [REDACTED]，其余原样
        assert payload["input"] == {"password": "[REDACTED]", "note": "keep"}
        # 入参不被就地修改（RunLogRedactor 副本语义）
        assert snapshot == {"password": "super-secret", "note": "keep"}

    async def test_none_snapshot_omits_input_key(self):
        instance_id = "nfis-none"
        registry = _make_instance_registry(instance_id)
        callback = NodeEventCallbackImpl(registry)

        await callback.on_node_failed_with_input(
            instance_id, "doomed", "nfis-trace", "stub 崩溃",
            input_snapshot=None,
        )

        payload = registry.get_events(instance_id)[0]["payload"]
        assert payload == {"error": "stub 崩溃"}
        assert "input" not in payload

    async def test_legacy_callback_falls_back_to_on_node_failed(self):
        compiled = await _compile_doc(FAIL_YAML)
        callback = _RecordingLegacyCallback()
        scheduler = _build_scheduler(callback)

        # 旧形态回调：失败回调先入流，随后 execute 抛 NodeExecutionError
        with pytest.raises(NodeExecutionError, match="doomed"):
            await scheduler.execute(
                compiled,
                SharedState(
                    instance_id="nfis-legacy",
                    node_outputs={"query": "topic"},
                    current_node_id=compiled.entry_node_id,
                ),
                "nfis-legacy",
                "nfis-trace-2",
            )

        assert callback.failed_calls == [("doomed", "stub 崩溃")]
