# =============================================================================
# 领域事件发布链路单测（tests/unit/test_domain_event_publisher.py）
#
# 覆盖：arch_review_v2_tasks_wave2 任务 2.12 测试三件套之一（CHG-51；
# AC-04 事件链证据、GK-05/GK-06）。
# 分层：
#   A. 模型层——DomainEvent 前缀/必填校验 + summarize_text 摘要
#      （CHG-41/DC-22）；
#   B. 发布器层——WorkflowDomainEventPublisher 信封封装/序列自增/降级吞异常
#      （CHG-43/DC-31）；
#   C. 引擎层——Scheduler 发布点 P5/P6（CHG-61/DC-23；发布器 None 旁路）；
#   D. 路由层——run_and_record 发布点 P1/P2/P3 + 装配注入入口
#      （CHG-62/DC-23）。
# =============================================================================

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from pydantic import ValidationError

from orchestration_platform.api.routers import execution_instance_router as router_mod
from orchestration_platform.domain.errors import (
    WF_NODE_EXECUTION_FAILED,
    NodeExecutionError,
)
from orchestration_platform.domain.models.domain_event import (
    DEFAULT_SUMMARY_MAX_CHARS,
    DEFAULT_TENANT_ID,
    DomainEvent,
    summarize_text,
)
from orchestration_platform.domain.models.enums import (
    MessageEventType,
    NodeType,
)
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.engine.nodes.trigger_node_executor import TriggerNodeExecutor
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.governance.event_bus.in_memory_event_bus import (
    InMemoryEventBus,
)
from orchestration_platform.governance.event_bus.workflow_domain_event_publisher import (
    WorkflowDomainEventPublisher,
)
from orchestration_platform.observability.run_log.fields import (
    BUSINESS_CATEGORY_DEFAULT,
)
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

_FOUR_NODE_YAML = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "valid_workflow_definitions"
    / "valid_sequential_four_nodes.yaml"
).read_text(encoding="utf-8")


# =============================================================================
# 测试基建：发布记录桩 / 调度器桩 / 计数桩执行器
# =============================================================================

class _RecordingPublisher:
    """发布记录桩：收集已发布事件；可选模拟发布基础设施故障。"""

    def __init__(self, raise_on_publish: bool = False) -> None:
        self.events: List[DomainEvent] = []
        self._raise_on_publish = raise_on_publish

    async def publish_domain_event(self, event: DomainEvent) -> None:
        self.events.append(event)
        if self._raise_on_publish:
            raise RuntimeError("发布基础设施故障（注入）")


class _StubScheduler:
    """调度器桩：成功返回初始状态；预设异常时抛出（不执行真实工作流）。"""

    def __init__(self, error: Optional[BaseException] = None) -> None:
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    async def execute(
        self,
        compiled,
        initial_state,
        instance_id,
        trace_id,
        resume_checkpoint=None,
        tenant_id=None,  # 2026-11-22 B7 W2a：run_and_record 新增租户穿参实参
    ):
        self.calls.append({"instance_id": instance_id, "trace_id": trace_id})
        if self.error is not None:
            raise self.error
        return initial_state


class _CountingStubExecutor:
    """计数桩执行器：按 node_id 统计执行次数；指定节点首次执行失败。"""

    def __init__(self, node_type: NodeType, fail_first_node_ids=None):
        self._node_type = node_type
        self._fail_first_node_ids = fail_first_node_ids or set()
        self.node_calls: dict = {}

    @property
    def node_type(self) -> NodeType:
        return self._node_type

    async def execute(self, context) -> NodeExecutionResult:
        node_id = context.node_definition.node_id
        self.node_calls[node_id] = self.node_calls.get(node_id, 0) + 1
        if node_id in self._fail_first_node_ids and self.node_calls[node_id] == 1:
            return NodeExecutionResult(
                success=False,
                error_code=WF_NODE_EXECUTION_FAILED,
                error_message="模拟节点执行失败（领域事件链路测试）",
            )
        return NodeExecutionResult(
            success=True,
            output_name=getattr(
                context.node_definition, "effective_output_name", None
            ),
            output_value={"stub": node_id},
        )


