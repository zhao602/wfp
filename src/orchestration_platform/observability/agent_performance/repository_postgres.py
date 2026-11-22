# =============================================================================
# Agent 绩效 Postgres 仓储（observability/agent_performance/repository_postgres.py）
#
# 职责：Postgres 持久化实现（G3/T228）。
# 设计意图（对应 spec 5.3.2、design D5）：
#   1. asyncpg 延迟导入（无驱动沙箱中加载不失败）；
#   2. GROUP BY 单条聚合 SQL 一次返回完整四维结果（性能约束 4.1.3）；
#   3. 连接失败自动切换内存实现并记录降级日志（A3 异常场景 1）；
#   4. data_source 标识真实后端（"postgres"）或降级来源（"in_memory_degraded"）。
# =============================================================================

import logging
from datetime import datetime
from typing import Optional

from orchestration_platform.observability.agent_performance.models import (
    AgentPerformanceAggregate,
    AgentPerformanceRecord,
)
from orchestration_platform.observability.agent_performance.repository_local import (
    InMemoryPerformanceRepository,
)

logger = logging.getLogger(__name__)


class PostgresPerformanceRepository:
    """Postgres 绩效仓储：持久化明细 + 单条聚合 SQL。

    连接失败自动降级内存实现（degraded 标记可观测）。
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        db: str = "orchestration_platform",
        user: str = "postgres",
        password: str = "",
        memory_window_size: int = 10000,
    ) -> None:
        self._host = host
        self._port = port
        self._db = db
        self._user = user
        self._password = password
        # 降级内存实现（连接失败时启用）
        self._degraded: Optional[InMemoryPerformanceRepository] = None
        self._pool = None

    @property
    def data_source(self) -> str:
        """后端标识：真实 Postgres 或降级内存来源。"""
        if self._degraded is not None:
            return "in_memory_degraded"
        return "postgres"

    async def _get_pool(self):
        """延迟初始化连接池；asyncpg 缺失或连接失败时降级内存。"""
        if self._degraded is not None:
            return None
        if self._pool is not None:
            return self._pool
        try:
            import asyncpg
        except ImportError:
            logger.warning("asyncpg 不可用，Agent 绩效仓储降级内存实现")
            self._degraded = InMemoryPerformanceRepository()
            return None
        try:
            self._pool = await asyncpg.create_pool(
                host=self._host,
                port=self._port,
                database=self._db,
                user=self._user,
                password=self._password,
                min_size=1,
                max_size=5,
            )
            return self._pool
        except Exception as exc:
            logger.warning("Agent 绩效 Postgres 连接失败，降级内存实现: %s", exc)
            self._degraded = InMemoryPerformanceRepository()
            return None

    async def append(self, record: AgentPerformanceRecord) -> None:
        """追加明细记录。"""
        pool = await self._get_pool()
        if pool is None:
            await self._degraded.append(record)
            return
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_performance_records (
                        record_id BIGSERIAL PRIMARY KEY,
                        agent_id TEXT NOT NULL,
                        succeeded BOOLEAN NOT NULL,
                        duration_ms BIGINT NOT NULL DEFAULT 0,
                        token_count BIGINT NOT NULL DEFAULT 0,
                        quality_score DOUBLE PRECISION NOT NULL DEFAULT 0,
                        occurred_at TIMESTAMPTZ NOT NULL
                    )
                    """,
                )
                await conn.execute(
                    """
                    INSERT INTO agent_performance_records
                        (agent_id, succeeded, duration_ms, token_count, quality_score, occurred_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    record.agent_id,
                    record.succeeded,
                    record.duration_ms,
                    record.token_count,
                    record.quality_score,
                    record.occurred_at,
                )
        except Exception as exc:
            logger.warning("Agent 绩效写入失败，降级内存: %s", exc)
            if self._degraded is None:
                self._degraded = InMemoryPerformanceRepository()
            await self._degraded.append(record)

    async def aggregate(
        self, agent_id: str, start: datetime, end: datetime
    ) -> AgentPerformanceAggregate:
        """GROUP BY 单条聚合 SQL 一次返回完整四维结果（4.1.3）。

        无匹配记录返回零值聚合，不抛异常（A3.3）。
        """
        pool = await self._get_pool()
        if pool is None:
            return await self._degraded.aggregate(agent_id, start, end)
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_performance_records (
                        record_id BIGSERIAL PRIMARY KEY,
                        agent_id TEXT NOT NULL,
                        succeeded BOOLEAN NOT NULL,
                        duration_ms BIGINT NOT NULL DEFAULT 0,
                        token_count BIGINT NOT NULL DEFAULT 0,
                        quality_score DOUBLE PRECISION NOT NULL DEFAULT 0,
                        occurred_at TIMESTAMPTZ NOT NULL
                    )
                    """,
                )
                row = await conn.fetchrow(
                    """
                    SELECT
                        COUNT(*)::BIGINT AS total,
                        COUNT(*) FILTER (WHERE succeeded)::BIGINT AS succeeded_count,
                        COALESCE(AVG(duration_ms), 0)::DOUBLE PRECISION AS avg_duration,
                        COALESCE(SUM(token_count), 0)::BIGINT AS total_tokens,
                        COALESCE(AVG(quality_score), 0)::DOUBLE PRECISION AS avg_quality
                    FROM agent_performance_records
                    WHERE agent_id = $1 AND occurred_at >= $2 AND occurred_at <= $3
                    """,
                    agent_id,
                    start,
                    end,
                )
        except Exception as exc:
            logger.warning("Agent 绩效聚合查询失败，降级内存: %s", exc)
            if self._degraded is None:
                self._degraded = InMemoryPerformanceRepository()
            return await self._degraded.aggregate(agent_id, start, end)

        total = row["total"] or 0
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
            success_rate=(row["succeeded_count"] or 0) / total,
            avg_duration_ms=float(row["avg_duration"] or 0.0),
            total_tokens=int(row["total_tokens"] or 0),
            avg_quality_score=float(row["avg_quality"] or 0.0),
            sample_count=int(total),
            data_source=self.data_source,
        )
