# =============================================================================
# PostgreSQL 执行事件仓储（persistence/postgres/postgres_event_repository.py）
#
# 职责：以 PostgreSQL 持久化执行事件，保证序号严格递增与同事务提交。
# 设计意图（对应 spec 4.2.5、6.5、design 2.2.2.3）：
#   1. sequence 在事务内以"实例当前最大序号 + 1"生成，保证严格递增；
#   2. void_after 仅置 voided=TRUE，绝不物理删除（事件溯源不可变原则）；
#   3. 多租户强隔离（roadmap B1）：可选 tenant_id 参数缺省回退默认租户
#      （settings.default_tenant_id）；INSERT 显式写入 tenant_id；
#      append_in_transaction 外部连接路径经 _ensure_session_tenant 补设
#      会话变量（不覆盖调用方已注入的租户上下文）。
# =============================================================================

from typing import List, Optional

from orchestration_platform.domain.errors import WF_VALIDATION_FAILED, DomainError
from orchestration_platform.domain.models.execution_event import ExecutionEvent
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


class PostgresEventRepository(TenantScopedPostgresRepository):
    """执行事件仓储——PostgreSQL 实现。"""

    async def append(
        self, event: ExecutionEvent, tenant_id: Optional[str] = None
    ) -> int:
        """追加事件，事务内生成严格递增序号（tenant_id 缺省回退默认租户）。"""
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            return await self._append_in_connection(conn, event, resolved_tenant)

    async def append_in_transaction(
        self,
        conn,
        event: ExecutionEvent,
        tenant_id: Optional[str] = None,
    ) -> int:
        """在给定连接（事务内）追加事件，保证与检查点同事务提交。"""
        resolved_tenant = self._resolve_tenant(tenant_id)
        await self._ensure_session_tenant(conn, resolved_tenant)
        return await self._append_in_connection(conn, event, resolved_tenant)

    async def _append_in_connection(
        self, conn, event: ExecutionEvent, resolved_tenant: str
    ) -> int:
        # 事务内查询当前最大序号并分配新序号
        row = await conn.fetchrow(
            "SELECT COALESCE(MAX(sequence), 0) AS max_seq FROM execution_event "
            "WHERE instance_id=$1 AND tenant_id=$2",
            event.instance_id, resolved_tenant,
        )
        current_max = row["max_seq"] if row else 0
        assigned_sequence = current_max + 1

        if event.sequence > 0 and event.sequence != assigned_sequence:
            raise DomainError(
                WF_VALIDATION_FAILED,
                f"事件序号不连续：期望 {assigned_sequence}，实际 {event.sequence}",
            )

        await conn.execute(
            """
            INSERT INTO execution_event
                (tenant_id, event_id, instance_id, event_type, node_id, sequence,
                 payload, voided, occurred_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, NOW())
            """,
            resolved_tenant, event.event_id, event.instance_id,
            event.event_type.value, event.node_id,
            assigned_sequence,
            event.model_dump_json(),
        )
        return assigned_sequence

    async def list_for_instance(
        self,
        instance_id: str,
        include_voided: bool = True,
        tenant_id: Optional[str] = None,
    ) -> List[ExecutionEvent]:
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            if include_voided:
                rows = await conn.fetch(
                    "SELECT * FROM execution_event "
                    "WHERE instance_id=$1 AND tenant_id=$2 ORDER BY sequence",
                    instance_id, resolved_tenant,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM execution_event "
                    "WHERE instance_id=$1 AND tenant_id=$2 AND voided=FALSE ORDER BY sequence",
                    instance_id, resolved_tenant,
                )
        return [self._row_to_event(r) for r in rows]

    async def void_after(
        self,
        instance_id: str,
        sequence: int,
        tenant_id: Optional[str] = None,
    ) -> int:
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            result = await conn.execute(
                "UPDATE execution_event SET voided=TRUE "
                "WHERE instance_id=$1 AND sequence>$2 AND voided=FALSE AND tenant_id=$3",
                instance_id, sequence, resolved_tenant,
            )
        # asyncpg execute 返回 "UPDATE N" 格式
        return int(result.split()[-1]) if result else 0

    def _row_to_event(self, row) -> ExecutionEvent:
        from orchestration_platform.domain.models.enums import EventType
        return ExecutionEvent(
            event_id=row["event_id"], instance_id=row["instance_id"],
            event_type=EventType(row["event_type"]), node_id=row["node_id"],
            sequence=row["sequence"],
            payload={}, voided=row["voided"], occurred_at=row["occurred_at"],
        )
