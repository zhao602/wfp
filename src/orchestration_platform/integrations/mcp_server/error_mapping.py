# =============================================================================
# MCP 错误映射（integrations/mcp_server/error_mapping.py）
#
# 职责：将平台内部错误映射为 MCP 协议标准错误响应。
# 设计意图（对应 spec 5.3.1.4、任务 T32.4）：
#   1. 不暴露内部堆栈信息；
#   2. 超时映射为 MCP_TOOL_TIMEOUT。
# =============================================================================

from typing import Any, Dict

from orchestration_platform.domain.errors import (
    DATASOURCE_READ_TIMEOUT,
    MCP_TOOL_TIMEOUT,
    NODE_TIMEOUT,
    DomainError,
)


def map_to_mcp_error(error: Exception) -> Dict[str, Any]:
    """将平台异常映射为 MCP 标准错误响应。

    Args:
        error: 平台内部异常。

    Returns:
        MCP 标准错误响应字典：{error_code, error_message}
    """
    if isinstance(error, DomainError):
        if error.code in (NODE_TIMEOUT, DATASOURCE_READ_TIMEOUT):
            return {"error_code": MCP_TOOL_TIMEOUT, "error_message": "工具调用超时"}
        return {"error_code": error.code, "error_message": error.message}

    # 非领域异常不暴露内部信息
    return {"error_code": "MCP_INTERNAL_ERROR", "error_message": "内部错误"}
