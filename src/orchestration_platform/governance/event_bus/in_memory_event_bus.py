# =============================================================================
# 内存事件总线实现（governance/event_bus/in_memory_event_bus.py）
#
# 职责：进程内事件总线实现，用于降级模式。
# 设计意图（对应偏差修正 spec 5.5.1.6 / design 模块E InMemoryEventBus）：
#   - 使用 asyncio.Queue 按 topic 隔离
#   - 进程内有效，日志提示降级模式
#   - roadmap B3 扇出改造：topic → {group_id → queue} 二级隔离——
#     同 group_id 组内竞争（Kafka 消费组语义对齐），不同 group_id 各得
#     全量事件（扇出）；此前每 topic 单共享队列的多订阅方竞争分流为
#     已知缺陷（审计/Webhook 出口/SSE 同挂会互相抢事件），本批修复。
# =============================================================================

import asyncio
import logging
from typing import AsyncIterator, Dict, List, Optional

from orchestration_platform.governance.event_bus.models import (
    EventMessage,
    PublishResult,
    ReplayResult,
)

_logger = logging.getLogger(__name__)


class InMemoryEventBus:
    """内存事件总线实现（降级模式）。

    使用 asyncio.Queue 按 topic + group_id 二级隔离（roadmap B3 扇出改造）：
    同 group_id 共队列（组内竞争），不同 group_id 各得全量（扇出）。
    """

    def __init__(self, history_max: Optional[int] = None) -> None:
        """初始化内存事件总线。

        Args:
            history_max: 每个主题保留的历史事件条数上限；None 表示不限制
                （CHG-67：默认不限制以保持既有行为，上限保护由配置注入）。
        """
        self._group_queues: Dict[str, Dict[str, asyncio.Queue]] = {}
        self._event_history: Dict[str, List[EventMessage]] = {}
        self._history_max = history_max

    def _get_group_queue(self, topic: str, group_id: str) -> asyncio.Queue:
        """取/建 topic 下指定消费组的队列（首次建档时同步建历史列表）。"""
        if topic not in self._group_queues:
            self._group_queues[topic] = {}
            self._event_history.setdefault(topic, [])
        queues = self._group_queues[topic]
        if group_id not in queues:
            queues[group_id] = asyncio.Queue()
        return queues[group_id]

    def _ensure_history(self, topic: str) -> None:
        """确保主题历史列表已建档（无订阅方时 publish 亦可重放）。"""
        if topic not in self._event_history:
            self._event_history[topic] = []
        self._group_queues.setdefault(topic, {})

    async def publish(
        self,
        topic: str,
        event: EventMessage,
    ) -> PublishResult:
        """发布事件至内存队列（扇出：该 topic 全部消费组各投一份）。"""
        degraded_event = event.model_copy(update={"is_degraded": True})
        self._ensure_history(topic)
        for queue in self._group_queues[topic].values():
            await queue.put(degraded_event)
        self._event_history[topic].append(degraded_event)
        # 历史上限保护（CHG-67）：超出上限丢弃最旧事件，防止长期运行内存膨胀。
        if self._history_max is not None:
            history = self._event_history[topic]
            dropped = len(history) - self._history_max
            if dropped > 0:
                del history[:dropped]
                _logger.warning("事件历史达到上限，丢弃最旧 %d 条: topic=%s", dropped, topic)
        return PublishResult(success=True, is_degraded=True)

    async def subscribe(
        self,
        topic: str,
        group_id: str,
    ) -> AsyncIterator[EventMessage]:
        """订阅内存队列事件流（同 group_id 组内竞争）。"""
        queue = self._get_group_queue(topic, group_id)
        while True:
            event = await queue.get()
            yield event

    def unsubscribe(
        self,
        topic: str,
        group_id: str,
    ) -> bool:
        """移除指定消费组队列（SSE 断连清理防泄漏）；返回是否实际移除。

        仅内存总线实有（IEventBus 协议不强制）；组内仍持有旧队列引用的
        消费循环不会再收到新事件（安全终止语义）。
        """
        queues = self._group_queues.get(topic)
        if queues is None or group_id not in queues:
            return False
        del queues[group_id]
        return True

    async def replay_from_offset(
        self,
        topic: str,
        offset: int,
        max_events: int,
    ) -> ReplayResult:
        """从内存事件历史重放。"""
        history = self._event_history.get(topic, [])
        events = history[offset:offset + max_events]
        return ReplayResult(events=events)

    @property
    def is_degraded(self) -> bool:
        return True
