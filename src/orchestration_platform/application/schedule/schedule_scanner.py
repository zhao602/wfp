# =============================================================================
# 定时触发扫描器与注册表（application/schedule/schedule_scanner.py）
#
# 职责：TriggerScheduleRegistry（内存注册表，DC-28 规则 6）+ ScheduleScanner
#       （标准库 asyncio 扫描循环 + 假时钟注入，DC-28 规则 4/5）+ 幂等键
#       构建 + fire 回调类型 + P8 领域事件发布（arch_review_v2_tasks_wave2
#       G3，CHG-48，design §3.3 / DC-28）。
# 设计意图：
#   1. 标准库 asyncio 后台任务（否决第三方调度框架/独立线程，HC-06/DC-28）；
#      时钟注入 clock: Callable[[], datetime]——假时钟同步驱动单测，
#      禁止真实定时长跑（spec FR-04 规则 2）；
#   2. 幂等：last_fired_key = {workflow_id}:{trigger_node_id}:{fire_at ISO 秒级}
#      ——同键重复到期跳过并告警（同一触发器同一到期窗口最多启动一次，
#      一次且仅一次）；mark_fired 先于 fire，fire 异常/失败不重发；
#   3. 到期触发经 fire 回调走既有实例启动链路（与手动触发同源，
#      回调装配见 application_factory.build_cron_fire_callback）；
#   4. P8 领域事件（instance.triggered + instance.created，附录 A）由扫描器
#      发布；发布逐层 try/except 降级（DC-31），publisher 为 None 直接旁路；
#   5. 注册表内存态：重启后为空（已知边界，经管理 API 重新注册）；
#      不建持久化去重表（单实例部署边界，Wave2b F9 落库时消除）。
# =============================================================================

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional

