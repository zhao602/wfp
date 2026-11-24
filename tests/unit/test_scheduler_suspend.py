# =============================================================================
# 人工审核门挂起单测（tests/unit/test_scheduler_suspend.py）
#
# 覆盖：arch_review_v2_tasks_wave2 任务 3.7 之一（CHG-53；AC-01 引擎/路由侧证据）
#       + 3.1 信号类冒烟 + 3.2 三重条件判定 + 3.3 挂起状态落点 + 3.4 重启恢复。
# 分层：
#   A. 信号与错误码——冒烟（继承关系/字段/标识码，不进 DomainError 处理器）；
#   B. 引擎层——三重条件正反例（缺一不挂起）/检查点先于信号/护栏轮次不计挂起；
#   C. 路由层——挂起状态落点（waiting_review/INSTANCE_SUSPENDED/发布点 P4）+
#      recover 重启恢复（恢复位点 completed=review 门 → 置 WAITING_REVIEW）。
# =============================================================================

from types import SimpleNamespace

import pytest

from orchestration_platform.domain.errors import (
    INSTANCE_NOT_WAITING_REVIEW,
    WF_BUDGET_EXCEEDED,
    WF_INSTANCE_SUSPENDED,
    DomainError,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.abstractions.engine_config import EngineSettings
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.engine.nodes.control_flow_node_executor import (
    ControlFlowNodeExecutor,
)
from orchestration_platform.engine.nodes.trigger_node_executor import TriggerNodeExecutor
from orchestration_platform.engine.scheduler import (
    Scheduler,
    WorkflowSuspendedSignal,
)
from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
    LocalFileCheckpointRepository,
)
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

# 含人工审核门的三节点流水线：trigger → review_gate → after_review
_REVIEW_GATE_YAML = """spec_version: "1.0"
definition_id: review_gate_flow
name: 人工审核门流水线
definition_version: 1

inputs:
  - name: topic
    type: string
    required: true
    description: 分析主题

outputs:
  - name: analysis
    type: object
    description: 分析结果

nodes:
  - node_id: trigger_manual
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual

  - node_id: review_gate
    name: 人工审核门
    node_type: control_flow
    configuration:
      control_flow_kind: human_review_gate
      review_instructions: 请审阅执行计划后放行
      review_timeout_action: reject

  - node_id: after_review
    name: 审阅后节点
    node_type: agent
    output_name: analysis
    configuration:
      model_identifier: primary-model
      system_prompt: 你是一名数据分析师。

edges:
  - edge_id: e1
    source_node_id: trigger_manual
    target_node_id: review_gate
  - edge_id: e2
    source_node_id: review_gate
    target_node_id: after_review
"""


# =============================================================================
# 测试基建
# =============================================================================

class _CountingStubExecutor:
    """计数桩执行器：按 node_id 统计执行次数；可注入任意 metadata（反例构造用）。"""

    def __init__(self, node_type: NodeType, metadata=None):
        self._node_type = node_type
        self._metadata = metadata or {}
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
            metadata=dict(self._metadata),
        )


class _RecordingPublisher:
    """发布记录桩：收集已发布领域事件（发布点断言用）。"""

    def __init__(self) -> None:
        self.events = []

    async def publish_domain_event(self, event) -> None:
        self.events.append(event)


def _build_gate_registry() -> NodeExecutorRegistry:
    """挂起正例注册表：trigger/control_flow 真实执行器 + agent 计数桩。"""
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())
    registry.register(ControlFlowNodeExecutor())
    registry.register(_CountingStubExecutor(NodeType.AGENT))
    return registry


def _build_stub_registry(control_flow_metadata=None, agent_metadata=None):
    """反例注册表：control_flow/agent 皆为可注入 metadata 的计数桩。"""
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())
    registry.register(
        _CountingStubExecutor(NodeType.CONTROL_FLOW, metadata=control_flow_metadata)
    )
    registry.register(_CountingStubExecutor(NodeType.AGENT, metadata=agent_metadata))
    return registry


async def _compile_gate_flow():
    definition = YamlWorkflowParser().parse(_REVIEW_GATE_YAML)
    return await GraphCompiler().compile(definition)


# =============================================================================
# A. 信号与错误码冒烟（3.1，CHG-60/CHG-61）
# =============================================================================

