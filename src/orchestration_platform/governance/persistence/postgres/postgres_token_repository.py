# TODO(tech-debt): B档候选孤儿（族群 B2），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# PostgreSQL 令牌仓储（governance/persistence/postgres/postgres_token_repository.py）
#
# 职责：以 PostgreSQL 持久化访问令牌。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.2）：
#   1. 令牌哈希存储，不保存原文；
#   2. 全部查询注入 tenant_id 过滤条件保证隔离（第一道保险）；
#   3. 仓储基类向连接注入 app.current_tenant 会话变量（RLS 第二道保险，
#      roadmap B1，见 persistence/postgres/tenant_scoped_base.py）；
#   4. 预租户上下文认证入口（get_by_hash / is_revoked / revoke_token /
#      update_last_used 仅持哈希或令牌标识，无租户归属）走基类
#      _admin_connection 旁路通道；
#   5. 批量撤销通过 UPDATE WHERE 实现。
# =============================================================================

from datetime import datetime, timezone
from typing import List, Optional

from orchestration_platform.domain.models.enums import TokenStatus
from orchestration_platform.governance.tenant.models import AccessToken
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PostgresTokenRepository(TenantScopedPostgresRepository):
    """令牌仓储——PostgreSQL 实现。"""

    async def save_token(self, token: AccessToken) -> AccessToken:
        """保存令牌记录（幂等：同标识覆盖）。"""
        async with self._tenant_connection(token.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO governance_access_token
                    (token_id, user_id, tenant_id, token_hash, status,
                     issued_at, expires_at, revoked_at, last_used_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (token_id) DO UPDATE
                SET token_hash = $4, status = $5, expires_at = $7,
                    revoked_at = $8, last_used_at = $9
                """,
                token.token_id, token.user_id, token.tenant_id,
                token.token_hash, token.status.value,
                token.issued_at, token.expires_at,
                token.revoked_at, token.last_used_at,
            )
        return token

    async def get_token(
        self, tenant_id: str, token_id: str
    ) -> Optional[AccessToken]:
        """按标识查询令牌。"""
        async with self._tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT token_id, user_id, tenant_id, token_hash, status, "
                "issued_at, expires_at, revoked_at, last_used_at "
                "FROM governance_access_token WHERE tenant_id = $1 AND token_id = $2",
                tenant_id, token_id,
            )
        if row is None:
            return None
        return self._row_to_token(row)

    async def get_by_hash(
        self, token_hash: str
    ) -> Optional[AccessToken]:
        """按令牌哈希查询令牌（预租户上下文认证入口，走旁路通道）。"""
        async with self._admin_connection() as conn:
            row = await conn.fetchrow(
                "SELECT token_id, user_id, tenant_id, token_hash, status, "
                "issued_at, expires_at, revoked_at, last_used_at "
                "FROM governance_access_token WHERE token_hash = $1",
                token_hash,
            )
        if row is None:
            return None
        return self._row_to_token(row)

    async def is_revoked(self, token_id: str) -> bool:
        """判断令牌是否已吊销（预租户上下文认证入口，走旁路通道）。"""
        async with self._admin_connection() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM governance_access_token WHERE token_id = $1",
                token_id,
            )
        if row is None:
            return True
        return row["status"] == TokenStatus.REVOKED.value

    async def revoke_token(self, token_id: str) -> None:
        """吊销令牌（预租户上下文认证入口，走旁路通道）。"""
        async with self._admin_connection() as conn:
            await conn.execute(
                "UPDATE governance_access_token SET status = 'revoked', revoked_at = $2 "
                "WHERE token_id = $1",
                token_id, _utc_now(),
            )

    async def revoke_for_user(
        self, tenant_id: str, user_id: str
    ) -> int:
        """撤销用户的所有活跃令牌，返回撤销数量。"""
        async with self._tenant_connection(tenant_id) as conn:
            result = await conn.execute(
                "UPDATE governance_access_token SET status = 'revoked', revoked_at = $3 "
                "WHERE tenant_id = $1 AND user_id = $2 AND status = 'active'",
                tenant_id, user_id, _utc_now(),
            )
        # asyncpg 返回 "UPDATE N" 格式
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def list_tokens_for_user(
        self, tenant_id: str, user_id: str
    ) -> List[AccessToken]:
        """列出用户的全部令牌。"""
        async with self._tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT token_id, user_id, tenant_id, token_hash, status, "
                "issued_at, expires_at, revoked_at, last_used_at "
                "FROM governance_access_token WHERE tenant_id = $1 AND user_id = $2 "
                "ORDER BY issued_at DESC",
                tenant_id, user_id,
            )
        return [self._row_to_token(r) for r in rows]

    async def update_last_used(
        self, token_id: str
    ) -> None:
        """更新令牌最近使用时间（预租户上下文认证入口，走旁路通道）。"""
        async with self._admin_connection() as conn:
            await conn.execute(
                "UPDATE governance_access_token SET last_used_at = $2 WHERE token_id = $1",
                token_id, _utc_now(),
            )

    @staticmethod
    def _row_to_token(row) -> AccessToken:
        """将数据库行映射为 AccessToken 模型。"""
        return AccessToken(
            token_id=row["token_id"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            token_hash=row["token_hash"],
            status=TokenStatus(row["status"]),
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            last_used_at=row["last_used_at"],
        )
