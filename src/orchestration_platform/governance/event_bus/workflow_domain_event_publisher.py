# =============================================================================
# 工作流领域事件发布器（governance/event_bus/workflow_domain_event_publisher.py）
#
# 职责：将 DomainEvent 封装为 EventMessage 发布到事件总线
#      （CHG-43 / DC-23 / DC-31）。
# 设计意图：
#   1. 复用既有事件总线信封（MessageEventType.WORKFLOW_EXECUTION），
#      不新增消息类型（零新增枚举约束）；
#   2. 序列号在发布器实例内自增，保证同一发布器的消息顺序单调；
#   3. 发布失败降级吞异常并记录告警，不向调度器/路由传播
#      （DC-31，GK-06）。
# =============================================================================

import logging
import uuid

from orchestration_platform.domain.models.domain_event import DomainEvent
from orchestration_platform.domain.models.enums import MessageEventType
from orchestration_platform.governance.event_bus.abstractions.event_bus import IEventBus
from orchestration_platform.governance.event_bus.models import EventMessage

_logger = logging.getLogger(__name__)


class WorkflowDomainEventPublisher:
    """工作流领域事件发布器：领域事件到事件总线的适配实现。"""

    def __init__(self, event_bus: IEventBus, topic: str) -> None:
        """初始化发布器。

        Args:
            event_bus: 事件总线实例（Kafka 或内存实现）。
            topic: 发布主题（复用 settings.kafka_workflow_topic）。
        """
        self._bus = event_bus
        self._topic = topic
        self._sequence = 0

    async def publish_domain_event(self, event: DomainEvent) -> None:
        """将领域事件封装为 EventMessage 发布；失败降级吞异常（DC-31）。"""
        self._sequence += 1
        message = EventMessage(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            event_type=MessageEventType.WORKFLOW_EXECUTION,
            sequence_number=self._sequence,
            payload={"domain_event": event.model_dump(mode="json")},
            instance_id=event.instance_id,
            occurred_at=event.occurred_at,
        )
        try:
            await self._bus.publish(self._topic, message)
        except Exception:
            _logger.warning(
                "领域事件发布失败（降级吞异常）: event_type=%s, instance_id=%s",
                event.event_type,
                event.instance_id,
                exc_info=True,
            )
