# =============================================================================
# MCP 工具市场（mcp_tool_market/tool_market.py）
#
# 职责：工具市场注册中心，维护已注册工具的元数据声明、版本管理与权限标注。
# 设计意图（对应 spec 5.3.1.9~5.3.1.11、任务 T34.2、T34.5）：
#   1. 注册时校验元数据完整性；
#   2. 冲突时（同一标识与版本已存在且元数据不一致）拒绝；
#   3. 支持按名称、类别、权限等条件查询工具清单。
# =============================================================================

from typing import List, Optional

from orchestration_platform.domain.errors import MCP_TOOL_REGISTRATION_CONFLICT, DomainError
from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.mcp_tool_market.models import (
    CallerIdentity,
    McpToolDescriptor,
    McpToolRegistration,
    RegistrationResult,
)
from orchestration_platform.mcp_tool_market.persistence.abstractions.mcp_tool_market_repository import (
    IMcpToolMarketRepository,
)


class McpToolMarket:
    """MCP 工具市场注册中心。

    管理工具注册、版本、权限与发现查询。
    """

    def __init__(self, repository: IMcpToolMarketRepository) -> None:
        """初始化工具市场。

        Args:
            repository: 持久化仓储。
        """
        self._repo = repository

    async def register(
        self,
        descriptor: McpToolDescriptor,
        registered_by: str,
    ) -> RegistrationResult:
        """注册工具至市场。

        Args:
            descriptor: 工具描述符。
            registered_by: 注册者标识。

        Returns:
            注册结果。

        Raises:
            DomainError: 元数据不完整或版本冲突时抛出。
        """
        # 校验元数据完整性
        self._validate_descriptor(descriptor)

        # 检查冲突
        exists = await self._repo.exists(descriptor.tool_identifier, descriptor.version)
        if exists:
            existing = await self._repo.get(descriptor.tool_identifier, descriptor.version)
            if existing and existing.descriptor.model_dump() != descriptor.model_dump():
                return RegistrationResult(
                    success=False,
                    tool_identifier=descriptor.tool_identifier,
                    version=descriptor.version,
                    conflict_reason="同一标识与版本已存在且元数据不一致",
                )

        # 查找前一版本
        versions = await self._repo.list_versions(descriptor.tool_identifier)
        previous_version = versions[-1].descriptor.version if versions else None

        registration = McpToolRegistration(
            descriptor=descriptor,
            registered_by=registered_by,
            previous_version=previous_version,
        )
        await self._repo.save(registration)

        return RegistrationResult(
            success=True,
            tool_identifier=descriptor.tool_identifier,
            version=descriptor.version,
        )

    async def get_tool(
        self,
        tool_identifier: str,
        version: str,
    ) -> Optional[McpToolRegistration]:
        """按标识与版本查询工具。"""
        return await self._repo.get(tool_identifier, version)

    async def list_by_category(self, category: str) -> List[McpToolRegistration]:
        """按类别查询工具清单。"""
        return await self._repo.list_by_category(category)

    async def list_by_permission(
        self,
        permission: ToolPermission,
    ) -> List[McpToolRegistration]:
        """按权限查询工具清单。"""
        return await self._repo.list_by_permission(permission)

    async def list_versions(self, tool_identifier: str) -> List[McpToolRegistration]:
        """列出工具的所有版本。"""
        return await self._repo.list_versions(tool_identifier)

    async def search(
        self,
        name: Optional[str] = None,
        category: Optional[str] = None,
        permission: Optional[ToolPermission] = None,
    ) -> List[McpToolRegistration]:
        """综合搜索工具清单。"""
        if category:
            tools = await self._repo.list_by_category(category)
        elif permission:
            tools = await self._repo.list_by_permission(permission)
        else:
            return []

        if name:
            tools = [t for t in tools if name.lower() in t.descriptor.name.lower()]
        return tools

    @staticmethod
    def _validate_descriptor(descriptor: McpToolDescriptor) -> None:
        """校验工具描述符元数据完整性。"""
        if not descriptor.tool_identifier:
            raise DomainError(MCP_TOOL_REGISTRATION_CONFLICT, "工具标识不能为空")
        if not descriptor.name:
            raise DomainError(MCP_TOOL_REGISTRATION_CONFLICT, "工具名称不能为空")
        if not descriptor.version:
            raise DomainError(MCP_TOOL_REGISTRATION_CONFLICT, "工具版本不能为空")
