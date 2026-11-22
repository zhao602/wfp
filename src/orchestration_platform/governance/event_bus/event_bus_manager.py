# =============================================================================
# 事件总线管理器（governance/event_bus/event_bus_manager.py）
#
# 职责：根据配置选择事件总线后端，提供统一访问入口。
# =============================================================================

import logging

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.event_bus.abstractions.event_bus import IEventBus
from orchestration_platform.governance.event_bus.in_memory_event_bus import (
    InMemoryEventBus,
)

_logger = logging.getLogger(__name__)


class EventBusManager:
    """事件总线管理器。

    根据配置选择 Kafka 或内存后端。
    """

    def __init__(self) -> None:
        """初始化事件总线管理器。"""
        settings = get_settings()
        self._is_degraded = True

        if settings.kafka_enabled and settings.kafka_bootstrap_servers:
            try:
                from orchestration_platform.governance.event_bus.kafka_event_bus import (
                    KafkaEventBus,
                )
                self._bus = KafkaEventBus(settings.kafka_bootstrap_servers)
                self._is_degraded = False
                _logger.info("事件总线初始化: Kafka 后端")
            except ImportError:
                self._init_degraded()
        else:
            self._init_degraded()

    def _init_degraded(self) -> None:
        """初始化降级内存后端。"""
        self._bus = InMemoryEventBus()
        self._is_degraded = True
        _logger.warning("事件总线初始化: 内存降级模式（MQ_DEGRADED）")

    @property
    def is_degraded(self) -> bool:
        return self._is_degraded

    def get_bus(self) -> IEventBus:
        """获取事件总线。"""
        return self._bus
