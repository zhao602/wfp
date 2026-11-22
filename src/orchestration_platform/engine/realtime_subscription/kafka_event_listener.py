# TODO(tech-debt): B档候选孤儿（族群 B10），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# Kafka 事件监听器（engine/realtime_subscription/kafka_event_listener.py）
#
# 职责：将 Kafka 连接器适配为订阅管理器所需的异步事件监听器。
# 设计意图：
#   1. 实现 async generator 接口，持续从 Kafka topic 消费消息并 yield 事件字典；
#   2. 与 RealtimeSubscriptionManager 的 register_event_listener 配合使用；
#   3. 无 aiokafka 驱动时 yield 空序列（优雅降级）。
# =============================================================================

import json
from typing import Any, AsyncIterator, Dict


async def kafka_event_listener(
    connection_reference: str,
    event_source: str,
) -> AsyncIterator[Dict[str, Any]]:
    """Kafka 事件监听器：持续消费 topic 消息并产出事件字典。

    Args:
        connection_reference: Kafka broker 列表（逗号分隔）。
        event_source: Kafka topic 名称。

    Yields:
        事件数据字典。消息值为 JSON 时自动解析，否则包装为 {"raw": value}。
    """
    try:
        from aiokafka import AIOKafkaConsumer
    except ImportError:
        return

    consumer = AIOKafkaConsumer(
        event_source,
        bootstrap_servers=connection_reference,
        group_id=f"orchestration_sub_{event_source}",
        auto_offset_reset="latest",
        enable_auto_commit=True,
        value_deserializer=lambda v: v.decode("utf-8") if v else None,
    )
    await consumer.start()
    try:
        async for record in consumer:
            try:
                value = json.loads(record.value)
            except (json.JSONDecodeError, TypeError):
                value = {"raw": record.value}
            value["_kafka_topic"] = record.topic
            value["_kafka_partition"] = record.partition
            value["_kafka_offset"] = record.offset
            value["_kafka_timestamp"] = record.timestamp
            yield value
    finally:
        await consumer.stop()