def _build_registry(fail_first_agent: bool = False) -> NodeExecutorRegistry:
    """构造带计数桩的执行器注册表（trigger 用真实执行器）。"""
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())
    registry.register(_CountingStubExecutor(NodeType.DATA_SOURCE))
    registry.register(
        _CountingStubExecutor(
            NodeType.AGENT,
            fail_first_node_ids={"analyze_data"} if fail_first_agent else None,
        )
    )
    registry.register(_CountingStubExecutor(NodeType.TOOL))
    return registry


async def _compile_four_nodes():
    definition = YamlWorkflowParser().parse(_FOUR_NODE_YAML)
    return await GraphCompiler().compile(definition)


def _event(**overrides: Any) -> DomainEvent:
    """构造合法领域事件（默认 instance.started，可按需覆盖字段）。"""
    base: Dict[str, Any] = {
        "event_type": "instance.started",
        "instance_id": "inst-1",
        "tenant_id": "tenant-1",
        "trace_id": "trace-1",
    }
    base.update(overrides)
    return DomainEvent(**base)


# =============================================================================
# A. 模型层：DomainEvent 校验 + summarize_text（CHG-41/DC-22）
# =============================================================================

class TestDomainEventModel:
    """DomainEvent 契约：三前缀命名、node.* 必带 node_id、标识非空。"""

    def test_accepts_instance_event(self):
        event = _event(payload={"k": "v"})
        assert event.event_type == "instance.started"
        assert event.node_id is None
        assert event.payload == {"k": "v"}
        assert event.occurred_at.tzinfo is not None, "occurred_at 统一 UTC 带时区"

    def test_accepts_node_event_with_node_id(self):
        event = _event(event_type="node.succeeded", node_id="node-1")
        assert event.node_id == "node-1"

    def test_accepts_governance_event_without_node_id(self):
        event = _event(event_type="governance.audit_written")
        assert event.node_id is None

    def test_rejects_unknown_event_type_prefix(self):
        with pytest.raises(ValidationError):
            _event(event_type="workflow.started")

    def test_node_event_requires_node_id(self):
        with pytest.raises(ValidationError):
            _event(event_type="node.failed")

    def test_rejects_blank_instance_id(self):
        with pytest.raises(ValidationError):
            _event(instance_id="   ")

    def test_rejects_blank_trace_id(self):
        with pytest.raises(ValidationError):
            _event(trace_id="")

    def test_default_tenant_id_constant(self):
        # 2026-11-22 B7 W2b：值由 "default" 统一为 "tenant_default"
        # （与 RLS 谓词/backfill 口径正向收敛；出口记录已登记）。
        assert DEFAULT_TENANT_ID == "tenant_default"


class TestSummarizeText:
    """summarize_text：None/空白/短文/长文/自定义上限（GK-05）。"""

    def test_none_returns_empty(self):
        assert summarize_text(None) == ""

    def test_blank_returns_empty(self):
        assert summarize_text("   ") == ""

    def test_short_value_unchanged(self):
        assert summarize_text("hello") == "hello"

    def test_long_text_truncated_with_marker(self):
        text = "x" * (DEFAULT_SUMMARY_MAX_CHARS + 10)
        result = summarize_text(text)
        assert result == "x" * DEFAULT_SUMMARY_MAX_CHARS + "...[truncated]"

    def test_custom_max_chars(self):
        assert summarize_text("abcdef", max_chars=3) == "abc...[truncated]"


# =============================================================================
# A2. G1 配置默认值（GK-12/HC-07；2.13 门禁③关闭态前提）
# =============================================================================

class TestG1SettingsDefaults:
    """三项新增配置默认安全：发布/订阅开关默认 False，数值参数默认在位。"""

    def test_g1_defaults_safe(self, monkeypatch):
        from orchestration_platform.configuration.settings import Settings

        monkeypatch.delenv("EVENT_BUS_PUBLISH_ENABLED", raising=False)
        monkeypatch.delenv("AUDIT_EVENT_SUBSCRIPTION_ENABLED", raising=False)
        monkeypatch.delenv("EVENT_BUS_MEMORY_HISTORY_MAX", raising=False)

        settings = Settings()

        assert settings.event_bus_publish_enabled is False, "发布开关默认关（GK-12）"
        assert settings.audit_event_subscription_enabled is False, "订阅开关默认关"
        assert settings.event_bus_memory_history_max == 10000


