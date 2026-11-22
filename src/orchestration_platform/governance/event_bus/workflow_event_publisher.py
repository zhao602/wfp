# =============================================================================
# 工作流执行事件发布（governance/event_bus/workflow_event_publisher.py）
# =============================================================================

import logging
import uuid
from typing import Any, Dict

from orchestration_platform.domain.models.enums import MessageEventType
from orchestration_platform.governance.event_bus.abstractions.event_bus import IEventBus
from orchestration_platform.governance.event_bus.models import EventMessage

_logger = logging.getLogger(__name__)


class WorkflowEventPublisher:
    """工作流执行事件发布器。"""

    def __init__(self, event_bus: IEventBus, topic: str) -> None:
        self._bus = event_bus
        self._topic = topic
        self._sequence = 0

    async def publish_execution_event(
        self,
        instance_id: str,
        node_id: str,
        status: str,
        payload: Dict[str, Any] = None,
    ) -> None:
        """发布工作流执行事件。"""
        self._sequence += 1
        event = EventMessage(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            event_type=MessageEventType.WORKFLOW_EXECUTION,
            sequence_number=self._sequence,
            payload={
                "instance_id": instance_id,
                "node_id": node_id,
                "status": status,
                **(payload or {}),
            },
            instance_id=instance_id,
        )
        await self._bus.publish(self._topic, event)
