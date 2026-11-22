# =============================================================================
# 租户作用域 PostgreSQL 仓储基类（persistence/postgres/tenant_scoped_base.py）
#
# 职责：为 asyncpg 仓储统一提供连接池管理与租户上下文注入（RLS 会话变量）。
# 设计意图（roadmap B1，roadmap §7 多租户强隔离）：
#   1. 双保险架构：仓储 SQL 的 WHERE tenant_id 过滤为第一道保险，本基类向
#      连接注入 app.current_tenant 会话变量（v1_7 RLS 策略谓词）为第二道保险；
#   2. fail-fast：租户上下文缺失（无参数且默认租户未配置）抛 TenantContextError；
#   3. SET LOCAL 不支持参数绑定，必须用 SELECT set_config(name, $1, true)
#      三参形式（事务级，随事务结束自动失效，连接归还连接池零残留）；
#   4. _admin_connection 旁路通道（app.bypass_rls='on'）：供预租户上下文认证
#      入口（token get_by_hash 等）与跨租户管理操作使用，安全边界由应用层控制；
#   5. 对外构造签名 pool=None 不变（B0 冒烟脚本 pool 注入兼容）。
# =============================================================================

from contextlib import asynccontextmanager
from typing import Optional

from orchestration_platform.configuration.settings import get_settings


class TenantContextError(RuntimeError):
    """租户上下文缺失或非法（roadmap B1 fail-fast）。"""


class TenantScopedPostgresRepository:
    """租户作用域仓储基类——连接池管理 + RLS 会话变量注入。"""

    def __init__(self, pool=None) -> None:
        """构造仓储实例。

        参数：
            pool —— asyncpg 连接池；缺省在首次调用时从配置创建。
        """
        self._pool = pool

    async def _get_pool(self):
        """获取或创建连接池（延迟导入 asyncpg）。"""
        if self._pool is not None:
            return self._pool
        import asyncpg
        settings = get_settings()
        self._pool = await asyncpg.create_pool(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
        )
        return self._pool

    def _resolve_default_tenant(self) -> str:
        """解析默认租户标识（settings.default_tenant_id）；缺失时 fail-fast。"""
        settings = get_settings()
        default_tenant = getattr(settings, "default_tenant_id", None)
        if not default_tenant:
            raise TenantContextError(
                "默认租户未配置（settings.default_tenant_id 为空）"
            )
        return default_tenant

    def _resolve_tenant(self, tenant_id: Optional[str]) -> str:
        """解析租户：显式参数优先，缺省回退默认租户；解析后强制非空。"""
        resolved = tenant_id if tenant_id else self._resolve_default_tenant()
        if not resolved:
            raise TenantContextError("租户上下文为空（tenant_id 未解析）")
        return resolved

    @asynccontextmanager
    async def _tenant_connection(self, tenant_id: Optional[str] = None):
        """获取注入租户上下文的连接（事务级 app.current_tenant）。"""
        resolved = self._resolve_tenant(tenant_id)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, true)",
                    resolved,
                )
                yield conn

    @asynccontextmanager
    async def _admin_connection(self):
        """获取旁路 RLS 的管理连接（事务级 app.bypass_rls='on'）。"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.bypass_rls', 'on', true)"
                )
                yield conn

    @staticmethod
    async def _ensure_session_tenant(conn, tenant_id: str) -> None:
        """外部连接路径：仅当会话未设置租户变量时补设（不覆盖调用方上下文）。

        供 append_in_transaction 等接收外部连接（事务内）的方法使用——
        调用方已注入租户上下文则保持原值，未注入则以给定租户补设。
        注意：set_config 第三参 true 为事务级，要求连接处于打开的事务内。
        """
        row = await conn.fetchrow(
            "SELECT current_setting('app.current_tenant', true) AS current_tenant"
        )
        current = row["current_tenant"] if row is not None else None
        if not current:
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, true)",
                tenant_id,
            )