# =============================================================================
# B. 发布器层：信封封装 + 降级吞异常（CHG-43/DC-31）
# =============================================================================

class TestWorkflowDomainEventPublisher:
    """信封格式：WORKFLOW_EXECUTION + payload["domain_event"] + 序列自增。"""

    async def test_envelope_roundtrip_via_in_memory_bus(self):
        bus = InMemoryEventBus()
        publisher = WorkflowDomainEventPublisher(bus, topic="workflow.events")
        event1 = _event(
            event_type="instance.started",
            instance_id="inst-env",
            tenant_id="tenant-env",
            trace_id="trace-env",
            payload={"k": "v"},
        )
        event2 = _event(
            event_type="instance.succeeded",
            instance_id="inst-env",
            tenant_id="tenant-env",
            trace_id="trace-env",
            payload={"node_count_summary": 3},
        )

        await publisher.publish_domain_event(event1)
        await publisher.publish_domain_event(event2)

        replay = await bus.replay_from_offset("workflow.events", 0, 10)
        assert len(replay.events) == 2
        first, second = replay.events
        assert first.event_id.startswith("evt_"), "信封事件标识前缀"
        assert first.event_type == MessageEventType.WORKFLOW_EXECUTION
        assert first.sequence_number == 1, "同一发布器序列号单调自增"
        assert second.sequence_number == 2
        assert first.payload["domain_event"] == event1.model_dump(mode="json")
        assert first.occurred_at == event1.occurred_at, "occurred_at 取领域事件时间"
        assert first.is_degraded is True, "内存总线标记降级"
        restored = DomainEvent.model_validate(first.payload["domain_event"])
        assert restored.event_type == "instance.started"
        assert restored.instance_id == "inst-env"
        assert restored.tenant_id == "tenant-env"
        assert restored.trace_id == "trace-env"
        assert restored.payload == {"k": "v"}

    async def test_publish_failure_swallowed_with_degradation_log(self, caplog):
        class _FailingBus:
            async def publish(self, topic, event):
                raise RuntimeError("事件总线不可用（注入）")

        publisher = WorkflowDomainEventPublisher(
            _FailingBus(), topic="workflow.events"
        )
        with caplog.at_level(logging.WARNING):
            await publisher.publish_domain_event(_event())  # 不抛异常（DC-31）
        assert any(
            "领域事件发布失败" in record.getMessage() for record in caplog.records
        ), "降级告警日志在位（2.13 门禁④/INV-07）"

    async def test_sequence_increments_across_publishes(self):
        bus = InMemoryEventBus()
        publisher = WorkflowDomainEventPublisher(bus, topic="workflow.events")
        for _ in range(3):
            await publisher.publish_domain_event(_event())
        replay = await bus.replay_from_offset("workflow.events", 0, 10)
        assert [m.sequence_number for m in replay.events] == [1, 2, 3]


# =============================================================================
# C. 引擎层：Scheduler 发布点 P5/P6（CHG-61/DC-23）
# =============================================================================

