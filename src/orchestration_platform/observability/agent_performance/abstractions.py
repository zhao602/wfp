# =============================================================================
# Agent 绩效仓储抽象（observability/agent_performance/abstractions.py）
#
# 职责：定义绩效仓储 Protocol（G3/T228）。
# 设计意图（对应 spec 5.3.2、design D5）：
#   1. append —— 追加明细；aggregate —— 一次返回完整四维聚合（A3.1）；
#   2. 无记录返回零值聚合，不抛异常（A3.3）；
#   3. data_source 属性暴露后端标识（降级可观测）。
# =============================================================================

from datetime import datetime
from typing import Protocol, runtime_checkable

from orchestration_platform.observability.agent_performance.models import (
    AgentPerformanceAggregate,
    AgentPerformanceRecord,
)


@runtime_checkable
class IPerformanceRepository(Protocol):
    """Agent 绩效仓储抽象接口。

    前置条件：无。
    后置条件：aggregate 一次返回完整四维聚合；无记录返回零值聚合不抛异常。
    """

    async def append(self, record: AgentPerformanceRecord) -> None:
        """追加一条绩效明细记录。"""
        ...

    async def aggregate(
        self, agent_id: str, start: datetime, end: datetime
    ) -> AgentPerformanceAggregate:
        """按 agent_id 与时间范围聚合，一次返回完整四维结果。

        无匹配记录时返回零值聚合（sample_count=0，指标全 0），不抛异常。
        """
        ...

    @property
    def data_source(self) -> str:
        """返回后端标识（"postgres" 或 "in_memory_degraded"）。"""
        ...
