# =============================================================================
# MCP 服务端（integrations/mcp_server/mcp_server.py）
#
# 职责：将平台能力暴露为 MCP 工具，供外部 Agent 通过 MCP 协议调用。
# 设计意图（对应 spec 5.3.1.1~5.3.1.4、任务 T32.5）：
#   1. list_tools 返回平台能力工具清单；
#   2. call_tool 路由到应用服务并返回 MCP 标准响应；
#   3. start/stop 管理生命周期。
# =============================================================================

from typing import Any, Dict, List

from orchestration_platform.integrations.mcp_server.authentication import McpAuthenticator
from orchestration_platform.integrations.mcp_server.error_mapping import map_to_mcp_error
from orchestration_platform.integrations.mcp_server.tool_declarations import (
    get_platform_tool_declarations,
    get_tool_by_identifier,
)
from orchestration_platform.integrations.mcp_server.tool_router import ToolRouter
from orchestration_platform.mcp_tool_market.models import McpToolDescriptor


class McpServer:
    """MCP 服务端：将平台能力暴露为 MCP 工具。

    生命周期：
        start() → list_tools() / call_tool() → stop()
    """

    def __init__(
        self,
        tool_router: ToolRouter,
        authenticator: McpAuthenticator | None = None,
    ) -> None:
        """初始化 MCP 服务端。

        Args:
            tool_router: 工具调用路由器。
            authenticator: 鉴权器（None 表示不鉴权）。
        """
        self._router = tool_router
        self._authenticator = authenticator
        self._running = False

    async def start(self) -> None:
        """启动 MCP 服务端。"""
        self._running = True

    async def stop(self) -> None:
        """停止 MCP 服务端。

        停止后正在处理的调用会被优雅拒绝。
        """
        self._running = False

    @property
    def is_running(self) -> bool:
        """服务端是否运行中。"""
        return self._running

    def list_tools(self) -> List[McpToolDescriptor]:
        """返回平台能力工具清单。"""
        return get_platform_tool_declarations()

    async def call_tool(
        self,
        tool_identifier: str,
        arguments: Dict[str, Any],
        token: str | None = None,
    ) -> Dict[str, Any]:
        """调用 MCP 工具。

        Args:
            tool_identifier: 工具标识。
            arguments: 调用参数。
            token: 访问令牌（可选）。

        Returns:
            MCP 标准响应：成功时 {result: ...}，失败时 {error_code, error_message}。
        """
        if not self._running:
            return {"error_code": "MCP_SERVER_NOT_RUNNING", "error_message": "服务端未启动"}

        try:
            # 鉴权
            if self._authenticator and token:
                identity = self._authenticator.authenticate(token)
                tool = get_tool_by_identifier(tool_identifier)
                if tool:
                    self._authenticator.check_permission(identity, tool.permission)

            # 路由调用
            result = await self._router.route(tool_identifier, arguments)
            return {"result": result}
        except Exception as exc:
            return map_to_mcp_error(exc)