class TestSchedulerDomainEvents:
    """P5 节点成功 / P6 节点失败；发布器 None 旁路；发布故障不影响执行。"""

    async def test_publishes_node_succeeded_for_each_node(self):
        recorder = _RecordingPublisher()
        scheduler = Scheduler(
            executor_registry=_build_registry(),
            domain_event_publisher=recorder,
        )
        compiled = await _compile_four_nodes()

        final_state = await scheduler.execute(
            compiled,
            SharedState(instance_id="inst-p5", node_outputs={"topic": "t"}),
            "inst-p5",
            "trace-p5",
        )

        assert final_state.instance_id == "inst-p5"
        assert [e.event_type for e in recorder.events] == ["node.succeeded"] * 4
        assert [e.node_id for e in recorder.events] == [
            "trigger_manual",
            "read_dataset",
            "analyze_data",
            "build_report",
        ]
        for event in recorder.events:
            assert event.instance_id == "inst-p5"
            assert event.tenant_id == DEFAULT_TENANT_ID, "引擎节点事件归默认租户"
            assert event.trace_id == "trace-p5"
            assert event.payload["node_id"] == event.node_id
            assert isinstance(event.payload["duration_ms"], int)
            assert event.payload["duration_ms"] >= 0
            assert event.payload["output_name"] == compiled.get_node(
                event.node_id
            ).name

    async def test_publishes_node_failed_on_node_failure(self):
        recorder = _RecordingPublisher()
        scheduler = Scheduler(
            executor_registry=_build_registry(fail_first_agent=True),
            domain_event_publisher=recorder,
        )
        compiled = await _compile_four_nodes()

        with pytest.raises(NodeExecutionError) as exc_info:
            await scheduler.execute(
                compiled,
                SharedState(instance_id="inst-p6", node_outputs={"topic": "t"}),
                "inst-p6",
                "trace-p6",
            )
        assert exc_info.value.code == WF_NODE_EXECUTION_FAILED

        assert [e.event_type for e in recorder.events] == [
            "node.succeeded",
            "node.succeeded",
            "node.failed",
        ]
        failed = recorder.events[-1]
        assert failed.node_id == "analyze_data"
        assert failed.payload["error_code"] == WF_NODE_EXECUTION_FAILED
        assert "模拟节点执行失败" in failed.payload["error_message"]

    async def test_execution_survives_publisher_failure(self, caplog):
        recorder = _RecordingPublisher(raise_on_publish=True)
        scheduler = Scheduler(
            executor_registry=_build_registry(),
            domain_event_publisher=recorder,
        )
        compiled = await _compile_four_nodes()

        with caplog.at_level(logging.WARNING):
            final_state = await scheduler.execute(
                compiled,
                SharedState(instance_id="inst-swallow", node_outputs={"topic": "t"}),
                "inst-swallow",
                "trace-swallow",
            )

        assert final_state.instance_id == "inst-swallow", "发布故障不影响执行（DC-31）"
        assert len(recorder.events) == 4, "每个节点均尝试过发布"
        assert any(
            "node.succeeded 领域事件发布失败" in record.getMessage()
            for record in caplog.records
        ), "降级告警日志在位（2.13 门禁④）"

    async def test_without_publisher_completes_normally(self):
        scheduler = Scheduler(executor_registry=_build_registry())
        compiled = await _compile_four_nodes()

        final_state = await scheduler.execute(
            compiled,
            SharedState(instance_id="inst-none", node_outputs={"topic": "t"}),
            "inst-none",
            "trace-none",
        )
        assert final_state.instance_id == "inst-none"


# =============================================================================
# D. 路由层：run_and_record 发布点 P1/P2/P3（CHG-62/DC-23）
# =============================================================================

def _register_runtime(
    registry: router_mod.InstanceRegistry,
    instance_id: str,
    **kwargs,
) -> router_mod.InstanceRuntime:
    rt = router_mod.InstanceRuntime(
        instance_id=instance_id, definition_id="wf-demo", **kwargs
    )
    registry.register(rt)
    return rt


