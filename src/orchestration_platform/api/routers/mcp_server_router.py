# =============================================================================
# MCP 服务端工具查询路由（api/routers/mcp_server_router.py）
#
# 职责：提供 MCP 服务端工具清单查询端点。
# 设计意图（对应任务 T41.4）：
#   GET /api/v1/mcp/server/tools 返回平台暴露的 MCP 工具清单。
# =============================================================================

from fastapi import APIRouter

from orchestration_platform.integrations.mcp_server.tool_declarations import (
    get_platform_tool_declarations,
)

router = APIRouter(tags=["MCP 服务端"])


@router.get("/mcp/server/tools")
async def list_mcp_server_tools() -> list:
    """查询平台暴露的 MCP 工具清单。"""
    tools = get_platform_tool_declarations()
    return [tool.model_dump() for tool in tools]
