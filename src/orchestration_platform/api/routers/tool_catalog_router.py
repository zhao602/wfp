# =============================================================================
# 工具发现路由（api/routers/tool_catalog_router.py）
#
# 职责：暴露 GET /api/v1/tools 统一工具清单端点（三源聚合，只读）。
# 设计意图（merge_tool_into_agent_node DC-A / FR-1 / CHG-7、CHG-10）：
#   1. 薄层无业务逻辑——全部聚合逻辑落在 ToolCatalogService；
#   2. 恒 200（service 层单源降级保证清单恒可用）；
#   3. 无鉴权依赖（与既有 mcp_server_router 同级开放）；
#   4. 既有 GET /api/v1/mcp/server/tools 完全不动（INV-1）。
# =============================================================================

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from orchestration_platform.tool_discovery import ToolCatalogItem, ToolCatalogService

router = APIRouter(tags=["工具发现"])


class ToolCatalogResponse(BaseModel):
    """工具清单响应：items + total。"""

    tools: List[ToolCatalogItem] = Field(default_factory=list, description="工具清单")
    total: int = Field(default=0, description="工具总数")


@router.get("/tools", response_model=ToolCatalogResponse)
async def list_tools() -> ToolCatalogResponse:
    """返回三源聚合工具清单（platform / mcp / local）。"""
    service = ToolCatalogService()
    items = await service.list_tools()
    return ToolCatalogResponse(tools=items, total=len(items))