class TestRunAndRecordDomainEvents:
    """P1 实例启动 / P2 实例成功 / P3 实例失败；触发源与租户透传。"""

    async def test_publishes_started_and_succeeded(self, monkeypatch):
        registry = router_mod.InstanceRegistry()
        rt = _register_runtime(registry, "inst-p1")
        recorder = _RecordingPublisher()
        monkeypatch.setattr(router_mod, "_domain_event_publisher", recorder)

        await router_mod.run_and_record(
            _StubScheduler(),
            object(),
            SharedState(instance_id="inst-p1"),
            "inst-p1",
            "trace-p1",
            registry,
        )

        assert rt.status == "succeeded"
        assert [e.event_type for e in recorder.events] == [
            "instance.started",
            "instance.succeeded",
        ]
        started, succeeded = recorder.events
        assert started.instance_id == "inst-p1"
        assert started.tenant_id == DEFAULT_TENANT_ID, "无租户上下文归默认租户"
        assert started.trace_id == "trace-p1"
        assert started.payload["workflow_id"] == "wf-demo"
        assert started.payload["trigger_source"] == "manual"
        assert started.payload["business_category"] == BUSINESS_CATEGORY_DEFAULT
        assert succeeded.payload["node_count_summary"] == 0

    async def test_node_count_summary_counts_node_completed(self, monkeypatch):
        registry = router_mod.InstanceRegistry()
        rt = _register_runtime(registry, "inst-p2")
        for i in range(2):
            rt.events.append({
                "event_id": f"evt-{i}",
                "instance_id": "inst-p2",
                "event_type": "NODE_COMPLETED",
                "node_id": f"node-{i}",
                "timestamp": "2026-11-18T00:00:00+00:00",
                "payload": {},
            })
        recorder = _RecordingPublisher()
        monkeypatch.setattr(router_mod, "_domain_event_publisher", recorder)

        await router_mod.run_and_record(
            _StubScheduler(),
            object(),
            SharedState(instance_id="inst-p2"),
            "inst-p2",
            "trace-p2",
            registry,
        )

        succeeded = recorder.events[-1]
        assert succeeded.event_type == "instance.succeeded"
        assert succeeded.payload["node_count_summary"] == 2

    async def test_publishes_failed_on_scheduler_error(self, monkeypatch):
        registry = router_mod.InstanceRegistry()
        rt = _register_runtime(registry, "inst-p3")
        recorder = _RecordingPublisher()
        monkeypatch.setattr(router_mod, "_domain_event_publisher", recorder)
        error = NodeExecutionError(
            WF_NODE_EXECUTION_FAILED, "节点 analyze_data 执行失败：boom"
        )

        await router_mod.run_and_record(
            _StubScheduler(error=error),
            object(),
            SharedState(instance_id="inst-p3"),
            "inst-p3",
            "trace-p3",
            registry,
        )

        assert rt.status == "failed"
        assert [e.event_type for e in recorder.events] == [
            "instance.started",
            "instance.failed",
        ]
        failed = recorder.events[-1]
        assert failed.payload["error_code"] == WF_NODE_EXECUTION_FAILED
        assert failed.payload["error_message"] == "节点 analyze_data 执行失败：boom"

    async def test_trigger_source_passthrough(self, monkeypatch):
        registry = router_mod.InstanceRegistry()
        _register_runtime(registry, "inst-p4")
        recorder = _RecordingPublisher()
        monkeypatch.setattr(router_mod, "_domain_event_publisher", recorder)

        await router_mod.run_and_record(
            _StubScheduler(),
            object(),
            SharedState(instance_id="inst-p4"),
            "inst-p4",
            "trace-p4",
            registry,
            trigger_source="schedule",
        )

        assert recorder.events[0].payload["trigger_source"] == "schedule"

    async def test_uses_runtime_tenant_id_when_present(self, monkeypatch):
        registry = router_mod.InstanceRegistry()
        _register_runtime(registry, "inst-p6", tenant_id="tenant-9")
        recorder = _RecordingPublisher()
        monkeypatch.setattr(router_mod, "_domain_event_publisher", recorder)

        await router_mod.run_and_record(
            _StubScheduler(),
            object(),
            SharedState(instance_id="inst-p6"),
            "inst-p6",
            "trace-p6",
            registry,
        )

        assert all(e.tenant_id == "tenant-9" for e in recorder.events)

    async def test_without_publisher_baseline_unchanged(self, monkeypatch):
        monkeypatch.setattr(router_mod, "_domain_event_publisher", None)
        registry = router_mod.InstanceRegistry()
        rt = _register_runtime(registry, "inst-p5")

        await router_mod.run_and_record(
            _StubScheduler(),
            object(),
            SharedState(instance_id="inst-p5"),
            "inst-p5",
            "trace-p5",
            registry,
        )

        assert rt.status == "succeeded"
        assert [e["event_type"] for e in rt.events] == [
            "INSTANCE_STARTED",
            "INSTANCE_COMPLETED",
        ]

    def test_set_domain_event_publisher_round_trip(self):
        recorder = _RecordingPublisher()
        router_mod.set_domain_event_publisher(recorder)
        try:
            assert router_mod._domain_event_publisher is recorder
        finally:
            router_mod.set_domain_event_publisher(None)
        assert router_mod._domain_event_publisher is None
