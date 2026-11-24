# =============================================================================
# 定时触发扫描器/注册表/管理路由/触发链单测（tests/unit/test_schedule_scanner.py）
#
# 覆盖：arch_review_v2_tasks_wave2 任务 4.3/4.4/4.5/4.6/4.7（CHG-56 + CHG-49/
#       CHG-59/CHG-66；AC-05 后半）。
# 分层：
#   A. TriggerScheduleRegistry 内存注册表（DC-28 规则 6：register/get/list/
#      enable/disable/next_fire_at/mark_fired/deregister）；
#   B. ScheduleScanner 假时钟驱动（DC-28 规则 4/5：到期一次且仅一次/幂等去重/
#      disable 不触发/mark_fired 先于 fire/失败不重发/P8 发布/start-stop 幂等）；
#   C. 管理路由直调（DC-29 三端点 + 422/404 语义）；
#   D. 开关门禁（trigger_schedule_api_enabled 默认 False → 404 实测，HC-07）；
#   E. 触发链端到端（fire 闭包经既有实例启动链路：定义重取 → 编译 →
#      run_and_record(trigger_source="cron")；P1/P8 事件；业务类别回退；
#      定义缺失 → None 不重发）。
# 纪律：假时钟同步推进（spec FR-04 规则 2），零真实定时长跑；
#      测试文件自包含（桩/固件不跨文件导入，对齐 test_review_service 范式）。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_schedule_scanner.py -q
# =============================================================================

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from orchestration_platform.api import application_factory
from orchestration_platform.api.application_factory import (
    build_cron_fire_callback,
    create_app,
)
from orchestration_platform.api.routers import trigger_schedule_router
from orchestration_platform.api.routers.trigger_schedule_router import (
    NextFireResponse,
    TriggerScheduleEnableBody,
    disable_trigger_schedule,
    enable_trigger_schedule,
    get_next_fire,
    list_trigger_schedules,
    set_trigger_schedule_registry,
)
from orchestration_platform.application.schedule.models import TriggerSchedule
from orchestration_platform.application.schedule.schedule_scanner import (
    ScheduleFireOutcome,
    ScheduleScanner,
    TriggerScheduleRegistry,
    build_fired_key,
)
from orchestration_platform.configuration.settings import Settings
from orchestration_platform.domain.errors import (
    SCHEDULE_INVALID_CRON,
    SCHEDULE_TRIGGER_NOT_FOUND,
    WF_VALIDATION_FAILED,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.enums import FrequencyPresetEnum
from orchestration_platform.persistence.repository_factory import RepositoryFactory
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

# Cron 触发流水线（E 触发链载体）：trigger_scheduled（TRIGGER/scheduled）→
# cron_agent（agent，计数桩执行）。business_category 断言取 TRIGGER 节点
# trigger_type 值（"scheduled"）。
_CRON_FLOW_YAML = """spec_version: "1.0"
definition_id: cron_flow
name: Cron 定时触发流水线
definition_version: 1
inputs: []

outputs:
  - name: analysis
    type: object
    description: 分析结果

nodes:
  - node_id: trigger_scheduled
    name: 定时触发
    node_type: trigger
    configuration:
      trigger_type: scheduled
      schedule: "0 12 * * *"

  - node_id: cron_agent
    name: 分析节点
    node_type: agent
    output_name: analysis
    configuration:
      model_identifier: primary-model
      system_prompt: 你是一名数据分析师。

edges:
  - edge_id: e1
    source_node_id: trigger_scheduled
    target_node_id: cron_agent
"""


# =============================================================================
# 测试基建：假时钟 + fire/发布记录桩 + 计数桩执行器 + 路由固件
# =============================================================================

class _FakeClock:
    """假时钟（spec FR-04 规则 2：同步驱动，禁止真实定时长跑）。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now = self.now + timedelta(**kwargs)


# 显式产物哨兵：区分「未指定（返回新生成产物）」与「显式 None（启动失败）」
_OUTCOME_UNSPECIFIED = object()


class _RecordingFire:
    """fire 回调记录桩：捕获 (schedule, fire_at) 与调用瞬间的注册表状态。"""

    def __init__(self, outcome=_OUTCOME_UNSPECIFIED, error=None) -> None:
        self.calls = []
        self._outcome = outcome
        self._error = error

    async def __call__(self, schedule, fire_at):
        self.calls.append(
            {
                "schedule": schedule,
                "fire_at": fire_at,
                "last_fired_key_at_call": schedule.last_fired_key,
            }
        )
        if self._error is not None:
            raise self._error
        if self._outcome is not _OUTCOME_UNSPECIFIED:
            return self._outcome  # 显式 None = 启动失败（fire 返回 None 分支）
        return ScheduleFireOutcome(
            instance_id=f"inst-{len(self.calls)}",
            trace_id=f"trace-{len(self.calls)}",
        )


class _RecordingPublisher:
    """发布记录桩：收集已发布领域事件（P8/P1 断言用）。"""

    def __init__(self) -> None:
        self.events = []

    async def publish_domain_event(self, event) -> None:
        self.events.append(event)


class _CountingStubExecutor:
    """计数桩执行器（agent 节点桩，不依赖 LLM）。"""

    def __init__(self, node_type):

        self._node_type = node_type
        self.node_calls: dict = {}

    @property
    def node_type(self):
        return self._node_type

    async def execute(self, context):
        from orchestration_platform.engine.nodes.base import NodeExecutionResult

        node_id = context.node_definition.node_id
        self.node_calls[node_id] = self.node_calls.get(node_id, 0) + 1
        return NodeExecutionResult(
            success=True,
            output_name=getattr(
                context.node_definition, "effective_output_name", None
            ),
            output_value={"stub": node_id},
        )


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
    """热替换路由执行器注册表 agent 执行器为计数桩。"""
    import orchestration_platform.api.routers.execution_instance_router as router_mod
    from orchestration_platform.domain.models.enums import NodeType

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


@pytest.fixture
def schedule_registry():
    """注入全新注册表到管理路由（测试后恢复原值）。"""
    original = trigger_schedule_router._registry
    registry = TriggerScheduleRegistry()
    set_trigger_schedule_registry(registry)
    try:
        yield registry
    finally:
        set_trigger_schedule_registry(original)


# =============================================================================
# A. TriggerScheduleRegistry 内存注册表（CHG-48，DC-28 规则 6）
# =============================================================================

class TestTriggerScheduleRegistry:
    """注册表：登记/查询/启停/触发留痕/注销（重启空注册表为已知边界）。"""

    @staticmethod
    def _mk(**overrides):
        params = dict(
            workflow_id="wf",
            trigger_node_id="trig",
            schedule="0 12 * * *",
        )
        params.update(overrides)
        return TriggerSchedule(**params)

    def test_register_computes_next_fire_at(self):
        """register 经 resolve_next_fire 回填 next_fire_at。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registered = registry.register(self._mk())
        assert registered.next_fire_at == datetime(2026, 11, 19, 12, 0)

    def test_register_upserts_same_schedule_id(self):
        """同 schedule_id 重复登记为 upsert（不重复）。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk(schedule="0 12 * * *"))
        registry.register(self._mk(schedule="0 13 * * *"))
        schedules = registry.list()
        assert len(schedules) == 1
        assert schedules[0].schedule == "0 13 * * *"

    def test_register_invalid_cron_raises(self):
        """注册期解析失败抛 SCHEDULE_INVALID_CRON（HTTP 422 语义）。"""
        registry = TriggerScheduleRegistry()
        with pytest.raises(WorkflowValidationError) as exc_info:
            registry.register(self._mk(schedule="bad cron"))
        assert exc_info.value.code == SCHEDULE_INVALID_CRON

    def test_get_unregistered_raises_not_found(self):
        """未注册触发器 → ResourceNotFoundError(SCHEDULE_TRIGGER_NOT_FOUND)。"""
        registry = TriggerScheduleRegistry()
        with pytest.raises(ResourceNotFoundError) as exc_info:
            registry.get("missing:thing")
        assert exc_info.value.code == SCHEDULE_TRIGGER_NOT_FOUND

    def test_list_sorted_by_schedule_id(self):
        """list 按 schedule_id 排序（遍历确定性）。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk(workflow_id="zwf", trigger_node_id="trig"))
        registry.register(self._mk(workflow_id="awf", trigger_node_id="trig"))
        assert [s.schedule_id for s in registry.list()] == [
            "awf:trig",
            "zwf:trig",
        ]

    def test_enable_recomputes_next_fire_at(self):
        """enable 从当前时刻重算 next_fire_at。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())
        registry.disable("wf:trig")
        clock.advance(hours=5)  # 15:00：当日 12:00 锚点已过
        enabled = registry.enable("wf:trig")
        assert enabled.enabled is True
        assert enabled.next_fire_at == datetime(2026, 11, 20, 12, 0)

    def test_disable_sets_enabled_false(self):
        """disable 软停用（不删除注册）。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())
        disabled = registry.disable("wf:trig")
        assert disabled.enabled is False
        assert len(registry.list()) == 1

    def test_next_fire_at_query(self):
        """next_fire_at 查询：已注册返回时刻，未注册返回 None。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())
        assert registry.next_fire_at("wf:trig") == datetime(2026, 11, 19, 12, 0)
        assert registry.next_fire_at("missing:thing") is None

    def test_mark_fired_updates_state(self):
        """mark_fired 留痕：last_fired_key + 推进后的 next_fire_at。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())
        following = datetime(2026, 11, 20, 12, 0)
        registry.mark_fired(
            "wf:trig", "wf:trig:2026-11-19T12:00:00Z", following
        )
        schedule = registry.get("wf:trig")
        assert schedule.last_fired_key == "wf:trig:2026-11-19T12:00:00Z"
        assert schedule.next_fire_at == following

    def test_deregister_idempotent(self):
        """deregister 幂等：未注册静默返回。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())
        registry.deregister("wf:trig")
        registry.deregister("wf:trig")
        with pytest.raises(ResourceNotFoundError):
            registry.get("wf:trig")

    def test_register_preset_interval(self):
        """间隔预设登记：INTERVAL_MINUTES → now + 1 分钟。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registered = registry.register(
            self._mk(schedule="", frequency_preset=FrequencyPresetEnum.INTERVAL_MINUTES)
        )
        assert registered.next_fire_at == datetime(2026, 11, 19, 10, 1)


