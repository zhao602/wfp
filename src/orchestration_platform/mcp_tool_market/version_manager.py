# =============================================================================
# MCP 工具版本管理（mcp_tool_market/version_manager.py）
#
# 职责：管理同一工具标识的多版本共存。
# 设计意图（对应 spec 5.3.1.10、任务 T34.3）：
#   1. 同一工具标识支持多版本共存；
#   2. 查询工具清单返回全部版本；
#   3. 可按版本调用指定版本工具。
# =============================================================================

from typing import List, Optional

from orchestration_platform.mcp_tool_market.models import McpToolRegistration
from orchestration_platform.mcp_tool_market.persistence.abstractions.mcp_tool_market_repository import (
    IMcpToolMarketRepository,
)


class VersionManager:
    """MCP 工具版本管理器。"""

    def __init__(self, repository: IMcpToolMarketRepository) -> None:
        """初始化版本管理器。

        Args:
            repository: 持久化仓储。
        """
        self._repo = repository

    async def list_versions(self, tool_identifier: str) -> List[McpToolRegistration]:
        """列出工具的所有版本（按版本排序）。"""
        return await self._repo.list_versions(tool_identifier)

    async def get_latest_version(
        self,
        tool_identifier: str,
    ) -> Optional[McpToolRegistration]:
        """获取工具的最新版本。"""
        versions = await self._repo.list_versions(tool_identifier)
        if not versions:
            return None
        # 按版本号排序取最新
        sorted_versions = sorted(
            versions,
            key=lambda r: r.descriptor.version,
            reverse=True,
        )
        return sorted_versions[0]

    async def get_specific_version(
        self,
        tool_identifier: str,
        version: str,
    ) -> Optional[McpToolRegistration]:
        """获取工具的指定版本。"""
        return await self._repo.get(tool_identifier, version)

    async def check_version_exists(
        self,
        tool_identifier: str,
        version: str,
    ) -> bool:
        """检查指定版本是否存在。"""
        return await self._repo.exists(tool_identifier, version)
