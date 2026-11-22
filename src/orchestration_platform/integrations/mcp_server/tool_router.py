# =============================================================================
# MCP 工具调用路由（integrations/mcp_server/tool_router.py）
#
# 职责：将 MCP 工具调用路由到对应应用服务方法。
# 设计意图（对应 spec 5.3.1.2、任务 T32.2）：
#   1. 工具不存在返回 MCP_TOOL_NOT_FOUND；
#   2. 路由表与具体服务实现解耦，通过注入的服务字典调用。
# =============================================================================

from typing import Any, Callable, Dict

from orchestration_platform.domain.errors import MCP_TOOL_NOT_FOUND, DomainError


class ToolRouter:
    """MCP 工具调用路由器。

    通过注入的服务方法字典将工具调用路由到对应应用服务。
    """

    def __init__(self, service_methods: Dict[str, Callable]) -> None:
        """初始化路由器。

        Args:
            service_methods: 工具标识 -> 异步服务方法 的映射。
                方法签名: async def method(arguments: dict) -> dict
        """
        self._routes: Dict[str, Callable] = service_methods

    async def route(self, tool_identifier: str, arguments: Dict[str, Any]) -> Any:
        """路由工具调用到对应服务方法。

        Args:
            tool_identifier: 工具标识。
            arguments: 调用参数。

        Returns:
            服务方法返回值。

        Raises:
            DomainError: 工具不存在时抛出 MCP_TOOL_NOT_FOUND。
        """
        handler = self._routes.get(tool_identifier)
        if handler is None:
            raise DomainError(MCP_TOOL_NOT_FOUND, f"MCP 工具不存在: {tool_identifier}")
        return await handler(arguments)

    def has_tool(self, tool_identifier: str) -> bool:
        """检查工具是否已注册路由。"""
        return tool_identifier in self._routes
