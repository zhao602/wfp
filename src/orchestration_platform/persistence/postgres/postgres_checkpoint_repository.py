# =============================================================================
# PostgreSQL 检查点仓储（persistence/postgres/postgres_checkpoint_repository.py）
#
# 职责：以 PostgreSQL 持久化检查点，支持同事务提交。
# 设计意图（对应 spec 4.2.5、design 2.2.2.3）：
#   1. 检查点写入与事件追加可在同一事务内提交，保证位点一致；
#   2. latest_for_instance 返回 round_index 最大且 is_available=TRUE 的检查点；
#   3. 同轮幂等（arch_review_v2 3.1，CHG-15/DC-09）：(instance_id, round_index)
#      命中 uq_checkpoint_instance_round 唯一索引时 ON CONFLICT DO NOTHING——
#      重试/恢复场景的同轮重复写是合法重入，告警 + 返回既有标识，不抛异常；
#   4. 多租户强隔离（roadmap B1）：可选 tenant_id 参数缺省回退默认租户
#      （settings.default_tenant_id）；INSERT 显式写入 tenant_id；
#      append_in_transaction 外部连接路径经 _ensure_session_tenant 补设
#      会话变量（不覆盖调用方已注入的租户上下文）。
# =============================================================================

import logging
from typing import List, Optional

from orchestration_platform.domain.models.checkpoint import (
    Checkpoint,
    CheckpointSummary,
)
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


class PostgresCheckpointRepository(TenantScopedPostgresRepository):
    """检查点仓储——PostgreSQL 实现。"""

    async def append(
        self, checkpoint: Checkpoint, tenant_id: Optional[str] = None
    ) -> str:
        """追加检查点（同轮幂等，arch_review_v2 3.1，CHG-15/DC-09）。

        (instance_id, round_index) 命中唯一索引时 ON CONFLICT DO NOTHING：
        重试/恢复场景的同轮重复写是合法重入——告警日志 + 返回既有检查点标识，
        不抛异常、不产生重复行。tenant_id 缺省回退默认租户。
        """
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            inserted = await conn.fetchrow(
                """
                INSERT INTO checkpoint
                    (tenant_id, checkpoint_id, instance_id, completed_node_id, next_node_id,
                     state_snapshot, round_index, is_available, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                ON CONFLICT (instance_id, round_index) DO NOTHING
                RETURNING checkpoint_id
                """,
                resolved_tenant, checkpoint.checkpoint_id, checkpoint.instance_id,
                checkpoint.completed_node_id, checkpoint.next_node_id,
                checkpoint.state_snapshot.model_dump_json(),
                checkpoint.round_index, checkpoint.is_available,
            )
            if inserted is not None:
                return checkpoint.checkpoint_id

            existing = await conn.fetchrow(
                "SELECT checkpoint_id FROM checkpoint "
                "WHERE instance_id=$1 AND round_index=$2 AND tenant_id=$3",
                checkpoint.instance_id, checkpoint.round_index, resolved_tenant,
            )
            existing_id = (
                existing["checkpoint_id"] if existing is not None else checkpoint.checkpoint_id
            )
            logging.warning(
                "检查点同轮重复写被幂等吸收: instance_id=%s round_index=%s"
                " 既有=%s 新写=%s（重试/恢复合法重入，不抛异常）",
                checkpoint.instance_id, checkpoint.round_index,
                existing_id, checkpoint.checkpoint_id,
            )
            return existing_id

    async def append_in_transaction(
        self,
        conn,
        checkpoint: Checkpoint,
        tenant_id: Optional[str] = None,
    ) -> str:
        """在给定连接（事务内）追加检查点，保证与事件同事务提交。"""
        resolved_tenant = self._resolve_tenant(tenant_id)
        await self._ensure_session_tenant(conn, resolved_tenant)
        await conn.execute(
            """
            INSERT INTO checkpoint
                (tenant_id, checkpoint_id, instance_id, completed_node_id, next_node_id,
                 state_snapshot, round_index, is_available, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            """,
            resolved_tenant, checkpoint.checkpoint_id, checkpoint.instance_id,
            checkpoint.completed_node_id, checkpoint.next_node_id,
            checkpoint.state_snapshot.model_dump_json(),
            checkpoint.round_index, checkpoint.is_available,
        )
        return checkpoint.checkpoint_id

    async def get(
        self, checkpoint_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Checkpoint]:
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM checkpoint WHERE checkpoint_id=$1 AND tenant_id=$2",
                checkpoint_id, resolved_tenant,
            )
        if row is None:
            return None
        return self._row_to_checkpoint(row)

    async def latest_for_instance(
        self, instance_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Checkpoint]:
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM checkpoint "
                "WHERE instance_id=$1 AND tenant_id=$2 AND is_available=TRUE "
                "ORDER BY round_index DESC LIMIT 1",
                instance_id, resolved_tenant,
            )
        if row is None:
            return None
        return self._row_to_checkpoint(row)

    async def list_for_instance(
        self, instance_id: str, tenant_id: Optional[str] = None
    ) -> List[CheckpointSummary]:
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            rows = await conn.fetch(
                "SELECT checkpoint_id, instance_id, completed_node_id, next_node_id, "
                "round_index, is_available, created_at FROM checkpoint "
                "WHERE instance_id=$1 AND tenant_id=$2 ORDER BY round_index DESC",
                instance_id, resolved_tenant,
            )
        return [
            CheckpointSummary(
                checkpoint_id=r["checkpoint_id"], instance_id=r["instance_id"],
                completed_node_id=r["completed_node_id"], next_node_id=r["next_node_id"],
                round_index=r["round_index"], created_at=r["created_at"],
                is_available=r["is_available"],
            )
            for r in rows
        ]

    async def mark_unavailable(
        self,
        checkpoint_id: str,
        reason: str,
        tenant_id: Optional[str] = None,
    ) -> None:
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            await conn.execute(
                "UPDATE checkpoint SET is_available=FALSE, unavailable_reason=$2 "
                "WHERE checkpoint_id=$1 AND tenant_id=$3",
                checkpoint_id, reason, resolved_tenant,
            )

    def _row_to_checkpoint(self, row) -> Checkpoint:
        from orchestration_platform.domain.models.shared_state import SharedState
        return Checkpoint(
            checkpoint_id=row["checkpoint_id"], instance_id=row["instance_id"],
            completed_node_id=row["completed_node_id"], next_node_id=row["next_node_id"],
            state_snapshot=SharedState.model_validate_json(row["state_snapshot"]),
            round_index=row["round_index"], created_at=row["created_at"],
            is_available=row["is_available"],
            unavailable_reason=row.get("unavailable_reason"),
        )
