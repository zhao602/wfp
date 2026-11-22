# =============================================================================
# Agent 绩效内存仓储（observability/agent_performance/repository_local.py）
#
# 职责：内存环形窗口实现（默认后端，G3/T228）。
# 设计意图（对应 spec 5.3.2、design D5）：
#   1. 固定容量环形窗口（agent_performance_memory_window_size，默认 10000 条，
#      先进先出淘汰）；
#   2. 聚合为单遍扫描 O(窗口)；
#   3. data_source 恒 "in_memory_degraded"（内存降级标识，A3 异常场景 1）。
# =============================================================================

import threading
from collections import deque
from datetime import datetime
from typing import Deque

from orchestration_platform.observability.agent_performance.models import (
    AgentPerformanceAggregate,
    AgentPerformanceRecord,
)


class InMemoryPerformanceRepository:
    """内存绩效仓储：固定容量环形窗口 + 单遍聚合。

    构造参数：
        window_size —— 环形窗口容量（默认 10000，先进先出淘汰）
    """

    def __init__(self, window_size: int = 10000) -> None:
        if window_size < 1:
            window_size = 1
        self._window_size = window_size
        self._records: Deque[AgentPerformanceRecord] = deque(maxlen=window_size)
        self._lock = threading.Lock()

    @property
    def data_source(self) -> str:
        """后端标识：内存实现恒为降级标识。"""
        return "in_memory_degraded"

    async def append(self, record: AgentPerformanceRecord) -> None:
        """追加明细（窗口满时最旧记录先进先出淘汰）。"""
        with self._lock:
            self._records.append(record)

    async def aggregate(
        self, agent_id: str, start: datetime, end: datetime
    ) -> AgentPerformanceAggregate:
        """单遍扫描聚合（O(窗口)）。

        时间范围过滤：start ≤ occurred_at ≤ end（含边界）。
        无匹配记录返回零值聚合，不抛异常（A3.3）。
        """
        total = 0
        succeeded_count = 0
        duration_sum = 0
        token_sum = 0
        quality_sum = 0.0

        with self._lock:
            snapshot = list(self._records)
        for rec in snapshot:
            if rec.agent_id != agent_id:
                continue
            if rec.occurred_at < start or rec.occurred_at > end:
                continue
            total += 1
            if rec.succeeded:
                succeeded_count += 1
            duration_sum += rec.duration_ms
            token_sum += rec.token_count
            quality_sum += rec.quality_score

        if total == 0:
            return AgentPerformanceAggregate(
                agent_id=agent_id,
                start=start,
                end=end,
                success_rate=0.0,
                avg_duration_ms=0.0,
                total_tokens=0,
                avg_quality_score=0.0,
                sample_count=0,
                data_source=self.data_source,
            )

        return AgentPerformanceAggregate(
            agent_id=agent_id,
            start=start,
            end=end,
            success_rate=succeeded_count / total,
            avg_duration_ms=duration_sum / total,
            total_tokens=token_sum,
            avg_quality_score=quality_sum / total,
            sample_count=total,
            data_source=self.data_source,
        )

    def __len__(self) -> int:
        """当前窗口内记录数。"""
        return len(self._records)
