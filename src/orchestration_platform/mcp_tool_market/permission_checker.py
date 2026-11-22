# =============================================================================
# MCP 工具权限校验（mcp_tool_market/permission_checker.py）
#
# 职责：校验调用方是否具备工具所需权限。
# 设计意图（对应 spec 5.3.1.11、任务 T34.4）：
#   1. 每个工具声明权限要求（read_only / read_write / admin）；
#   2. 调用方权限不足时拒绝返回 MCP_TOOL_PERMISSION_DENIED 并附所需权限。
# =============================================================================

from typing import List

from orchestration_platform.domain.errors import MCP_TOOL_PERMISSION_DENIED, DomainError
from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.mcp_tool_market.models import CallerIdentity, McpToolDescriptor


class PermissionChecker:
    """MCP 工具权限校验器。"""

    # 权限层级：admin > read_write > read_only
    _PERMISSION_LEVEL = {
        ToolPermission.READONLY: 1,
        ToolPermission.READWRITE: 2,
        ToolPermission.ADMIN: 3,
    }

    def check(self, identity: CallerIdentity, tool: McpToolDescriptor) -> None:
        """校验调用方是否具备工具所需权限。

        Args:
            identity: 调用方身份。
            tool: 工具描述符。

        Raises:
            DomainError: 权限不足时抛出 MCP_TOOL_PERMISSION_DENIED。
        """
        required_level = self._PERMISSION_LEVEL.get(tool.permission, 0)
        caller_max_level = max(
            (self._PERMISSION_LEVEL.get(p, 0) for p in identity.permissions),
            default=0,
        )

        if caller_max_level < required_level:
            raise DomainError(
                MCP_TOOL_PERMISSION_DENIED,
                f"权限不足: 需要 {tool.permission.value}",
            )

    def check_batch(
        self,
        identity: CallerIdentity,
        tools: List[McpToolDescriptor],
    ) -> List[McpToolDescriptor]:
        """批量校验权限，返回调用方有权限调用的工具列表。"""
        accessible = []
        for tool in tools:
            try:
                self.check(identity, tool)
                accessible.append(tool)
            except DomainError:
                continue
        return accessible