# =============================================================================
# B. ScheduleScanner 假时钟驱动（CHG-48，DC-28 规则 4/5；一次且仅一次）
# =============================================================================

class TestScheduleScannerFakeClock:
    """假时钟同步推进：到期判定/幂等去重/失败不重发/P8 发布/生命周期。"""

    @staticmethod
    def _mk(**overrides):
        params = dict(
            workflow_id="wf",
            trigger_node_id="trig",
            schedule="0 12 * * *",
        )
        params.update(overrides)
        return TriggerSchedule(**params)

    @staticmethod
    def _scanner(registry, fire, publisher=None, clock=None):
        return ScheduleScanner(
            registry=registry,
            fire=fire,
            interval_seconds=30,
            publisher=publisher,
            clock=clock if clock is not None else _FakeClock(
                datetime(2026, 11, 19, 10, 0)
            ),
        )

    async def test_not_due_no_fire(self):
        """未到期（next_fire_at > clock）不触发。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())  # next 12:00
        fire = _RecordingFire()
        scanner = self._scanner(registry, fire, clock=clock)
        assert await scanner.tick() == 0
        assert fire.calls == []

    async def test_due_fires_once(self):
        """到期触发一次且仅一次（无发布器旁路不报错）。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())  # next 12:00
        clock.advance(hours=2)
        fire = _RecordingFire()
        scanner = self._scanner(registry, fire, clock=clock)
        assert await scanner.tick() == 1
        assert len(fire.calls) == 1
        assert fire.calls[0]["fire_at"] == datetime(2026, 11, 19, 12, 0)

    async def test_mark_fired_precedes_fire(self):
        """mark_fired 先于 fire（fire 时 last_fired_key 已写入——一次且仅一次）。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())
        clock.advance(hours=2)
        fire = _RecordingFire()
        scanner = self._scanner(registry, fire, clock=clock)
        await scanner.tick()
        fired_key = build_fired_key("wf", "trig", datetime(2026, 11, 19, 12, 0))
        assert fire.calls[0]["last_fired_key_at_call"] == fired_key

    async def test_same_window_no_second_fire(self):
        """同到期窗口二次 tick 不再触发（next_fire_at 已推进）。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())
        clock.advance(hours=2)
        fire = _RecordingFire()
        scanner = self._scanner(registry, fire, clock=clock)
        assert await scanner.tick() == 1
        assert await scanner.tick() == 0
        assert len(fire.calls) == 1

    async def test_next_window_fires_again(self):
        """推进到下一窗口再次触发（不同幂等键）。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        schedule = registry.register(self._mk())
        clock.advance(hours=2)
        fire = _RecordingFire()
        scanner = self._scanner(registry, fire, clock=clock)
        await scanner.tick()
        clock.advance(days=1)  # 次日 12:00 窗口
        assert await scanner.tick() == 1
        assert len(fire.calls) == 2
        assert (
            schedule.last_fired_key
            == "wf:trig:2026-11-20T12:00:00Z"
        )

    async def test_disabled_no_fire(self):
        """停用后不再触发（已有实例不受影响——spec §6.3 规则 2）。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())
        registry.disable("wf:trig")
        clock.advance(hours=2)
        fire = _RecordingFire()
        scanner = self._scanner(registry, fire, clock=clock)
        assert await scanner.tick() == 0
        assert fire.calls == []

    async def test_idempotent_key_dedup_branch(self):
        """同键重复到期（重叠窗口）→ 幂等去重跳过（DC-28 规则 5 防线）。"""
        clock = _FakeClock(datetime(2026, 11, 19, 12, 0))
        registry = TriggerScheduleRegistry(
            clock=_FakeClock(datetime(2026, 11, 19, 10, 0))
        )
        schedule = registry.register(self._mk())
        # 手工构造重叠窗口：last_fired_key 与当前到期键一致、next_fire_at 未推进
        registry.mark_fired(
            "wf:trig",
            build_fired_key("wf", "trig", schedule.next_fire_at),
            schedule.next_fire_at,
        )
        fire = _RecordingFire()
        scanner = self._scanner(registry, fire, clock=clock)
        assert await scanner.tick() == 0
        assert fire.calls == []

    async def test_fire_returns_none_no_refire(self):
        """fire 返回 None（启动失败）不重发——一次且仅一次。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())
        clock.advance(hours=2)
        fire = _RecordingFire(outcome=None)
        scanner = self._scanner(registry, fire, clock=clock)
        assert await scanner.tick() == 0
        assert len(fire.calls) == 1
        assert await scanner.tick() == 0
        assert len(fire.calls) == 1

    async def test_fire_raises_no_refire(self):
        """fire 抛异常不重发、不阻断扫描循环。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())
        clock.advance(hours=2)
        fire = _RecordingFire(error=RuntimeError("fire 通道不可用（注入）"))
        scanner = self._scanner(registry, fire, clock=clock)
        assert await scanner.tick() == 0
        assert len(fire.calls) == 1
        assert await scanner.tick() == 0
        assert len(fire.calls) == 1

    async def test_resolve_failure_skips_unmarked_retries(self):
        """运行时解析失败：告警跳过、不 mark（下轮重试）、不 fire。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        schedule = registry.register(self._mk())
        schedule.schedule = "bad cron"  # 注册后破坏（模拟运行时解析失败）
        clock.advance(hours=2)
        fire = _RecordingFire()
        scanner = self._scanner(registry, fire, clock=clock)
        assert await scanner.tick() == 0
        assert fire.calls == []
        assert schedule.last_fired_key is None  # 未 mark → 下轮重试
        assert await scanner.tick() == 0
        assert fire.calls == []

    async def test_p8_events_published(self):
        """P8 发布：instance.triggered + instance.created（附录 A 载荷键）。"""
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(self._mk())
        clock.advance(hours=2)
        fire = _RecordingFire()
        publisher = _RecordingPublisher()
        scanner = self._scanner(registry, fire, publisher=publisher, clock=clock)
        assert await scanner.tick() == 1
        assert [e.event_type for e in publisher.events] == [
            "instance.triggered",
            "instance.created",
        ]
        triggered = publisher.events[0]
        assert triggered.payload["trigger_node_id"] == "trig"
        assert triggered.payload["schedule"] == "0 12 * * *"
        assert triggered.payload["fire_at"] == "2026-11-19T12:00:00Z"
        assert (
            triggered.payload["idempotency_key"]
            == "wf:trig:2026-11-19T12:00:00Z"
        )
        # 2026-11-22 B7 W2b：DEFAULT_TENANT_ID 值由 "default" 统一为
        # "tenant_default"（无租户上下文的 cron 事件回退权威常量；出口记录已登记）。
        assert triggered.tenant_id == "tenant_default"
        assert triggered.instance_id == "inst-1"
        created = publisher.events[1]
        assert created.payload == {"trigger_source": "cron"}
        assert created.trace_id == "trace-1"

    async def test_start_stop_idempotent(self):
        """start/stop 幂等：重复 start 同一任务；stop 后任务清空。"""
        registry = TriggerScheduleRegistry(
            clock=_FakeClock(datetime(2026, 11, 19, 10, 0))
        )
        fire = _RecordingFire()
        scanner = ScheduleScanner(
            registry=registry, fire=fire, interval_seconds=0.01
        )
        await scanner.start()
        first_task = scanner._task
        await scanner.start()
        assert scanner._task is first_task
        await scanner.stop()
        assert scanner._task is None
        await scanner.stop()  # 幂等
        assert fire.calls == []


# =============================================================================
# C. 管理路由直调（CHG-49，DC-29：列出/启停/下次触发 + 422/404 语义）
# =============================================================================

class TestTriggerScheduleRouterDirect:
    """路由函数直调（schedule_registry 固件注入全新注册表）。"""

    @staticmethod
    def _enable_body(**overrides):
        params = dict(
            workflow_id="wf",
            trigger_node_id="trig",
            schedule="0 12 * * *",
        )
        params.update(overrides)
        return TriggerScheduleEnableBody(**params)

    async def test_list_empty(self, schedule_registry):
        """空注册表：列表返回空数组。"""
        assert await list_trigger_schedules() == {"schedules": []}

    async def test_enable_registers_and_returns_serialized(self, schedule_registry):
        """enable 登记并返回序列化结构（8 键，CHG-49 契约）。"""
        result = await enable_trigger_schedule("wf:trig", self._enable_body())
        assert set(result.keys()) == {
            "schedule_id",
            "workflow_id",
            "trigger_node_id",
            "schedule",
            "frequency_preset",
            "enabled",
            "next_fire_at",
            "last_fired_key",
        }
        assert result["schedule_id"] == "wf:trig"
        assert result["schedule"] == "0 12 * * *"
        assert result["frequency_preset"] is None
        assert result["enabled"] is True
        assert result["next_fire_at"] is not None
        assert result["last_fired_key"] is None

    async def test_enable_invalid_cron_raises(self, schedule_registry):
        """注册期 cron 解析失败 → SCHEDULE_INVALID_CRON（HTTP 422 语义）。"""
        with pytest.raises(WorkflowValidationError) as exc_info:
            await enable_trigger_schedule(
                "wf:trig", self._enable_body(schedule="bad cron")
            )
        assert exc_info.value.code == SCHEDULE_INVALID_CRON

    async def test_enable_id_mismatch_raises(self, schedule_registry):
        """路径组合键与请求体漂移 → WF_VALIDATION_FAILED（防错位登记）。"""
        with pytest.raises(WorkflowValidationError) as exc_info:
            await enable_trigger_schedule("wf:other", self._enable_body())
        assert exc_info.value.code == WF_VALIDATION_FAILED

    async def test_enable_requires_schedule_or_preset(self, schedule_registry):
        """cron/预设双缺 → SCHEDULE_INVALID_CRON（HTTP 422 语义）。"""
        with pytest.raises(WorkflowValidationError) as exc_info:
            await enable_trigger_schedule(
                "wf:trig", self._enable_body(schedule="")
            )
        assert exc_info.value.code == SCHEDULE_INVALID_CRON

    async def test_disable_unregistered_raises_not_found(self, schedule_registry):
        """停用未注册触发器 → SCHEDULE_TRIGGER_NOT_FOUND（HTTP 404 语义）。"""
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await disable_trigger_schedule("missing:thing")
        assert exc_info.value.code == SCHEDULE_TRIGGER_NOT_FOUND

    async def test_disable_known_soft_keeps_registration(self, schedule_registry):
        """disable 软停用：保留注册（列表仍可见）。"""
        await enable_trigger_schedule("wf:trig", self._enable_body())
        result = await disable_trigger_schedule("wf:trig")
        assert result["enabled"] is False
        schedules = (await list_trigger_schedules())["schedules"]
        assert len(schedules) == 1
        assert schedules[0]["enabled"] is False

    async def test_next_fire_unknown_raises_not_found(self, schedule_registry):
        """下次触发查询未注册 → SCHEDULE_TRIGGER_NOT_FOUND（HTTP 404 语义）。"""
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await get_next_fire("missing:thing")
        assert exc_info.value.code == SCHEDULE_TRIGGER_NOT_FOUND

    async def test_next_fire_known_returns_response(self, schedule_registry):
        """下次触发查询：NextFireResponse 结构 + ISO 时间戳可解析。"""
        await enable_trigger_schedule("wf:trig", self._enable_body())
        result = await get_next_fire("wf:trig")
        assert isinstance(result, NextFireResponse)
        assert result.schedule_id == "wf:trig"
        assert result.enabled is True
        assert result.next_fire_at is not None
        assert datetime.fromisoformat(result.next_fire_at) is not None

    async def test_list_includes_registered_sorted(self, schedule_registry):
        """列表含多条注册、按 schedule_id 排序且键集完整。"""
        await enable_trigger_schedule(
            "zwf:trig", self._enable_body(workflow_id="zwf")
        )
        await enable_trigger_schedule(
            "awf:trig", self._enable_body(workflow_id="awf")
        )
        schedules = (await list_trigger_schedules())["schedules"]
        assert [s["schedule_id"] for s in schedules] == ["awf:trig", "zwf:trig"]
        expected_keys = {
            "schedule_id",
            "workflow_id",
            "trigger_node_id",
            "schedule",
            "frequency_preset",
            "enabled",
            "next_fire_at",
            "last_fired_key",
        }
        assert all(set(s.keys()) == expected_keys for s in schedules)

    async def test_enable_with_preset(self, schedule_registry):
        """预设启用：INTERVAL_MINUTES → now + 1 分钟（真实时钟 aware UTC）。"""
        result = await enable_trigger_schedule(
            "wf:trig",
            self._enable_body(
                schedule="",
                frequency_preset=FrequencyPresetEnum.INTERVAL_MINUTES,
            ),
        )
        assert result["frequency_preset"] == "interval_minutes"
        parsed = datetime.fromisoformat(result["next_fire_at"])
        delta = parsed - datetime.now(timezone.utc)
        assert timedelta(0) < delta <= timedelta(seconds=90)


# =============================================================================
# D. 开关门禁（HC-07：trigger_schedule_api_enabled 默认 False → 404 实测）
# =============================================================================

class TestTriggerScheduleApiSwitchGate:
    """开关门控实测：关 → 管理端点 404（不注册）；开 → 端点可达。

    TestClient 不用 with（不触发 startup，不拉起扫描循环）。
    """

    def test_disabled_switch_routes_return_404(self):
        """开关关闭（默认）：管理端点不注册（404），健康检查不受影响。"""
        with patch.object(
            application_factory,
            "get_settings",
            return_value=Settings(trigger_schedule_api_enabled=False),
        ):
            app = create_app()
        client = TestClient(app)
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/trigger-schedules").status_code == 404
        assert (
            client.post("/api/v1/trigger-schedules/wf:trig:disable").status_code
            == 404
        )

    def test_enabled_switch_routes_registered(self):
        """开关开启：管理端点注册（列表 200 空数组 + 冒号路径实测）。"""
        with patch.object(
            application_factory,
            "get_settings",
            return_value=Settings(trigger_schedule_api_enabled=True),
        ):
            app = create_app()
        client = TestClient(app)
        response = client.get("/api/v1/trigger-schedules")
        assert response.status_code == 200
        assert response.json() == {"schedules": []}
        # 冒号路径经完整 HTTP 栈实测（直调函数不覆盖路由匹配）
        created = client.post(
            "/api/v1/trigger-schedules/wf:trig:enable",
            json={
                "workflow_id": "wf",
                "trigger_node_id": "trig",
                "schedule": "0 12 * * *",
            },
        )
        assert created.status_code == 200
        assert created.json()["schedule_id"] == "wf:trig"
        next_fire = client.get("/api/v1/trigger-schedules/wf:trig/next-fire")
        assert next_fire.status_code == 200
        assert next_fire.json()["schedule_id"] == "wf:trig"


# =============================================================================
# E. fire 闭包经既有实例启动链路端到端（CHG-59/CHG-66，DC-28 规则 5）
# =============================================================================

class TestCronFireChainEndToEnd:
    """端到端：扫描器到期 → build_cron_fire_callback → run_and_record("cron")。

    定义重取 → 编译 → 执行（agent 计数桩）→ 注册表留痕；
    P1（instance.started，既有链路）与 P8（instance.triggered/created，
    扫描器）发布断言；定义缺失 → None 不重发（一次且仅一次）。
    """

    @staticmethod
    def _instance_ids() -> set:
        """快照模块级实例注册表键集（前后差分定位新实例）。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod

        return set(router_mod._instance_registry._instances.keys())

    @staticmethod
    async def _save_cron_flow(repository) -> None:
        """解析并保存 Cron 触发流水线定义到仓储。"""
        definition = YamlWorkflowParser().parse(_CRON_FLOW_YAML)
        await repository.save(definition)

    @classmethod
    def _assemble(cls, repository, clock, publisher=None):
        """装配注册表 + 扫描器（fire 闭包经真实仓储取定义）。"""
        fire = build_cron_fire_callback(repository)
        registry = TriggerScheduleRegistry(clock=clock)
        registry.register(
            TriggerSchedule(
                workflow_id="cron_flow",
                trigger_node_id="trigger_scheduled",
                schedule="0 12 * * *",
            )
        )
        scanner = ScheduleScanner(
            registry=registry,
            fire=fire,
            interval_seconds=30,
            publisher=publisher,
            clock=clock,
        )
        return registry, scanner

    async def test_fire_starts_instance_via_existing_chain(
        self, routed_storage_root, stubbed_agent_executor
    ):
        """到期触发经既有启动链路执行至 succeeded（与手动触发同源）。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod

        repository = RepositoryFactory.create_workflow_repository()
        await self._save_cron_flow(repository)
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        _, scanner = self._assemble(repository, clock)
        clock.advance(hours=2)  # 12:00 到期
        before = self._instance_ids()
        assert await scanner.tick() == 1
        new_ids = self._instance_ids() - before
        assert len(new_ids) == 1
        rt = router_mod._instance_registry._instances[new_ids.pop()]
        assert rt.task is not None
        await rt.task
        assert rt.status == "succeeded"
        assert stubbed_agent_executor.node_calls.get("cron_agent") == 1
        event_types = [e["event_type"] for e in rt.events]
        assert "INSTANCE_STARTED" in event_types
        assert "INSTANCE_COMPLETED" in event_types

    async def test_p1_and_p8_events_same_recorder(
        self, routed_storage_root, stubbed_agent_executor, recording_publisher
    ):
        """P1（run_and_record 既有链路）与 P8（扫描器）进同一记录发布器。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod

        repository = RepositoryFactory.create_workflow_repository()
        await self._save_cron_flow(repository)
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        _, scanner = self._assemble(
            repository, clock, publisher=recording_publisher
        )
        clock.advance(hours=2)
        before = self._instance_ids()
        assert await scanner.tick() == 1
        rt = router_mod._instance_registry._instances[
            (self._instance_ids() - before).pop()
        ]
        await rt.task
        event_types = [e.event_type for e in recording_publisher.events]
        # P8（扫描器发布）
        assert "instance.triggered" in event_types
        assert "instance.created" in event_types
        # P1（既有链路发布，trigger_source="cron"）
        started = [
            e
            for e in recording_publisher.events
            if e.event_type == "instance.started"
        ]
        assert len(started) == 1
        assert started[0].payload["trigger_source"] == "cron"
        assert started[0].payload["workflow_id"] == "cron_flow"

    async def test_fire_missing_definition_returns_none(
        self, routed_storage_root, stubbed_agent_executor
    ):
        """定义缺失：fire 返回 None 不重发；mark_fired 先于 fire 已留痕。"""

        repository = RepositoryFactory.create_workflow_repository()  # 仓储空
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry, scanner = self._assemble(repository, clock)
        clock.advance(hours=2)
        before = self._instance_ids()
        assert await scanner.tick() == 0  # fire 返回 None → 不计触发
        # mark_fired 先于 fire：定义缺失也已留痕（一次且仅一次，不重发）
        assert (
            registry.get("cron_flow:trigger_scheduled").last_fired_key
            == "cron_flow:trigger_scheduled:2026-11-19T12:00:00Z"
        )
        # 失败清理：实例注册表零残留
        assert self._instance_ids() == before
        assert await scanner.tick() == 0  # 不重发

    async def test_business_category_from_trigger_node(
        self, routed_storage_root, stubbed_agent_executor
    ):
        """业务类别两级回退：TRIGGER 节点 trigger_type → "scheduled"。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod

        repository = RepositoryFactory.create_workflow_repository()
        await self._save_cron_flow(repository)
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        _, scanner = self._assemble(repository, clock)
        clock.advance(hours=2)
        before = self._instance_ids()
        assert await scanner.tick() == 1
        rt = router_mod._instance_registry._instances[
            (self._instance_ids() - before).pop()
        ]
        await rt.task
        assert rt.business_category == "scheduled"

    async def test_idempotent_once_per_window(
        self, routed_storage_root, stubbed_agent_executor
    ):
        """幂等：同窗口一次且仅一次；次日窗口再触发（agent 桩计数 2）。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod

        repository = RepositoryFactory.create_workflow_repository()
        await self._save_cron_flow(repository)
        clock = _FakeClock(datetime(2026, 11, 19, 10, 0))
        registry, scanner = self._assemble(repository, clock)
        schedule = registry.get("cron_flow:trigger_scheduled")
        clock.advance(hours=2)
        before = self._instance_ids()
        assert await scanner.tick() == 1
        rt_first = router_mod._instance_registry._instances[
            (self._instance_ids() - before).pop()
        ]
        await rt_first.task
        assert await scanner.tick() == 0  # 同窗口幂等（next_fire_at 已推进）
        clock.advance(days=1)  # 次日 12:00 窗口
        assert await scanner.tick() == 1
        rt_second = router_mod._instance_registry._instances[
            (
                self._instance_ids()
                - before
                - {rt_first.instance_id}
            ).pop()
        ]
        await rt_second.task
        assert rt_first.instance_id != rt_second.instance_id
        assert stubbed_agent_executor.node_calls.get("cron_agent") == 2
        assert (
            schedule.last_fired_key
            == "cron_flow:trigger_scheduled:2026-11-20T12:00:00Z"
        )


