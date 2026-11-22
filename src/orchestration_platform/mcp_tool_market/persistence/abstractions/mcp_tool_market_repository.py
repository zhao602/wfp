# =============================================================================
# MCP 工具市场持久化抽象接口（mcp_tool_market/persistence/abstractions/mcp_tool_market_repository.py）
#
# 职责：定义工具市场持久化仓储接口。
# 设计意图（对应 spec 5.3.1.9、任务 T34.1）：
#   PostgreSQL 与本地文件两套实现共享此接口。
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.mcp_tool_market.models import McpToolRegistration


@runtime_checkable
class IMcpToolMarketRepository(Protocol):
    """MCP 工具市场持久化仓储接口。"""

    async def save(self, registration: McpToolRegistration) -> None:
        """保存工具注册记录。"""
        ...

    async def get(self, tool_identifier: str, version: str) -> Optional[McpToolRegistration]:
        """按标识与版本获取工具注册记录。"""
        ...

    async def list_by_category(self, category: str) -> List[McpToolRegistration]:
        """按类别列出工具。"""
        ...

    async def list_by_permission(self, permission: ToolPermission) -> List[McpToolRegistration]:
        """按权限列出工具。"""
        ...

    async def list_versions(self, tool_identifier: str) -> List[McpToolRegistration]:
        """列出工具的所有版本。"""
        ...

    async def exists(self, tool_identifier: str, version: str) -> bool:
        """检查工具版本是否已存在。"""
        ...
