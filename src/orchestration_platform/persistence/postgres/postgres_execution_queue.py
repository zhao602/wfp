# =============================================================================
# PostgreSQL 执行队列（persistence/postgres/postgres_execution_queue.py）
#
# 职责：以 PostgreSQL 表 execution_job 持久化执行队列，保证认领原子性与租约语义。
# 设计意图（roadmap-v3 中期窗口 M1，plan D2，F1 执行队列与提交路径统一）：
#   1. 认领原子性（两副本不重复执行，plan D3）：单条 UPDATE ... WHERE job_id =
#      (SELECT ... FOR UPDATE SKIP LOCKED) 子查询认领——数据库原子单赢；
#   2. 租约语义：认领即持租约（默认 30s），heartbeat 续期；
#      take_over_expired 接管租约过期的 running 作业（at-least-once）；
#   3. 提交幂等：idempotency_key 部分唯一索引
#      idx_execution_job_idempotency（v1_8 迁移；先例 execution_instance
#      幂等键 table_definitions.sql:46-48）+ ON CONFLICT DO NOTHING 后回读既有；
#   4. 队列操作走 _admin_connection（app.bypass_rls='on'）：execution_job 为
#      worker 系统级认领资源（跨租户扫描），不启用 RLS（基础设施表口径，
#      对齐 schema_versions 例外；tenant_id 列仅供作业归属统计）；
#   5. 复用 TenantScopedPostgresRepository 连接池管理（零新增依赖）。
# =============================================================================

import json
from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.execution_job import ExecutionJob
from orchestration_platform.persistence.abstractions.execution_queue import (
    IExecutionQueue,
)
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


