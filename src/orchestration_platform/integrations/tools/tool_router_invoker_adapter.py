# =============================================================================
# 平台工具路由适配器（integrations/tools/tool_router_invoker_adapter.py）
#
# 职责：将既有 ToolRouter（integrations/mcp_server/tool_router.py）包装为 IToolInvoker。
# 设计意图（对应 merge_tool_into_agent_node design 2.5.4 / DC-D / CHG-6）：
#   1. 平台工具（validate_workflow / get_workflow / get_instance 等服务方法）
#      经 ToolRouter 路由调用，包装后与本地/MCP 工具共用 Agent 执行器入口；
#   2. 路由缺失/服务异常封装为 ToolResult 失败（FR-4 规则 5 回填，节点不因单次失败终止）；
#   3. 薄适配：不缓存、不重试、不做权限校验（工具市场既有能力）。
# =============================================================================

import asyncio
from typing import Dict

from orchestration_platform.domain.errors import (
    NODE_TOOL_NOT_FOUND,
    NODE_TOOL_TIMEOUT,
    DomainError,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.integrations.abstractions.external_clients import ToolResult
from orchestration_platform.integrations.mcp_server.tool_router import ToolRouter


class ToolRouterInvokerAdapter:
    """平台工具 ToolRouter 适配器：将 ToolRouter 包装为 IToolInvoker。"""

    def __init__(self, router: ToolRouter, tool_identifier: str) -> None:
        """初始化适配器。

        参数：
            router          —— 既有 MCP 工具路由器（服务方法字典注入）
            tool_identifier —— 本适配器绑定的平台工具标识
        """
        self._router = router
        self._tool_identifier = tool_identifier

    @property
    def tool_identifier(self) -> str:
        return self._tool_identifier

    async def invoke(
        self, arguments: Dict[str, JsonValue], timeout_seconds: int
    ) -> ToolResult:
        """经 ToolRouter 路由调用平台服务方法。

        路由缺失/服务抛出 DomainError → 携原错误码的 ToolResult 失败；
        超时 → NODE_TOOL_TIMEOUT；其余异常 → NODE_TOOL_NOT_FOUND（与本地
        调用器同语义）。均为 ToolResult 失败，不抛出未封装异常。
        """
        try:
            output = await self._router.route(self._tool_identifier, arguments)
        except DomainError as exc:
            return ToolResult(
                success=False,
                error_code=exc.code,
                error_message=exc.message,
            )
        except (TimeoutError, asyncio.TimeoutError):
            # Python 3.10 中 asyncio.TimeoutError 与内建 TimeoutError 不同类，两者均捕获
            return ToolResult(
                success=False,
                error_code=NODE_TOOL_TIMEOUT,
                error_message=f"工具 {self._tool_identifier} 调用超时（上限 {timeout_seconds}s）",
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error_code=NODE_TOOL_NOT_FOUND,
                error_message=f"工具 {self._tool_identifier} 执行失败：{exc}",
            )
        return ToolResult(success=True, output=output)
