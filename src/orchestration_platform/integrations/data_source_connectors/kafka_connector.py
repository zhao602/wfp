# =============================================================================
# Kafka 数据源连接器（integrations/data_source_connectors/kafka_connector.py）
#
# 职责：以消费者模式从 Kafka topic 拉取消息，支持分页（批量拉取）与 Schema 推断。
# 设计意图（对应项目构建计划书「数据集成」MCP 连接器要求）：
#   1. 使用 aiokafka（延迟导入）实现异步消费；
#   2. 分页语义：offset=已消费偏移量，limit=单次拉取最大消息数；
#   3. 消息值为 JSON 时自动解析为 dict，否则包装为 {"raw": value}。
# =============================================================================

import json
from typing import Any, Dict, List

from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceConnectionConfig,
    DataSourceReadRequest,
    DataSourceReadResult,
    DataSchema,
    SchemaField,
)
from orchestration_platform.integrations.data_source_connectors.probe_support import (
    PROBE_TIMEOUT_SECONDS,
    build_connection_error,
    build_param_error,
    require_driver,
)


class KafkaDataSourceConnector:
    """Kafka 消息流数据源连接器（只读消费）。"""

    async def connect(self, config: DataSourceConnectionConfig) -> None:
        """验证 Kafka broker 与 topic 配置。"""
        if not config.kafka_brokers:
            from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
            raise DomainError(DATASOURCE_CONNECTION_FAILED, "Kafka broker 列表未配置")
        if not config.kafka_topic:
            from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
            raise DomainError(DATASOURCE_CONNECTION_FAILED, "Kafka topic 未配置")

    async def probe(self, config: DataSourceConnectionConfig, level: str = "liveness") -> None:
        """只读连通性探测：真实连接 Kafka broker 后立即停止（无副作用）。

        双探针级别（Q-6）：
          - liveness：仅验证 broker 连通（start() 触发元数据拉取即可）；
          - readiness：额外校验 kafka_topic 存在。

        四阶段：① 参数预检 → ② 驱动守卫 → ③ 真实探测 → ④ finally 释放。
        """
        import asyncio

        # ① 参数预检
        if not config.kafka_brokers:
            raise build_param_error("Kafka broker 列表未配置")
        if not config.kafka_topic:
            raise build_param_error("Kafka topic 未配置")
        first_broker = config.kafka_brokers.split(",")[0].strip()
        if ":" not in first_broker:
            raise build_param_error("Kafka broker 格式无效（应为 host:port）")
        # ② 驱动守卫
        aiokafka = require_driver("aiokafka")
        # ③ 真实探测（只读，10s 超时）
        consumer = None
        try:
            consumer = aiokafka.AIOKafkaConsumer(
                bootstrap_servers=config.kafka_brokers,
                group_id=config.kafka_group_id,
                auto_offset_reset=config.kafka_offset or "earliest",
                enable_auto_commit=False,
            )
            await asyncio.wait_for(consumer.start(), timeout=PROBE_TIMEOUT_SECONDS)
            if level == "readiness":
                partitions = consumer.partitions_for_topic(config.kafka_topic)
                if not partitions:
                    raise build_connection_error(
                        Exception("TopicNotFound"),
                        f"Kafka topic 不存在：{config.kafka_topic}",
                    )
        except Exception as exc:
            if getattr(exc, "code", "") == "DATASOURCE_CONNECTION_FAILED":
                raise
            raise build_connection_error(exc, "Kafka 连接失败")
        # ④ finally 释放
        finally:
            if consumer is not None:
                try:
                    await consumer.stop()
                except Exception:
                    pass

    async def read(self, request: DataSourceReadRequest) -> DataSourceReadResult:
        """从 Kafka topic 批量拉取消息。

        使用 aiokafka（延迟导入），无驱动时返回空结果。
        分页：limit=单次最大消息数，消费后返回 next_cursor 为最后一条消息的 offset+1。
        """
        config = request.connection_config
        limit = request.pagination.limit

        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError:
            return DataSourceReadResult(data=[], has_more=False)

        try:
            consumer = AIOKafkaConsumer(
                config.kafka_topic,
                bootstrap_servers=config.kafka_brokers,
                group_id=config.kafka_group_id,
                auto_offset_reset=config.kafka_offset,
                enable_auto_commit=False,
                value_deserializer=lambda v: v.decode("utf-8") if v else None,
            )
            await consumer.start()
            try:
                data: List[Dict[str, Any]] = []
                next_offset = None
                # 批量拉取 limit 条
                async for record in consumer:
                    try:
                        value = json.loads(record.value)
                    except (json.JSONDecodeError, TypeError):
                        value = {"raw": record.value}
                    value["_kafka_offset"] = record.offset
                    value["_kafka_partition"] = record.partition
                    data.append(value)
                    next_offset = record.offset + 1
                    if len(data) >= limit:
                        break

                has_more = len(data) == limit
                return DataSourceReadResult(
                    data=data,
                    total_count=len(data),
                    has_more=has_more,
                    next_cursor=str(next_offset) if next_offset else None,
                )
            finally:
                await consumer.stop()
        except Exception as exc:
            from orchestration_platform.domain.errors import DATASOURCE_STREAM_CONSUME_FAILED, DomainError
            raise DomainError(DATASOURCE_STREAM_CONSUME_FAILED, f"Kafka 消费失败: {type(exc).__name__}")

    async def infer_schema(self, config: DataSourceConnectionConfig) -> DataSchema:
        """从首条消息推断 Schema（JSON 消息的键作为字段）。"""
        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError:
            return DataSchema(fields=[])

        try:
            consumer = AIOKafkaConsumer(
                config.kafka_topic,
                bootstrap_servers=config.kafka_brokers,
                group_id=config.kafka_group_id,
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                value_deserializer=lambda v: v.decode("utf-8") if v else None,
            )
            await consumer.start()
            try:
                fields: List[SchemaField] = []
                async for record in consumer:
                    try:
                        value = json.loads(record.value)
                        if isinstance(value, dict):
                            for key, val in value.items():
                                fields.append(SchemaField(
                                    name=key,
                                    type=self._infer_type(val),
                                    nullable=True,
                                ))
                    except (json.JSONDecodeError, TypeError):
                        pass
                    break
                return DataSchema(fields=fields)
            finally:
                await consumer.stop()
        except Exception:
            return DataSchema(fields=[])

    @staticmethod
    def _infer_type(value: Any) -> str:
        """从 Python 值推断字段类型。"""
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "float"
        if isinstance(value, (dict,)):
            return "object"
        if isinstance(value, (list,)):
            return "array"
        return "string"

    async def close(self) -> None:
        pass
