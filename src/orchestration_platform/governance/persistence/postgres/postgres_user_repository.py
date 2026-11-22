# 销项（2026-11-18，arch_review_v2_tasks G3/CHG-06/CHG-14）：原 B2 族成员，已由 RBAC 落库接线为生产调用点（role 族注入 RbacPermissionChecker；user/tenant 族经 rbac/bootstrap 幂等初始化），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# PostgreSQL 用户仓储（governance/persistence/postgres/postgres_user_repository.py）
#
# 职责：以 PostgreSQL 持久化用户。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.2）：
#   1. 全部查询注入 tenant_id 过滤条件保证隔离（第一道保险）；
#   2. 仓储基类向连接注入 app.current_tenant 会话变量（RLS 第二道保险，
#      roadmap B1，见 persistence/postgres/tenant_scoped_base.py）；
#   3. 禁用用户时令牌批量失效通过触发器或应用层联动实现。
# =============================================================================

from typing import List, Optional

from orchestration_platform.domain.models.enums import UserStatus
from orchestration_platform.governance.tenant.models import User
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


class PostgresUserRepository(TenantScopedPostgresRepository):
    """用户仓储——PostgreSQL 实现。"""

    async def create_user(self, user: User) -> User:
        """创建用户（幂等：同标识覆盖）。"""
        async with self._tenant_connection(user.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO governance_user
                    (user_id, tenant_id, username, display_name, email,
                     org_node_id, status, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (tenant_id, user_id) DO UPDATE
                SET username = $3, display_name = $4, email = $5,
                    org_node_id = $6, status = $7, updated_at = $9
                """,
                user.user_id, user.tenant_id, user.username,
                user.display_name, user.email, user.org_node_id,
                user.status.value, user.created_at, user.updated_at,
            )
        return user

    async def get_user(
        self, tenant_id: str, user_id: str
    ) -> Optional[User]:
        """按标识查询用户。"""
        async with self._tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT user_id, tenant_id, username, display_name, email, "
                "org_node_id, status, created_at, updated_at "
                "FROM governance_user WHERE tenant_id = $1 AND user_id = $2",
                tenant_id, user_id,
            )
        if row is None:
            return None
        return self._row_to_user(row)

    async def get_user_by_username(
        self, tenant_id: str, username: str
    ) -> Optional[User]:
        """按用户名查询用户。"""
        async with self._tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT user_id, tenant_id, username, display_name, email, "
                "org_node_id, status, created_at, updated_at "
                "FROM governance_user WHERE tenant_id = $1 AND username = $2",
                tenant_id, username,
            )
        if row is None:
            return None
        return self._row_to_user(row)

    async def update_user(self, user: User) -> User:
        """更新用户信息。"""
        async with self._tenant_connection(user.tenant_id) as conn:
            await conn.execute(
                "UPDATE governance_user SET username = $3, display_name = $4, email = $5, "
                "org_node_id = $6, status = $7, updated_at = $8 "
                "WHERE tenant_id = $1 AND user_id = $2",
                user.tenant_id, user.user_id, user.username,
                user.display_name, user.email, user.org_node_id,
                user.status.value, user.updated_at,
            )
        return user

    async def disable_user(self, tenant_id: str, user_id: str) -> None:
        """禁用用户。"""
        async with self._tenant_connection(tenant_id) as conn:
            await conn.execute(
                "UPDATE governance_user SET status = 'disabled', updated_at = NOW() "
                "WHERE tenant_id = $1 AND user_id = $2",
                tenant_id, user_id,
            )

    async def list_users(
        self,
        tenant_id: str,
        org_node_id: Optional[str] = None,
    ) -> List[User]:
        """列出租户下全部用户，可按组织节点过滤。"""
        async with self._tenant_connection(tenant_id) as conn:
            if org_node_id is None:
                rows = await conn.fetch(
                    "SELECT user_id, tenant_id, username, display_name, email, "
                    "org_node_id, status, created_at, updated_at "
                    "FROM governance_user WHERE tenant_id = $1 ORDER BY created_at",
                    tenant_id,
                )
            else:
                rows = await conn.fetch(
                    "SELECT user_id, tenant_id, username, display_name, email, "
                    "org_node_id, status, created_at, updated_at "
                    "FROM governance_user WHERE tenant_id = $1 AND org_node_id = $2 "
                    "ORDER BY created_at",
                    tenant_id, org_node_id,
                )
        return [self._row_to_user(r) for r in rows]

    @staticmethod
    def _row_to_user(row) -> User:
        """将数据库行映射为 User 模型。"""
        return User(
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            username=row["username"],
            display_name=row["display_name"],
            email=row["email"],
            org_node_id=row["org_node_id"],
            status=UserStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