class TestSuspendedSignalSmoke:
    """WorkflowSuspendedSignal 信号类与错误码追加零破坏（3.1 验收）。"""

    def test_signal_is_plain_exception_not_domain_error(self):
        """继承 Exception 而非 DomainError：挂起非错误，不进异常处理器映射。"""
        assert issubclass(WorkflowSuspendedSignal, Exception)
        assert not issubclass(WorkflowSuspendedSignal, DomainError)

    def test_signal_carries_fields_and_code(self):
        signal = WorkflowSuspendedSignal(
            checkpoint_id="cp_x",
            suspended_node_id="review_gate",
            next_node_id="after_review",
            review_instructions="请审阅",
            timeout_action="reject",
        )
        assert signal.checkpoint_id == "cp_x"
        assert signal.suspended_node_id == "review_gate"
        assert signal.next_node_id == "after_review"
        assert signal.review_instructions == "请审阅"
        assert signal.timeout_action == "reject"
        assert signal.code == WF_INSTANCE_SUSPENDED

    def test_error_codes_appended(self):
        assert WF_INSTANCE_SUSPENDED == "WF_INSTANCE_SUSPENDED"
        assert INSTANCE_NOT_WAITING_REVIEW == "INSTANCE_NOT_WAITING_REVIEW"


# =============================================================================
# B. 引擎层：三重条件判定（3.2，CHG-61/DC-26/OP-03）
# =============================================================================

class TestSuspendConsumption:
    """三重条件正反例 + 检查点先于信号 + 护栏轮次不计挂起（CHG-53）。"""

    async def test_review_gate_suspends_with_checkpoint_before_signal(self, tmp_path):
        """正例：review 门挂起——先写检查点（真实后继）后抛信号，字段齐全。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        registry = _build_gate_registry()
        scheduler = Scheduler(executor_registry=registry, checkpoint_repository=repo)
        compiled = await _compile_gate_flow()

        with pytest.raises(WorkflowSuspendedSignal) as exc_info:
            await scheduler.execute(
                compiled,
                SharedState(instance_id="inst-gate", node_outputs={"topic": "t"}),
                "inst-gate",
                "trace-gate",
            )
        signal = exc_info.value
        assert signal.suspended_node_id == "review_gate"
        assert signal.next_node_id == "after_review"
        assert signal.review_instructions == "请审阅执行计划后放行"
        assert signal.timeout_action == "reject"

        # 检查点先于信号：挂起检查点已落盘（completed=review 门，next=真实后继）
        summaries = sorted(
            await repo.list_for_instance("inst-gate"),
            key=lambda s: s.round_index,
        )
        assert [s.round_index for s in summaries] == [0, 1]
        suspended = await repo.get(summaries[1].checkpoint_id)
        assert suspended is not None
        assert suspended.completed_node_id == "review_gate"
        assert suspended.next_node_id == "after_review"
        assert signal.checkpoint_id == suspended.checkpoint_id, (
            "信号携带的 checkpoint_id 必须是挂起时已落盘的检查点（检查点先于信号）"
        )

        # 挂起即停：后继节点未执行
        assert registry.get_executor(NodeType.AGENT).node_calls == {}

    async def test_missing_suspend_metadata_not_consumed(self, tmp_path):
        """反例①：suspend 元数据缺失（条件①不满足）→ 不挂起，全流程完成。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        registry = _build_stub_registry(
            control_flow_metadata={"control_flow_kind": "human_review_gate"},
        )
        scheduler = Scheduler(executor_registry=registry, checkpoint_repository=repo)
        compiled = await _compile_gate_flow()

        final_state = await scheduler.execute(
            compiled,
            SharedState(instance_id="inst-c1", node_outputs={"topic": "t"}),
            "inst-c1",
            "trace-c1",
        )
        assert final_state.instance_id == "inst-c1"
        assert (
            registry.get_executor(NodeType.CONTROL_FLOW).node_calls.get("review_gate")
            == 1
        )
        assert registry.get_executor(NodeType.AGENT).node_calls.get("after_review") == 1, (
            "suspend 缺失不消费：后继节点照常执行（既有 run 路径零变化）"
        )

    async def test_non_control_flow_suspend_not_consumed(self, tmp_path):
        """反例②：非 control_flow 节点（agent）携带 suspend 元数据 → 不消费。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        registry = _build_stub_registry(
            control_flow_metadata={"control_flow_kind": "human_review_gate"},
            agent_metadata={
                "suspend": True,
                "control_flow_kind": "human_review_gate",
            },
        )
        scheduler = Scheduler(executor_registry=registry, checkpoint_repository=repo)
        compiled = await _compile_gate_flow()

        final_state = await scheduler.execute(
            compiled,
            SharedState(instance_id="inst-c2", node_outputs={"topic": "t"}),
            "inst-c2",
            "trace-c2",
        )
        assert final_state.instance_id == "inst-c2"
        assert registry.get_executor(NodeType.AGENT).node_calls.get("after_review") == 1, (
            "非 control_flow 节点的 suspend 元数据不消费（OP-03），流程正常完成"
        )

    async def test_non_gate_kind_suspend_not_consumed(self, tmp_path):
        """反例③：control_flow 非 human_review_gate kind 携带 suspend → 不消费。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        registry = _build_stub_registry(
            control_flow_metadata={"suspend": True, "control_flow_kind": "loop"},
        )
        scheduler = Scheduler(executor_registry=registry, checkpoint_repository=repo)
        compiled = await _compile_gate_flow()

        final_state = await scheduler.execute(
            compiled,
            SharedState(instance_id="inst-c3", node_outputs={"topic": "t"}),
            "inst-c3",
            "trace-c3",
        )
        assert final_state.instance_id == "inst-c3"
        assert (
            registry.get_executor(NodeType.CONTROL_FLOW).node_calls.get("review_gate")
            == 1
        )
        assert registry.get_executor(NodeType.AGENT).node_calls.get("after_review") == 1, (
            "control_flow_kind 非 human_review_gate 的 suspend 不消费（三重条件③）"
        )

    async def test_guard_round_not_counted_on_suspend(self, tmp_path):
        """护栏轮次不计挂起：护栏开启下挂起信号先于步数护栏触发。

        M4 B10 改写（2026-11-23）：引擎经构造注入 EngineSettings（env 感知
        移宿主侧 application.engine_wiring），测试直接注入护栏两字段。
        """
        engine_settings = EngineSettings(
            workflow_step_guard_enabled=True, workflow_max_steps=2
        )
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        registry = _build_gate_registry()
        scheduler = Scheduler(
            executor_registry=registry,
            checkpoint_repository=repo,
            settings=engine_settings,
        )
        compiled = await _compile_gate_flow()

        with pytest.raises(WorkflowSuspendedSignal):
            await scheduler.execute(
                compiled,
                SharedState(instance_id="inst-guard", node_outputs={"topic": "t"}),
                "inst-guard",
                "trace-guard",
            )

    async def test_guard_fires_when_not_suspended(self, tmp_path):
        """对照组：未挂起时同一护栏配置在门后轮次触发（证明挂起分支确未计轮）。

        M4 B10 改写（2026-11-23）：同上，护栏两字段经 EngineSettings 注入。
        """
        engine_settings = EngineSettings(
            workflow_step_guard_enabled=True, workflow_max_steps=2
        )
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        registry = _build_stub_registry(
            control_flow_metadata={"control_flow_kind": "human_review_gate"},
        )
        scheduler = Scheduler(
            executor_registry=registry,
            checkpoint_repository=repo,
            settings=engine_settings,
        )
        compiled = await _compile_gate_flow()

        with pytest.raises(DomainError) as exc_info:
            await scheduler.execute(
                compiled,
                SharedState(instance_id="inst-guard2", node_outputs={"topic": "t"}),
                "inst-guard2",
                "trace-guard2",
            )
        assert exc_info.value.code == WF_BUDGET_EXCEEDED, (
            "门不挂起时：门完成轮（round=1）后 advance → round=2 达护栏"
        )


