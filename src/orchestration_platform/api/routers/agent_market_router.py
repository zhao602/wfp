# =============================================================================
# Agent 市场 API 路由（api/routers/agent_market_router.py）
#
# 职责（agent_tool_marketplace T9 / design §2.2.2 E-1~E-6 / DC-03）：
#   1. 条目清单 / 详情 / 分类计数 / 注册（/api/v1/agent-market/**）；
#   2. 内置模板只读访问（清单恒 5、详情含 recipe: null|{nodes,edges}，AC-12）；
#   3. 只读端点仅注册 GET（写方法自动 405，AC-6 规则 2/3）；
#   4. 响应白名单：显式 pydantic 模型逐字段构造，禁止 **dict 透传（INV-D08）；
#      响应不含 api_key / base_url / 超时类 / endpoint 凭据字段。
# 装配：application_factory 按 agent_market_enabled 条件注册（T11.3/DC-09），
#       开关关闭时本模块不加载，端点访问 404。
# =============================================================================

from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from orchestration_platform.market.agent_market_service import (
    AgentMarketRegisterResult,
    AgentMarketService,
)
from orchestration_platform.market.agent_templates import AgentTemplate
from orchestration_platform.market.models import (
    AgentMarketEntry,
    AgentMarketEntryDetail,
    MarketCategoryCount,
    MarketEntryStatus,
    TemplateDoc,
    TemplateEdgePreset,
    TemplateMetric,
    TemplateNodePreset,
    TemplatePerformanceBoundaries,
    TemplatePreset,
    TemplateToolConfig,
)
from orchestration_platform.persistence.repository_factory import RepositoryFactory

# 模块级路由器：前缀 /agent-market（/api/v1 由应用工厂统一注入）
router = APIRouter(prefix="/agent-market", tags=["Agent 市场"])

# E-5 模板摘要固定短文案（design §2.2.2 E-5；AgentTemplate 无 summary 字段，路由层承载）
_TEMPLATE_SUMMARIES = {
    "plan_and_execute": "规划→人工评审→执行",
    "basic_react": "最简单环轻量入门",
    "multi_agent": "多智能体协作+知识库检索",
    "reflexive_agent": "生成→独立批判→修订",
    "autonomous_loop": "自主循环+上下文压缩",
}


def _build_service() -> AgentMarketService:
    """按配置构造 Agent 市场服务（仓储经 RepositoryFactory 选择后端，OP-9）。"""
    return AgentMarketService(
        repository=RepositoryFactory.create_agent_market_repository()
    )


# ---------------------------------------------------------------------------
# 响应模型（显式 BaseModel，INV-D08）
# ---------------------------------------------------------------------------


class AgentMarketEntryListResponse(BaseModel):
    """E-1 响应：市场条目清单。"""

    entries: List[AgentMarketEntry] = Field(
        description="条目列表（内置 ∪ 注册，内置优先去重）"
    )
    total: int = Field(description="条目总数（筛选后）")


class AgentMarketCategoryListResponse(BaseModel):
    """E-3 响应：分类计数（total = 条目数，对齐 design §2.2.2 E-3 样例）。"""

    categories: List[MarketCategoryCount] = Field(
        description="分类计数列表（按分类名排序）"
    )
    total: int = Field(description="条目总数（各分类计数之和）")


class AgentMarketRegisterRequest(BaseModel):
    """E-4 注册请求体（字段校验下沉服务层，缺 entry_id/name → MARKET_ENTRY_INVALID 422）。"""

    entry_id: str = Field(default="", description="条目全局唯一标识（非空，服务层校验）")
    name: str = Field(default="", description="条目显示名称（非空，服务层校验）")
    description: str = Field(default="", description="条目功能描述")
    category: str = Field(default="general", description="主分类（缺省 general）")
    tags: List[str] = Field(default_factory=list, description="标签列表")
    status: Optional[str] = Field(
        default=None,
        description="条目状态（available/draft/pending_review；缺省 draft，服务层校验）",
    )
    preset_ref: Optional[str] = Field(default=None, description="配置预设引用（可空）")
    tool_refs: List[str] = Field(default_factory=list, description="工具引用集合（弱引用）")


class TemplateSummaryItem(BaseModel):
    """E-5 模板摘要条目（6 字段）。"""

    template_id: str = Field(description="模板标识")
    name: str = Field(description="模板名称")
    kind: str = Field(description="模板形态：single_node / recipe")
    summary: str = Field(description="模板摘要（固定短文案）")
    allowed_tools: List[str] = Field(description="工具选择集")
    max_tool_rounds: Optional[int] = Field(
        default=None, description="单节点轮次上限（None=回落全局缺省）"
    )


class TemplateListResponse(BaseModel):
    """E-5 响应：模板清单（total 恒 5，AC-12）。"""

    templates: List[TemplateSummaryItem] = Field(description="模板摘要列表（预置顺序）")
    total: int = Field(description="模板总数（恒 5）")


class TemplateRecipeResponse(BaseModel):
    """E-6 配方预设响应（kind=recipe 时非空）。"""

    nodes: List[TemplateNodePreset] = Field(description="配方节点预设（复用既有节点类型）")
    edges: List[TemplateEdgePreset] = Field(description="配方连线预设")


