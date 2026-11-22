# =============================================================================
# 会话事件流发布（governance/event_bus/session_event_publisher.py）
# =============================================================================

import uuid
from typing import Any, Dict

from orchestration_platform.domain.models.enums import MessageEventType
from orchestration_platform.governance.event_bus.abstractions.event_bus import IEventBus
from orchestration_platform.governance.event_bus.models import EventMessage


class SessionEventPublisher:
    """Agent 会话事件流发布器。"""

    def __init__(self, event_bus: IEventBus, topic: str) -> None:
        self._bus = event_bus
        self._topic = topic
        self._sequence = 0

    async def publish_session_event(
        self,
        session_id: str,
        old_state: str,
        new_state: str,
        payload: Dict[str, Any] = None,
    ) -> None:
        """发布会话状态变更事件。"""
        self._sequence += 1
        event = EventMessage(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            event_type=MessageEventType.SESSION_STATE_CHANGE,
            sequence_number=self._sequence,
            payload={
                "session_id": session_id,
                "old_state": old_state,
                "new_state": new_state,
                **(payload or {}),
            },
            instance_id=session_id,
        )
        await self._bus.publish(self._topic, event)
