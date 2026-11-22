# =============================================================================
# Tool 市场 API 路由（api/routers/tool_market_router.py）
#
# 职责（agent_tool_marketplace T10 / design §2.2.2 E-7~E-10 / DC-04）：
#   1. 清单/详情/分类只读目录（/api/v1/tool-market/**；清单 1:1 派生自
#      ToolCatalogService.list_tools()，无筛选时与 /api/v1/tools 标识集合
#      一致，AC-7）；
#   2. 工具注册复用既有 McpToolMarket.register 语义（E-10，仅经既有市场
#      能力落盘，不经旁路写入工具目录，Z-06）；
#   3. 响应不含 endpoint / api_key / base_url / 超时类凭据字段（INV-D08）。
# 装配：application_factory 按 tool_market_enabled 条件注册（T11.3/DC-09），
#       开关关闭时本模块不加载，端点访问 404。
# =============================================================================

from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from orchestration_platform.domain.errors import (
    DomainError,
    MARKET_TOOL_NOT_FOUND,
    MCP_TOOL_REGISTRATION_CONFLICT,
    ResourceNotFoundError,
)
from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.market.models import MarketCategoryCount, ToolMarketEntry
from orchestration_platform.market.tool_market_service import ToolMarketService
from orchestration_platform.mcp_tool_market.models import (
    McpToolDescriptor,
    RegistrationResult,
)
from orchestration_platform.mcp_tool_market.persistence.local_file_tool_market_repository import (
    LocalFileToolMarketRepository,
)
from orchestration_platform.mcp_tool_market.tool_market import McpToolMarket
from orchestration_platform.tool_discovery.tool_catalog_service import ToolCatalogService

# 模块级路由器：前缀 /tool-market（/api/v1 由应用工厂统一注入）
router = APIRouter(prefix="/tool-market", tags=["Tool 市场"])


def _build_service() -> ToolMarketService:
    """构造 Tool 市场服务。

    catalog_service 与 /api/v1/tools（tool_catalog_router）完全一致——裸
    ToolCatalogService()，保证无筛选时两端点标识集合相等（AC-7 回归层）；
    mcp_market 装配既有本地文件注册仓储（E-10 注册 + MCP 项 tags/schema 回填）。
    """
    return ToolMarketService(
        catalog_service=ToolCatalogService(),
        mcp_market=McpToolMarket(repository=LocalFileToolMarketRepository()),
    )


# ---------------------------------------------------------------------------
# 响应模型（显式 BaseModel，INV-D08）
# ---------------------------------------------------------------------------


class ToolMarketEntryListResponse(BaseModel):
    """E-7 响应：Tool 市场清单。"""

    tools: List[ToolMarketEntry] = Field(description="条目列表（1:1 派生自工具目录）")
    total: int = Field(description="条目总数（筛选后）")


class ToolMarketCategoryListResponse(BaseModel):
    """E-9 响应：分类计数（total = 分类数，对齐 design §2.2.2 E-9 样例）。"""

    categories: List[MarketCategoryCount] = Field(description="分类计数列表（按 tag 聚合）")
    total: int = Field(description="分类总数")


class ToolMarketRegisterRequest(BaseModel):
    """E-10 注册请求体（字段校验下沉既有 McpToolMarket.register 语义）。

    不含 endpoint / transport 等连接类字段（INV-D08；注册仅落元数据声明）。
    """

    tool_identifier: str = Field(default="", description="工具标识（非空，既有语义校验）")
    name: str = Field(default="", description="工具显示名称（非空，既有语义校验）")
    description: str = Field(default="", description="工具功能描述")
    version: str = Field(default="1.0.0", description="工具版本号（非空，既有语义校验）")
    permission: ToolPermission = Field(
        default=ToolPermission.READONLY, description="权限级别（readonly/readwrite/admin）"
    )
    input_schema: Dict[str, Any] = Field(default_factory=dict, description="输入参数 Schema")
    output_schema: Dict[str, Any] = Field(default_factory=dict, description="输出参数 Schema")
    tags: List[str] = Field(default_factory=list, description="标签列表")
    registered_by: str = Field(default="api", description="注册者标识")


# ---------------------------------------------------------------------------
# E-7 ~ E-9：清单 / 详情 / 分类
# ---------------------------------------------------------------------------


@router.get("/tools", response_model=ToolMarketEntryListResponse)
async def list_market_tools(
    q: Optional[str] = Query(default=None, description="名称关键字"),
    category: Optional[str] = Query(default=None, description="分类（tags 命中）"),
    permission: Optional[ToolPermission] = Query(
        default=None, description="权限级别（readonly|readwrite|admin）"
    ),
) -> ToolMarketEntryListResponse:
    """E-7：Tool 市场清单（无筛选时与 /api/v1/tools 标识集合一致，AC-7）。"""
    tools = await _build_service().list_tools(
        q=q, category=category, permission=permission
    )
    return ToolMarketEntryListResponse(tools=tools, total=len(tools))


@router.get("/tools/{tool_identifier}", response_model=ToolMarketEntry)
async def get_market_tool(tool_identifier: str) -> ToolMarketEntry:
    """E-8：Tool 市场条目详情（不存在 → 404 MARKET_TOOL_NOT_FOUND；响应不含 endpoint）。"""
    entry = await _build_service().get_tool(tool_identifier)
    if entry is None:
        raise ResourceNotFoundError(
            code=MARKET_TOOL_NOT_FOUND,
            message=f"工具市场中不存在该工具：{tool_identifier}",
            details={"tool_identifier": tool_identifier},
        )
    return entry


@router.get("/categories", response_model=ToolMarketCategoryListResponse)
async def list_market_categories() -> ToolMarketCategoryListResponse:
    """E-9：分类计数（按 tags 聚合，total = 分类数）。"""
    categories = await _build_service().list_categories()
    return ToolMarketCategoryListResponse(categories=categories, total=len(categories))


# ---------------------------------------------------------------------------
# E-10：工具注册（复用既有 McpToolMarket.register 语义）
# ---------------------------------------------------------------------------


@router.post("/tools", response_model=RegistrationResult)
async def register_market_tool(
    request: ToolMarketRegisterRequest,
) -> Union[RegistrationResult, JSONResponse]:
    """E-10：注册工具至市场（仅经既有 McpToolMarket.register 落盘，Z-06）。

    异常映射：缺标识/名称/版本 → 400 MCP_TOOL_REGISTRATION_CONFLICT（既有
    错误码，路由层就近映射为 400，避免走 DomainError 全局处理器落 500）；
    同标识同版本元数据不一致 → 200 含 success=false + conflict_reason（既有语义）。
    """
    descriptor = McpToolDescriptor(
        tool_identifier=request.tool_identifier,
        name=request.name,
        description=request.description,
        version=request.version,
        permission=request.permission,
        input_schema=request.input_schema,
        output_schema=request.output_schema,
        tags=list(request.tags),
    )
    try:
        return await _build_service().register_tool(
            descriptor, registered_by=request.registered_by
        )
    except DomainError as exc:
        if exc.code == MCP_TOOL_REGISTRATION_CONFLICT:
            return JSONResponse(status_code=400, content=exc.to_dict())
        raise