class PostgresExecutionQueue(TenantScopedPostgresRepository):
    """执行队列——PostgreSQL 实现（SKIP LOCKED 认领单赢 + 租约列）。"""

    def _default_ttl_seconds(self, lease_ttl_seconds: Optional[int]) -> int:
        if lease_ttl_seconds is not None:
            return lease_ttl_seconds
        return get_settings().execution_queue_lease_seconds

    @staticmethod
    def _row_to_job(row) -> ExecutionJob:
        """asyncpg 行 → ExecutionJob（时间戳 ISO 字符串化）。"""
        return ExecutionJob(
            job_id=row["job_id"],
            instance_id=row["instance_id"],
            tenant_id=row["tenant_id"],
            definition_id=row["definition_id"],
            definition_version=row["definition_version"],
            yaml_text=row["yaml_text"],
            initial_inputs=dict(row["initial_inputs"] or {}),
            trigger_source=row["trigger_source"],
            trace_id=row["trace_id"],
            resume_checkpoint_id=row["resume_checkpoint_id"],
            idempotency_key=row["idempotency_key"],
            status=row["status"],
            lease_owner=row["lease_owner"],
            lease_expires_at=(
                row["lease_expires_at"].isoformat()
                if row["lease_expires_at"] is not None
                else None
            ),
            attempts=row["attempts"],
            last_error=row["last_error"],
            created_at=(
                row["created_at"].isoformat()
                if row["created_at"] is not None
                else None
            ),
        )

    # ------------------------------------------------------------------
    # IExecutionQueue 实现（系统级通道：app.bypass_rls='on'）
    # ------------------------------------------------------------------

    async def enqueue(self, job: ExecutionJob) -> ExecutionJob:
        """入队作业（ON CONFLICT DO NOTHING；幂等键冲突回读既有非终态作业）。"""
        async with self._admin_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO execution_job (
                    job_id, instance_id, tenant_id, definition_id,
                    definition_version, yaml_text, initial_inputs,
                    trigger_source, trace_id, resume_checkpoint_id,
                    idempotency_key, status, attempts, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7::jsonb,
                    $8, $9, $10, $11, 'pending', 0, NOW()
                )
                ON CONFLICT DO NOTHING
                RETURNING *
                """,
                job.job_id,
                job.instance_id,
                job.tenant_id or "tenant_default",
                job.definition_id,
                job.definition_version,
                job.yaml_text,
                json.dumps(job.initial_inputs, ensure_ascii=False, default=str),
                job.trigger_source,
                job.trace_id,
                job.resume_checkpoint_id,
                job.idempotency_key,
            )
            if row is not None:
                return self._row_to_job(row)
            # 幂等回读：同 job_id 或同 idempotency_key 的既有作业
            existing = await conn.fetchrow(
                """
                SELECT * FROM execution_job
                WHERE job_id = $1
                   OR (idempotency_key = $2 AND idempotency_key IS NOT NULL)
                ORDER BY (status IN ('completed', 'failed', 'cancelled')) ASC,
                         created_at ASC
                LIMIT 1
                """,
                job.job_id,
                job.idempotency_key,
            )
            if existing is not None:
                return self._row_to_job(existing)
            return job

    async def claim(
        self, worker_id: str, lease_ttl_seconds: Optional[int] = None
    ) -> Optional[ExecutionJob]:
        """原子认领一个 pending 作业并持租约（FOR UPDATE SKIP LOCKED 单赢）。"""
        async with self._admin_connection() as conn:
            row = await conn.fetchrow(
                """
                UPDATE execution_job
                SET status = 'running',
                    lease_owner = $1,
                    lease_expires_at = NOW() + ($2::int * interval '1 second'),
                    attempts = attempts + 1
                WHERE job_id = (
                    SELECT job_id FROM execution_job
                    WHERE status = 'pending'
                    ORDER BY created_at, job_id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                worker_id,
                self._default_ttl_seconds(lease_ttl_seconds),
            )
            return self._row_to_job(row) if row is not None else None

    async def take_over_expired(
        self, worker_id: str, lease_ttl_seconds: Optional[int] = None
    ) -> Optional[ExecutionJob]:
        """接管一个租约已过期的 running 作业（SKIP LOCKED 单赢，attempts+1）。"""
        async with self._admin_connection() as conn:
            row = await conn.fetchrow(
                """
                UPDATE execution_job
                SET lease_owner = $1,
                    lease_expires_at = NOW() + ($2::int * interval '1 second'),
                    attempts = attempts + 1
                WHERE job_id = (
                    SELECT job_id FROM execution_job
                    WHERE status = 'running'
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < NOW()
                    ORDER BY lease_expires_at, job_id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                worker_id,
                self._default_ttl_seconds(lease_ttl_seconds),
            )
            return self._row_to_job(row) if row is not None else None

    async def heartbeat(self, job_id: str, worker_id: str) -> bool:
        """心跳续租：仅 lease_owner 匹配且 running 时刷新到期时刻。"""
        async with self._admin_connection() as conn:
            row = await conn.fetchrow(
                """
                UPDATE execution_job
                SET lease_expires_at = NOW() + ($3::int * interval '1 second')
                WHERE job_id = $1 AND lease_owner = $2 AND status = 'running'
                RETURNING job_id
                """,
                job_id,
                worker_id,
                self._default_ttl_seconds(None),
            )
            return row is not None

    async def complete(self, job_id: str, worker_id: str) -> bool:
        """作业完成流转终态（仅本 worker 持有且 running 时生效）。"""
        async with self._admin_connection() as conn:
            row = await conn.fetchrow(
                """
                UPDATE execution_job
                SET status = 'completed',
                    lease_owner = NULL,
                    lease_expires_at = NULL
                WHERE job_id = $1 AND lease_owner = $2 AND status = 'running'
                RETURNING job_id
                """,
                job_id,
                worker_id,
            )
            return row is not None

    async def fail(self, job_id: str, worker_id: str, reason: str = "") -> bool:
        """作业失败流转终态（仅本 worker 持有且 running 时生效）。"""
        async with self._admin_connection() as conn:
            row = await conn.fetchrow(
                """
                UPDATE execution_job
                SET status = 'failed',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error = $3
                WHERE job_id = $1 AND lease_owner = $2 AND status = 'running'
                RETURNING job_id
                """,
                job_id,
                worker_id,
                reason or None,
            )
            return row is not None

    async def get(self, job_id: str) -> Optional[ExecutionJob]:
        """按标识查询作业；不存在返回 None。"""
        async with self._admin_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM execution_job WHERE job_id = $1",
                job_id,
            )
            return self._row_to_job(row) if row is not None else None
