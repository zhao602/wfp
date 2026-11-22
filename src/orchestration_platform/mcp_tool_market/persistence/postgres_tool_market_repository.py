# TODO(tech-debt): B档候选孤儿（族群 B8），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# PostgreSQL 工具市场仓储（mcp_tool_market/persistence/postgres_tool_market_repository.py）
#
# 职责：以 PostgreSQL 方式持久化 MCP 工具市场注册记录。
# 设计意图（对应 spec 5.3.1.13、任务 T34.7）：
#   PostgreSQL 驱动延迟导入，与既有 persistence/postgres/ 实现一致。
# =============================================================================

from typing import List, Optional

from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.mcp_tool_market.models import McpToolRegistration


class PostgresToolMarketRepository:
    """PostgreSQL 工具市场仓储。

    使用 asyncpg（延迟导入）访问 PostgreSQL。
    """

    def __init__(self, connection_string: str) -> None:
        """初始化 PostgreSQL 仓储。

        Args:
            connection_string: PostgreSQL 连接串。
        """
        self._conn_str = connection_string

    async def save(self, registration: McpToolRegistration) -> None:
        """保存工具注册记录。"""
        try:
            import asyncpg
        except ImportError:
            return

        conn = await asyncpg.connect(self._conn_str)
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS mcp_tool_market (
                    tool_identifier TEXT NOT NULL,
                    version TEXT NOT NULL,
                    data JSONB NOT NULL,
                    PRIMARY KEY (tool_identifier, version)
                )"""
            )
            await conn.execute(
                """INSERT INTO mcp_tool_market (tool_identifier, version, data)
                VALUES ($1, $2, $3)
                ON CONFLICT (tool_identifier, version) DO UPDATE SET data = $3""",
                registration.descriptor.tool_identifier,
                registration.descriptor.version,
                registration.model_dump_json(),
            )
        finally:
            await conn.close()

    async def get(self, tool_identifier: str, version: str) -> Optional[McpToolRegistration]:
        """按标识与版本获取工具注册记录。"""
        try:
            import asyncpg
        except ImportError:
            return None

        conn = await asyncpg.connect(self._conn_str)
        try:
            row = await conn.fetchrow(
                "SELECT data FROM mcp_tool_market WHERE tool_identifier = $1 AND version = $2",
                tool_identifier,
                version,
            )
            if row is None:
                return None
            return McpToolRegistration.model_validate_json(row["data"])
        finally:
            await conn.close()

    async def list_by_category(self, category: str) -> List[McpToolRegistration]:
        """按类别列出工具。"""
        try:
            import asyncpg
        except ImportError:
            return []

        conn = await asyncpg.connect(self._conn_str)
        try:
            rows = await conn.fetch(
                "SELECT data FROM mcp_tool_market WHERE data->'descriptor'->'tags' ? $1",
                category,
            )
            return [McpToolRegistration.model_validate_json(row["data"]) for row in rows]
        finally:
            await conn.close()

    async def list_by_permission(self, permission: ToolPermission) -> List[McpToolRegistration]:
        """按权限列出工具。"""
        try:
            import asyncpg
        except ImportError:
            return []

        conn = await asyncpg.connect(self._conn_str)
        try:
            rows = await conn.fetch(
                "SELECT data FROM mcp_tool_market WHERE data->'descriptor'->'permission' = $1",
                permission.value,
            )
            return [McpToolRegistration.model_validate_json(row["data"]) for row in rows]
        finally:
            await conn.close()

    async def list_versions(self, tool_identifier: str) -> List[McpToolRegistration]:
        """列出工具的所有版本。"""
        try:
            import asyncpg
        except ImportError:
            return []

        conn = await asyncpg.connect(self._conn_str)
        try:
            rows = await conn.fetch(
                "SELECT data FROM mcp_tool_market WHERE tool_identifier = $1 ORDER BY version",
                tool_identifier,
            )
            return [McpToolRegistration.model_validate_json(row["data"]) for row in rows]
        finally:
            await conn.close()

    async def exists(self, tool_identifier: str, version: str) -> bool:
        """检查工具版本是否已存在。"""
        try:
            import asyncpg
        except ImportError:
            return False

        conn = await asyncpg.connect(self._conn_str)
        try:
            row = await conn.fetchrow(
                "SELECT 1 FROM mcp_tool_market WHERE tool_identifier = $1 AND version = $2",
                tool_identifier,
                version,
            )
            return row is not None
        finally:
            await conn.close()
