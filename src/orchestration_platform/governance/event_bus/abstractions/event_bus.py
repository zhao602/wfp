# =============================================================================
# 事件总线抽象接口（governance/event_bus/abstractions/event_bus.py）
# =============================================================================

from typing import AsyncIterator, Protocol, runtime_checkable

from orchestration_platform.governance.event_bus.models import (
    EventMessage,
    PublishResult,
    ReplayResult,
)


@runtime_checkable
class IEventBus(Protocol):
    """消息事件总线抽象接口。"""

    async def publish(
        self,
        topic: str,
        event: EventMessage,
    ) -> PublishResult:
        """发布事件至指定 topic。"""
        ...

    async def subscribe(
        self,
        topic: str,
        group_id: str,
    ) -> AsyncIterator[EventMessage]:
        """订阅 topic 事件流。"""
        ...

    async def replay_from_offset(
        self,
        topic: str,
        offset: int,
        max_events: int,
    ) -> ReplayResult:
        """从指定 offset 重放历史事件。"""
        ...

    @property
    def is_degraded(self) -> bool:
        """是否处于降级模式。"""
        ...
