# =============================================================================
# MCP HTTP 工具调用器（integrations/tools/mcp_http_tool_invoker.py）
#
# 职责：按 MCP 工具市场 descriptor.endpoint 的薄 HTTP 适配调用（实验性）。
# 设计意图（对应 merge_tool_into_agent_node design 2.5.4 / DC-D / CHG-5）：
#   1. POST endpoint，body=arguments；
#   2. 超时/非 2xx → ToolResult 失败（失败走 FR-4 规则 5 回填，节点不因单次失败终止）；
#   3. httpx 延迟导入（invoke 方法内部），无 httpx 环境降级返回失败结果；
#   4. 所有异常封装为 ToolResult(success=False)，不向调用方抛出未封装异常；
#   5. 实验性：完整 MCP 传输层适配另立任务，本类仅覆盖最小 HTTP 调用路径。
# =============================================================================

from typing import Dict

from orchestration_platform.domain.errors import MCP_CONNECTION_FAILED, MCP_TOOL_TIMEOUT
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.integrations.abstractions.external_clients import ToolResult


class McpHttpToolInvoker:
    """MCP HTTP 工具调用器（实验性）。

    按市场 descriptor.endpoint 的薄 HTTP 适配：POST endpoint，body=arguments。
    超时/非 2xx/网络异常均封装为 ToolResult 失败（FR-4 规则 5 回填，
    Agent 节点不因单次工具调用失败终止）。
    """

    def __init__(self, tool_identifier: str, endpoint: str) -> None:
        """初始化调用器（不建立网络连接）。

        参数：
            tool_identifier —— 工具标识（全局唯一）
            endpoint        —— 市场 descriptor 声明的工具端点地址
        """
        self._tool_identifier = tool_identifier
        self._endpoint = endpoint

    @property
    def tool_identifier(self) -> str:
        return self._tool_identifier

    async def invoke(
        self, arguments: Dict[str, JsonValue], timeout_seconds: int
    ) -> ToolResult:
        """调用远端工具端点：POST {endpoint}，body=arguments。

        超时 → MCP_TOOL_TIMEOUT；网络异常/非 2xx → MCP_CONNECTION_FAILED；
        均为 ToolResult 失败，不抛出未封装异常。
        """
        try:
            import httpx
        except ImportError:
            return ToolResult(
                success=False,
                error_code=MCP_CONNECTION_FAILED,
                error_message="HTTP 客户端库不可用: httpx 导入失败",
            )

        try:
            async with httpx.AsyncClient(timeout=float(timeout_seconds)) as client:
                response = await client.post(self._endpoint, json=arguments)
        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                error_code=MCP_TOOL_TIMEOUT,
                error_message=f"工具 {self._tool_identifier} 调用超时（上限 {timeout_seconds}s）",
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                success=False,
                error_code=MCP_CONNECTION_FAILED,
                error_message=f"工具 {self._tool_identifier} 请求失败: {type(exc).__name__}",
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error_code=MCP_CONNECTION_FAILED,
                error_message=f"工具 {self._tool_identifier} 调用异常: {type(exc).__name__}",
            )

        if not (200 <= response.status_code < 300):
            return ToolResult(
                success=False,
                error_code=MCP_CONNECTION_FAILED,
                error_message=f"工具 {self._tool_identifier} 端点返回状态码 {response.status_code}",
            )

        try:
            output = response.json()
        except Exception:
            # 非 JSON 响应按原文回填（观察仍可被 LLM 消费）
            output = response.text
        return ToolResult(success=True, output=output)
