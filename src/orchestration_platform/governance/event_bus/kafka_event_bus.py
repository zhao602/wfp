# =============================================================================
# Kafka 事件总线实现（governance/event_bus/kafka_event_bus.py）
#
# 职责：通过 Kafka 实现分布式事件总线。
# 设计意图（对应偏差修正 spec 5.5 / design 模块E KafkaEventBus）：
#   - 延迟导入 aiokafka，ImportError 时由上层降级
#   - 生产者发布事件，消费者订阅事件
#   - 支持从 offset 重放历史事件
# =============================================================================

import json
import logging
from typing import AsyncIterator

from orchestration_platform.governance.event_bus.models import (
    EventMessage,
    PublishResult,
    ReplayResult,
)

_logger = logging.getLogger(__name__)


class KafkaEventBus:
    """Kafka 事件总线实现。

    延迟导入 aiokafka，连接不可用时抛出异常由上层降级。
    """

    def __init__(self, bootstrap_servers: str) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._producer = None

    def _get_producer(self):
        if self._producer is not None:
            return self._producer
        try:
            from aiokafka import AIOKafkaProducer
            import asyncio
            loop = asyncio.get_event_loop()
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap_servers,
                loop=loop,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )
            return self._producer
        except ImportError:
            raise ImportError("aiokafka 包未安装，请安装 aiokafka 依赖")

    async def publish(
        self,
        topic: str,
        event: EventMessage,
    ) -> PublishResult:
        """发布事件至 Kafka topic。"""
        producer = self._get_producer()
        if not producer._sender:
            await producer.start()
        payload = event.model_dump(mode="json")
        offset = await producer.send_and_wait(topic, payload)
        return PublishResult(success=True, offset=offset.offset if hasattr(offset, "offset") else None)

    async def subscribe(
        self,
        topic: str,
        group_id: str,
    ) -> AsyncIterator[EventMessage]:
        """订阅 Kafka topic 事件流。"""
        from aiokafka import AIOKafkaConsumer
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=group_id,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )
        await consumer.start()
        try:
            async for msg in consumer:
                yield EventMessage.model_validate(msg.value)
        finally:
            await consumer.stop()

    async def replay_from_offset(
        self,
        topic: str,
        offset: int,
        max_events: int,
    ) -> ReplayResult:
        """从指定 offset 重放历史事件。"""
        from aiokafka import AIOKafkaConsumer
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap_servers,
            auto_offset_reset="earliest",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )
        await consumer.start()
        events = []
        try:
            async for msg in consumer:
                if msg.offset < offset:
                    continue
                events.append(EventMessage.model_validate(msg.value))
                if len(events) >= max_events:
                    break
        finally:
            await consumer.stop()
        return ReplayResult(events=events)

    @property
    def is_degraded(self) -> bool:
        return False
