# =============================================================================
# 本地函数工具调用器（integrations/tools/local_function_tool_invoker.py）
#
# 职责：提供本地确定性函数工具，与 MCP 工具共用 ToolInvoker 入口。
# 设计意图（对应 spec 5.3.1.5-6、design 2.5.5）：
#   相同入参返回相同结果（无状态），不重试。
# =============================================================================

from typing import Any, Callable, Dict

from orchestration_platform.domain.errors import NODE_TOOL_ARGUMENT_MISMATCH, NODE_TOOL_NOT_FOUND
from orchestration_platform.integrations.abstractions.external_clients import ToolResult


class LocalFunctionToolInvoker:
    """本地函数工具调用器。"""

    def __init__(self, tool_id: str, func: Callable[..., Any]) -> None:
        self._tool_id = tool_id
        self._func = func

    @property
    def tool_identifier(self) -> str:
        return self._tool_id

    async def invoke(self, arguments: Dict[str, Any], timeout_seconds: int) -> ToolResult:
        """调用本地函数。"""
        try:
            result = self._func(**arguments)
            return ToolResult(success=True, output=result)
        except TypeError as e:
            return ToolResult(
                success=False,
                error_code=NODE_TOOL_ARGUMENT_MISMATCH,
                error_message=f"入参不匹配：{e}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error_code=NODE_TOOL_NOT_FOUND,
                error_message=f"工具执行失败：{e}",
            )