from orchestration_platform.application.schedule.cron_parser import resolve_next_fire
from orchestration_platform.application.schedule.models import TriggerSchedule
from orchestration_platform.domain.errors import (
    SCHEDULE_TRIGGER_NOT_FOUND,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.domain_event import DEFAULT_TENANT_ID, DomainEvent
from orchestration_platform.engine.abstractions.domain_event_publisher import (
    IDomainEventPublisher,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """当前 UTC 时间（扫描器/注册表默认时钟，测试可注入假时钟替换）。"""
    return datetime.now(timezone.utc)


def _fire_at_iso(value: datetime) -> str:
    """规范化 ISO 秒级时间戳：aware 归一 UTC，naive 原样。

    幂等键与事件载荷 fire_at 共用同一规范化口径（DC-28 规则 5）。
    """
    work = value.astimezone(timezone.utc) if value.tzinfo is not None else value
    return work.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_fired_key(workflow_id: str, trigger_node_id: str, fire_at: datetime) -> str:
    """幂等键：{workflow_id}:{trigger_node_id}:{fire_at ISO 秒级}（DC-28 规则 5）。"""
    return f"{workflow_id}:{trigger_node_id}:{_fire_at_iso(fire_at)}"


@dataclass(frozen=True)
class ScheduleFireOutcome:
    """fire 回调产物：启动实例的定位信息（P8 发布与日志留痕用）。"""

    instance_id: str
    trace_id: str


# fire 回调类型：到期触发时经既有实例启动链路启动工作流（与手动触发同源），
# 返回 None 表示启动失败（扫描器告警不重发——一次且仅一次）。
ScheduleFireCallback = Callable[[TriggerSchedule, datetime], Awaitable[Optional[ScheduleFireOutcome]]]


class TriggerScheduleRegistry:
    """定时触发计划注册表（内存态，DC-28 规则 6）。

    已知边界：重启后注册表为空，触发器经管理 API 重新注册（无自动
    发现）；该边界在管理 API 文档明示，Wave2b F9 落库时消除。
    """

    def __init__(self, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._schedules: Dict[str, TriggerSchedule] = {}

    def register(self, schedule: TriggerSchedule) -> TriggerSchedule:
        """登记（upsert）：解析下次触发时刻并回填 next_fire_at。

        解析失败抛 SCHEDULE_INVALID_CRON（注册期 HTTP 422）。
        """
        schedule.next_fire_at = resolve_next_fire(
            schedule.schedule, schedule.frequency_preset, self._clock()
        )
        self._schedules[schedule.schedule_id] = schedule
        return schedule

    def get(self, schedule_id: str) -> TriggerSchedule:
        """取注册项；未注册抛 ResourceNotFoundError(SCHEDULE_TRIGGER_NOT_FOUND)。"""
        schedule = self._schedules.get(schedule_id)
        if schedule is None:
            raise ResourceNotFoundError(
                SCHEDULE_TRIGGER_NOT_FOUND,
                f"定时触发器未注册：{schedule_id}",
                details={"schedule_id": schedule_id},
            )
        return schedule

    def list(self) -> List[TriggerSchedule]:
        """列出全部注册项（按 schedule_id 排序，保证遍历确定性）。"""
        return [self._schedules[key] for key in sorted(self._schedules)]

    def enable(self, schedule_id: str) -> TriggerSchedule:
        """启用：置 enabled=True 并从当前时刻重算 next_fire_at。"""
        schedule = self.get(schedule_id)
        schedule.enabled = True
        schedule.next_fire_at = resolve_next_fire(
            schedule.schedule, schedule.frequency_preset, self._clock()
        )
        return schedule

    def disable(self, schedule_id: str) -> TriggerSchedule:
        """停用：置 enabled=False（停用后不再触发，已有实例不受影响）。"""
        schedule = self.get(schedule_id)
        schedule.enabled = False
        return schedule

    def next_fire_at(self, schedule_id: str) -> Optional[datetime]:
        """查询下次触发时刻（未注册返回 None）。"""
        schedule = self._schedules.get(schedule_id)
        return schedule.next_fire_at if schedule is not None else None

    def mark_fired(self, schedule_id: str, fired_key: str, next_fire_at: datetime) -> None:
        """触发兑现留痕：last_fired_key + 推进后的 next_fire_at（一次且仅一次）。"""
        schedule = self.get(schedule_id)
        schedule.last_fired_key = fired_key
        schedule.next_fire_at = next_fire_at

    def deregister(self, schedule_id: str) -> None:
        """注销（幂等：未注册静默返回）。"""
        self._schedules.pop(schedule_id, None)


class ScheduleScanner:
    """定时触发扫描器（asyncio 后台任务，DC-28 规则 4/5，design §3.3）。

    每 tick 遍历注册表：next_fire_at <= clock() 且 enabled → 幂等键去重
    （同键跳过告警，一次且仅一次）→ fire 回调经既有实例启动链路启动
    （与手动触发同源）→ P8 领域事件发布（instance.triggered +
    instance.created，附录 A）→ mark_fired 推进 last_fired_key/next_fire_at。
    """

    def __init__(
        self,
        registry: TriggerScheduleRegistry,
        fire: ScheduleFireCallback,
        interval_seconds: float = 30,
        publisher: Optional[IDomainEventPublisher] = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._registry = registry
        self._fire = fire
        self._interval_seconds = interval_seconds
        self._publisher = publisher
        self._clock = clock
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    async def tick(self) -> int:
        """单次扫描：遍历注册表处理到期触发器，返回本次触发数。

        单触发器意外异常隔离（告警跳过），不阻断扫描循环。
        """
        fired = 0
        for schedule in self._registry.list():
            try:
                if await self._process(schedule):
                    fired += 1
            except Exception:
                logger.warning(
                    "定时触发扫描处理异常（跳过）：%s",
                    schedule.schedule_id,
                    exc_info=True,
                )
        return fired

    async def _process(self, schedule: TriggerSchedule) -> bool:
        """处理单个触发器：到期判定 → 幂等去重 → mark_fired 先于 fire → P8。"""
        now = self._clock()
        if not schedule.enabled:
            return False
        fire_at = schedule.next_fire_at
        if fire_at is None:
            logger.warning(
                "定时触发器缺少 next_fire_at，跳过：%s",
                schedule.schedule_id,
            )
            return False
        if fire_at > now:
            return False
        fired_key = build_fired_key(
            schedule.workflow_id, schedule.trigger_node_id, fire_at
        )
        if schedule.last_fired_key == fired_key:
            logger.info(
                "定时触发幂等去重（同到期窗口已触发）：%s key=%s",
                schedule.schedule_id,
                fired_key,
            )
            return False
        try:
            following = resolve_next_fire(
                schedule.schedule, schedule.frequency_preset, fire_at
            )
        except WorkflowValidationError as error:
            logger.warning(
                "定时触发器下次触发计算失败（下轮重试）：%s code=%s",
                schedule.schedule_id,
                error.code,
            )
            return False
        # mark_fired 先于 fire：fire 异常/失败不重发——一次且仅一次（DC-28 规则 5）
        self._registry.mark_fired(schedule.schedule_id, fired_key, following)
        try:
            outcome = await self._fire(schedule, fire_at)
        except Exception:
            logger.warning(
                "定时触发 fire 回调异常（不重发）：%s",
                schedule.schedule_id,
                exc_info=True,
            )
            return False
        if outcome is None:
            logger.warning(
                "定时触发 fire 回调返回 None（不重发）：%s",
                schedule.schedule_id,
            )
            return False
        logger.info(
            "定时触发 fired：%s -> instance=%s fire_at=%s key=%s",
            schedule.schedule_id,
            outcome.instance_id,
            _fire_at_iso(fire_at),
            fired_key,
        )
        await self._publish_p8(schedule, fire_at, fired_key, outcome)
        return True

    async def _publish_p8(
        self,
        schedule: TriggerSchedule,
        fire_at: datetime,
        fired_key: str,
        outcome: ScheduleFireOutcome,
    ) -> None:
        """发布 P8：instance.triggered + instance.created（附录 A，DC-23）。

        逐层 try/except 降级（DC-31）：构造/发布失败告警跳过，
        不阻断扫描循环；publisher 为 None 直接旁路。
        """
        if self._publisher is None:
            return
        try:
            triggered = DomainEvent(
                event_type="instance.triggered",
                instance_id=outcome.instance_id,
                tenant_id=DEFAULT_TENANT_ID,
                trace_id=outcome.trace_id,
                payload={
                    "trigger_node_id": schedule.trigger_node_id,
                    "schedule": schedule.schedule,
                    "fire_at": _fire_at_iso(fire_at),
                    "idempotency_key": fired_key,
                },
            )
        except Exception:
            logger.warning(
                "instance.triggered 事件构造失败（降级跳过）：%s",
                schedule.schedule_id,
                exc_info=True,
            )
            return
        try:
            await self._publisher.publish_domain_event(triggered)
        except Exception:
            logger.warning(
                "instance.triggered 事件发布失败（降级跳过）：%s",
                schedule.schedule_id,
                exc_info=True,
            )
        try:
            created = DomainEvent(
                event_type="instance.created",
                instance_id=outcome.instance_id,
                tenant_id=DEFAULT_TENANT_ID,
                trace_id=outcome.trace_id,
                payload={"trigger_source": "cron"},
            )
            await self._publisher.publish_domain_event(created)
        except Exception:
            logger.warning(
                "instance.created 事件构造/发布失败（降级跳过）：%s",
                schedule.schedule_id,
                exc_info=True,
            )

    async def start(self) -> None:
        """启动扫描循环（幂等：已在运行直接返回）。"""
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """停止扫描循环（幂等；cancel 后等待退出并吞 CancelledError）。"""
        task = self._task
        if task is None:
            return
        self._stopping = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        """扫描主循环：先 tick 后 sleep（间隔 interval_seconds 秒）。"""
        while not self._stopping:
            try:
                await self.tick()
            except Exception:
                logger.warning("定时触发扫描 tick 异常（继续循环）", exc_info=True)
            await asyncio.sleep(self._interval_seconds)
