# 销项（2026-11-18，arch_review_v2_tasks G3/CHG-06/CHG-14）：原 B2 族成员，已由 RBAC 落库接线为生产调用点（role 族注入 RbacPermissionChecker；user/tenant 族经 rbac/bootstrap 幂等初始化），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# PostgreSQL 租户仓储（governance/persistence/postgres/postgres_tenant_repository.py）
#
# 职责：以 PostgreSQL 持久化租户与租户配额。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.2）：
#   1. 延迟导入 asyncpg 驱动，与既有 persistence/postgres/ 实现一致；
#   2. 全部查询注入 tenant_id 过滤条件保证隔离（第一道保险）；
#   3. 仓储基类向连接注入 app.current_tenant 会话变量（RLS 第二道保险，
#      roadmap B1，见 persistence/postgres/tenant_scoped_base.py）；
#   4. list_tenants 为跨租户管理读，走基类 _admin_connection 旁路通道
#      （RLS 策略 governance_tenant 行按自身 tenant_id 隔离）；
#   5. INSERT ON CONFLICT 实现幂等保存。
# =============================================================================

from typing import List, Optional

from orchestration_platform.governance.tenant.models import Tenant, TenantQuota
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


class PostgresTenantRepository(TenantScopedPostgresRepository):
    """租户仓储——PostgreSQL 实现。"""

    async def create_tenant(self, tenant: Tenant) -> Tenant:
        """创建租户（幂等：同标识覆盖）。"""
        async with self._tenant_connection(tenant.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO governance_tenant
                    (tenant_id, tenant_name, status, is_default, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (tenant_id) DO UPDATE
                SET tenant_name = $2, status = $3, updated_at = $6
                """,
                tenant.tenant_id,
                tenant.tenant_name,
                tenant.status.value,
                tenant.is_default,
                tenant.created_at,
                tenant.updated_at,
            )
        return tenant

    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """按标识查询租户。"""
        async with self._tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT tenant_id, tenant_name, status, is_default, created_at, updated_at "
                "FROM governance_tenant WHERE tenant_id = $1",
                tenant_id,
            )
        if row is None:
            return None
        return self._row_to_tenant(row)

    async def list_tenants(self) -> List[Tenant]:
        """列出全部租户（跨租户管理读，走旁路通道）。"""
        async with self._admin_connection() as conn:
            rows = await conn.fetch(
                "SELECT tenant_id, tenant_name, status, is_default, created_at, updated_at "
                "FROM governance_tenant ORDER BY created_at"
            )
        return [self._row_to_tenant(r) for r in rows]

    async def update_tenant(self, tenant: Tenant) -> Tenant:
        """更新租户信息。"""
        async with self._tenant_connection(tenant.tenant_id) as conn:
            await conn.execute(
                "UPDATE governance_tenant SET tenant_name = $2, status = $3, updated_at = $4 "
                "WHERE tenant_id = $1",
                tenant.tenant_id,
                tenant.tenant_name,
                tenant.status.value,
                tenant.updated_at,
            )
        return tenant

    async def disable_tenant(self, tenant_id: str) -> None:
        """禁用租户。"""
        async with self._tenant_connection(tenant_id) as conn:
            await conn.execute(
                "UPDATE governance_tenant SET status = 'disabled', updated_at = NOW() "
                "WHERE tenant_id = $1",
                tenant_id,
            )

    async def get_quota(self, tenant_id: str) -> Optional[TenantQuota]:
        """查询租户配额。"""
        async with self._tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT tenant_id, max_instances, max_workflows, max_tokens_per_day, "
                "max_storage_gb, updated_at FROM governance_tenant_quota WHERE tenant_id = $1",
                tenant_id,
            )
        if row is None:
            return None
        return TenantQuota(
            tenant_id=row["tenant_id"],
            max_instances=row["max_instances"],
            max_workflows=row["max_workflows"],
            max_tokens_per_day=row["max_tokens_per_day"],
            max_storage_gb=row["max_storage_gb"],
            updated_at=row["updated_at"],
        )

    async def save_quota(self, quota: TenantQuota) -> TenantQuota:
        """保存或更新租户配额。"""
        async with self._tenant_connection(quota.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO governance_tenant_quota
                    (tenant_id, max_instances, max_workflows, max_tokens_per_day,
                     max_storage_gb, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (tenant_id) DO UPDATE
                SET max_instances = $2, max_workflows = $3, max_tokens_per_day = $4,
                    max_storage_gb = $5, updated_at = $6
                """,
                quota.tenant_id,
                quota.max_instances,
                quota.max_workflows,
                quota.max_tokens_per_day,
                quota.max_storage_gb,
                quota.updated_at,
            )
        return quota

    @staticmethod
    def _row_to_tenant(row) -> Tenant:
        """将数据库行映射为 Tenant 模型。"""
        from orchestration_platform.domain.models.enums import TenantStatus
        return Tenant(
            tenant_id=row["tenant_id"],
            tenant_name=row["tenant_name"],
            status=TenantStatus(row["status"]),
            is_default=row["is_default"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
