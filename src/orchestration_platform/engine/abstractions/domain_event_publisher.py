# =============================================================================
# 领域事件发布器抽象（engine/abstractions/domain_event_publisher.py）
#
# 职责：定义引擎侧发布领域事件的统一出口接口（CHG-42 / DC-23 规则 1）。
# 设计意图：
#   1. 调度器/实例路由只依赖该抽象，不感知事件总线实现（Kafka/内存），
#      保证引擎层不反向依赖治理层基础设施；
#   2. 实现方必须自行吞掉发布异常并记录告警，不向调用方传播失败
#      （DC-31 降级语义，GK-06：基础设施故障不阻断工作流执行）；
#   3. 发布器为 None 时调用方直接旁路，保证默认配置零行为变化。
# =============================================================================

from typing import Protocol, runtime_checkable

from orchestration_platform.domain.models.domain_event import DomainEvent


@runtime_checkable
class IDomainEventPublisher(Protocol):
    """领域事件发布器抽象接口。"""

    async def publish_domain_event(self, event: DomainEvent) -> None:
        """发布单个领域事件；实现方须自吞异常并记录告警（DC-31，GK-06）。"""
        ...