# =============================================================================
# C. 路由层：挂起状态落点（3.3）+ 重启恢复（3.4，CHG-62/DC-26 规则 3/6）
# =============================================================================

@pytest.fixture
def routed_storage_root(tmp_path, monkeypatch):
    """LOCAL_STORAGE_ROOT 重定向 + settings 缓存刷新（路由侧仓储工厂取根）。"""
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    from orchestration_platform.configuration.settings import get_settings

    get_settings.cache_clear()
    try:
        yield root
    finally:
        get_settings.cache_clear()


@pytest.fixture
def stubbed_agent_executor():
    """热替换路由执行器注册表 agent 执行器为计数桩（after_review 不依赖 LLM）。"""
    import orchestration_platform.api.routers.execution_instance_router as router_mod

    registry = router_mod._executor_registry
    originals = dict(registry._executors)
    agent_stub = _CountingStubExecutor(NodeType.AGENT)
    registry.register(agent_stub)
    try:
        yield agent_stub
    finally:
        registry._executors.clear()
        registry._executors.update(originals)


@pytest.fixture
def recording_publisher():
    """注入记录型领域事件发布器（路由模块全局，测试后恢复原值）。"""
    import orchestration_platform.api.routers.execution_instance_router as router_mod

    publisher = _RecordingPublisher()
    original = router_mod._domain_event_publisher
    router_mod.set_domain_event_publisher(publisher)
    try:
        yield publisher
    finally:
        router_mod.set_domain_event_publisher(original)