class TemplateDetailResponse(BaseModel):
    """E-6 响应：模板详情（全字段 + recipe: null|{nodes,edges}）。"""

    template_id: str = Field(description="模板标识")
    name: str = Field(description="模板名称")
    kind: str = Field(description="模板形态：single_node / recipe")
    preset: TemplatePreset = Field(description="预设配置（键集合 ⊆ 白名单）")
    system_prompt: str = Field(description="提示词模板")
    allowed_tools: List[str] = Field(description="工具选择集")
    max_tool_rounds: Optional[int] = Field(default=None, description="单节点轮次上限")
    output_max_retries: Optional[int] = Field(default=None, description="输出校验重试上限")
    doc: TemplateDoc = Field(description="五维度说明文档")
    tool_config: TemplateToolConfig = Field(description="工具调用配置")
    implementation_key_points: List[str] = Field(default_factory=list, description="实现关键点")
    dependency_conditions: List[str] = Field(default_factory=list, description="依赖条件")
    performance_boundaries: TemplatePerformanceBoundaries = Field(description="性能边界")
    security_boundaries: List[str] = Field(default_factory=list, description="安全边界")
    evaluation_metrics: List[TemplateMetric] = Field(default_factory=list, description="评价指标")
    recipe: Optional[TemplateRecipeResponse] = Field(
        default=None, description="配方预设（single_node 为 null）"
    )


# ---------------------------------------------------------------------------
# E-1 ~ E-4：条目清单 / 详情 / 分类 / 注册
# ---------------------------------------------------------------------------


@router.get("/entries", response_model=AgentMarketEntryListResponse)
async def list_market_entries(
    q: Optional[str] = Query(default=None, description="关键字（名称/标识/描述命中）"),
    category: Optional[str] = Query(default=None, description="主分类（精确匹配）"),
    tag: Optional[str] = Query(default=None, description="标签（tags 命中）"),
    status: Optional[MarketEntryStatus] = Query(
        default=None, description="条目状态（非法值 → 422）"
    ),
) -> AgentMarketEntryListResponse:
    """E-1：市场条目清单（内置 ∪ 注册；仓储异常降级内置条目仍返回，DC-12）。"""
    entries = await _build_service().list_entries(
        query=q, category=category, tag=tag, status=status
    )
    return AgentMarketEntryListResponse(entries=entries, total=len(entries))


@router.get("/entries/{entry_id}", response_model=AgentMarketEntryDetail)
async def get_market_entry(entry_id: str) -> AgentMarketEntryDetail:
    """E-2：条目详情（preset_ref 指向内置模板时附加 preset 与 template_ref）。

    未知 entry_id → 404 MARKET_ENTRY_NOT_FOUND（全局异常处理器映射）。
    """
    return await _build_service().get_entry(entry_id)


@router.get("/categories", response_model=AgentMarketCategoryListResponse)
async def list_market_categories() -> AgentMarketCategoryListResponse:
    """E-3：分类计数（total = 条目数，对齐 design §2.2.2 E-3 样例）。"""
    categories = await _build_service().list_categories()
    return AgentMarketCategoryListResponse(
        categories=categories,
        total=sum(item.count for item in categories),
    )


@router.post("/entries", response_model=AgentMarketRegisterResult)
async def register_market_entry(
    request: AgentMarketRegisterRequest,
) -> AgentMarketRegisterResult:
    """E-4：注册条目元数据（同标识已注册条目覆盖，result 区分 created/replaced）。

    异常映射（全局处理器）：缺 entry_id/name → 422 MARKET_ENTRY_INVALID；
    覆盖内置条目 → 409 MARKET_ENTRY_BUILTIN_IMMUTABLE；仓储不可写 → 500。
    """
    service = _build_service()
    return await service.register_entry(payload=request.model_dump())


# ---------------------------------------------------------------------------
# E-5 / E-6：内置模板只读访问
# ---------------------------------------------------------------------------


def _to_summary_item(template: AgentTemplate) -> TemplateSummaryItem:
    """模板 → E-5 摘要条目（summary 为固定短文案）。"""
    return TemplateSummaryItem(
        template_id=template.template_id,
        name=template.name,
        kind=template.kind,
        summary=_TEMPLATE_SUMMARIES.get(template.template_id, template.name),
        allowed_tools=list(template.allowed_tools),
        max_tool_rounds=template.max_tool_rounds,
    )


def _to_detail_response(template: AgentTemplate) -> TemplateDetailResponse:
    """模板 → E-6 详情（recipe: null|{nodes,edges}）。"""
    recipe = (
        TemplateRecipeResponse(nodes=list(template.nodes), edges=list(template.edges))
        if template.kind == "recipe"
        else None
    )
    return TemplateDetailResponse(
        template_id=template.template_id,
        name=template.name,
        kind=template.kind,
        preset=template.preset,
        system_prompt=template.system_prompt,
        allowed_tools=list(template.allowed_tools),
        max_tool_rounds=template.max_tool_rounds,
        output_max_retries=template.output_max_retries,
        doc=template.doc,
        tool_config=template.tool_config,
        implementation_key_points=list(template.implementation_key_points),
        dependency_conditions=list(template.dependency_conditions),
        performance_boundaries=template.performance_boundaries,
        security_boundaries=list(template.security_boundaries),
        evaluation_metrics=list(template.evaluation_metrics),
        recipe=recipe,
    )


@router.get("/templates", response_model=TemplateListResponse)
async def list_market_templates() -> TemplateListResponse:
    """E-5：内置模板清单（total 恒 5，AC-12）。"""
    templates = _build_service().list_templates()
    return TemplateListResponse(
        templates=[_to_summary_item(template) for template in templates],
        total=len(templates),
    )


@router.get("/templates/{template_id}", response_model=TemplateDetailResponse)
async def get_market_template(template_id: str) -> TemplateDetailResponse:
    """E-6：模板详情（未知 template_id → 404 MARKET_TEMPLATE_NOT_FOUND）。"""
    return _to_detail_response(_build_service().get_template(template_id))