class TestRouterSuspendAndRecover:
    """路由级挂起落点与重启恢复（AC-01 路由侧证据）。"""

    async def test_suspend_lands_waiting_review_with_events_and_p4(
        self, routed_storage_root, stubbed_agent_executor, recording_publisher
    ):
        """挂起落点：waiting_review + INSTANCE_SUSPENDED 事件 + 发布点 P4 + task 结束。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod
        from orchestration_platform.api.routers.execution_instance_router import (
            StartInstanceBody,
            start_instance,
        )

        request = SimpleNamespace(state=SimpleNamespace(tenant_id=None))
        body = StartInstanceBody(
            definition_id="review_gate_flow",
            yaml_text=_REVIEW_GATE_YAML,
            inputs={"topic": "审批挂起"},
        )
        result = await start_instance(body, request)
        instance_id = result["instance_id"]

        rt = router_mod._instance_registry.get(instance_id)
        await rt.task
        assert rt.status == "waiting_review", "挂起落点为 waiting_review（零新增状态）"
        assert rt.task.done(), "挂起实例后台任务结束，不占调度游标"
        assert rt.suspended_checkpoint_id is not None

        events = router_mod._instance_registry.get_events(instance_id)
        event_types = [e["event_type"] for e in events]
        assert "INSTANCE_SUSPENDED" in event_types
        assert "INSTANCE_FAILED" not in event_types, (
            "挂起信号不被 except Exception 吞为失败（E-W2-11 分支顺序）"
        )
        suspended_event = next(
            e for e in events if e["event_type"] == "INSTANCE_SUSPENDED"
        )
        assert suspended_event["node_id"] == "review_gate"
        assert suspended_event["payload"]["next_node_id"] == "after_review"
        assert (
            suspended_event["payload"]["checkpoint_id"]
            == rt.suspended_checkpoint_id
        )

        repo = LocalFileCheckpointRepository()
        checkpoint = await repo.get(rt.suspended_checkpoint_id)
        assert checkpoint is not None
        assert checkpoint.completed_node_id == "review_gate"
        assert checkpoint.next_node_id == "after_review"
        assert checkpoint.round_index == 1

        p4_events = [
            e
            for e in recording_publisher.events
            if e.event_type == "instance.suspended"
        ]
        assert len(p4_events) == 1, "发布点 P4：instance.suspended 恰发布一次"
        assert p4_events[0].payload["suspended_node_id"] == "review_gate"
        assert p4_events[0].payload["checkpoint_id"] == rt.suspended_checkpoint_id
        assert p4_events[0].payload["next_node_id"] == "after_review"
        assert p4_events[0].payload["review_instructions"] == "请审阅执行计划后放行"

    async def test_recover_restores_waiting_review_for_suspended_instance(
        self, routed_storage_root, stubbed_agent_executor
    ):
        """AC-01 重启可恢复：挂起实例经 recover 置 WAITING_REVIEW，不直接续跑。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod
        from orchestration_platform.api.routers.execution_instance_router import (
            StartInstanceBody,
            recover_instance,
            start_instance,
        )

        request = SimpleNamespace(state=SimpleNamespace(tenant_id=None))
        body = StartInstanceBody(
            definition_id="review_gate_flow",
            yaml_text=_REVIEW_GATE_YAML,
            inputs={"topic": "重启恢复"},
        )
        result = await start_instance(body, request)
        instance_id = result["instance_id"]
        rt = router_mod._instance_registry.get(instance_id)
        await rt.task
        suspended_cp = rt.suspended_checkpoint_id
        assert rt.status == "waiting_review"

        # 模拟重启内存丢失：状态与挂起登记复位（compiled 保留 = 上下文重建语义）
        router_mod._instance_registry.update_status(instance_id, "failed")
        rt.suspended_checkpoint_id = None

        response = await recover_instance(instance_id)
        assert response["status"] == "waiting_review", (
            "恢复位点 completed=review 门 → 置 WAITING_REVIEW 而非直接续跑"
        )
        assert response["resumed_from_checkpoint_id"] == suspended_cp, (
            "latest_for_instance 取回挂起检查点（round_index 最大 = 挂起点）"
        )
        rt = router_mod._instance_registry.get(instance_id)
        assert rt.status == "waiting_review"
        assert rt.suspended_checkpoint_id == suspended_cp

        events = router_mod._instance_registry.get_events(instance_id)
        recovered_events = [
            e
            for e in events
            if e["event_type"] == "INSTANCE_SUSPENDED"
            and e["payload"].get("recovered") is True
        ]
        assert len(recovered_events) == 1, "恢复分支追加 recovered 标记的挂起事件"

        assert stubbed_agent_executor.node_calls == {}, "恢复不直接续跑（交决策驱动）"
