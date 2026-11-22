# =============================================================================
# Tool 市场服务层（market/tool_market_service.py）
#
# 职责（agent_tool_marketplace T8 / design §2.2.2 E-7~E-10 / DC-04）：
#   1. 清单唯一主键来源 = ToolCatalogService.list_tools()（零自建三源聚合，AC-7 结构层）；
#   2. tags / schema 摘要按标识从三源原始数据回填（platform 声明 + MCP 注册记录；
#      local 源无 tags/schema → 缺省空列表，不新建数据源）；
#   3. 1:1 派生：entries = [to_entry(item) for item in catalog]（不增删条目）；
#      无筛选时不调用 _apply_filters → 输出集合 = catalog 集合（AC-7 契约层）；
#   4. register_tool 委托既有 McpToolMarket.register（元数据校验 + 同标识同版本冲突语义，
#      不经旁路写入工具目录，T8.6）。
# 设计意图：
#   1. 禁用按权限全量枚举仓储接口（结构性静态检查，design §2.2.3 结构层）；
#   2. schema 摘要仅参数名与类型占位（INV-D08，不含示例值/默认值/凭据/端点）；
#   3. get_tool 缺失返回 None（路由层映射 MARKET_TOOL_NOT_FOUND / 404，对齐 T17.4）；
#   4. 响应白名单：显式 pydantic 模型逐字段构造（INV-D08）。
# =============================================================================

import logging
from typing import Callable, Dict, List, Optional, Tuple

from orchestration_platform.domain.errors import (
    DomainError,
    MARKET_STORAGE_UNAVAILABLE,
)
from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.integrations.mcp_server.tool_declarations import (
    get_platform_tool_declarations,
)
from orchestration_platform.market.models import (
    MarketCategoryCount,
    ParamSummary,
    ToolMarketEntry,
)
from orchestration_platform.mcp_tool_market.models import (
    McpToolDescriptor,
    RegistrationResult,
)
from orchestration_platform.mcp_tool_market.tool_market import McpToolMarket
from orchestration_platform.tool_discovery.models import (
    ToolCatalogItem,
    ToolSource,
)
from orchestration_platform.tool_discovery.tool_catalog_service import ToolCatalogService

_logger = logging.getLogger(__name__)


def _summarize_schema(schema: object) -> List[ParamSummary]:
    """schema → 参数摘要列表（仅参数名与类型占位，INV-D08）。

    兼容两种既有形态：
      1. 平台声明扁平形态：{"param": {"type": "string", ...}, ...}；
      2. JSON Schema properties 形态：{"type": "object", "properties": {...}}。
    """
    if not isinstance(schema, dict) or not schema:
        return []
    properties = schema.get("properties")
    entries = properties if isinstance(properties, dict) else schema
    summaries: List[ParamSummary] = []
    for param, spec in entries.items():
        if isinstance(spec, dict):
            param_type = spec.get("type", "string")
            if isinstance(param_type, list):
                param_type = param_type[0] if param_type else "string"
            summaries.append(ParamSummary(param=str(param), type=str(param_type)))
        else:
            summaries.append(ParamSummary(param=str(param)))
    return summaries


class ToolMarketService:
    """Tool 市场服务：三源工具目录的市场侧包装（1:1 派生 + 元数据回填 + 筛选）。"""

    def __init__(
        self,
        catalog_service: Optional[ToolCatalogService] = None,
        mcp_market: Optional[McpToolMarket] = None,
        platform_declarations_provider: Callable[..., List[McpToolDescriptor]] = (
            get_platform_tool_declarations
        ),
    ) -> None:
        self._catalog_service = catalog_service or ToolCatalogService()
        self._mcp_market = mcp_market
        self._platform_declarations_provider = platform_declarations_provider

    # ------------------------------------------------------------------
    # 清单 / 详情 / 分类（E-7 / E-8 / E-9）
    # ------------------------------------------------------------------

    async def list_tools(
        self,
        q: Optional[str] = None,
        category: Optional[str] = None,
        permission: Optional[ToolPermission] = None,
    ) -> List[ToolMarketEntry]:
        """Tool 市场清单（1:1 派生自 ToolCatalogService.list_tools()，AC-7）。"""
        catalog = await self._catalog_service.list_tools()  # 唯一主键来源
        descriptor_index = await self._load_descriptor_index(catalog)
        tag_index = self._build_tag_index(descriptor_index)
        schema_index = self._build_schema_index(descriptor_index)
        entries = [
            self._to_entry(
                item,
                tag_index.get(item.tool_identifier, []),
                schema_index.get(item.tool_identifier, ([], [])),
            )
            for item in catalog
        ]  # 1:1 映射，条目数不变（DC-04 映射层）
        if q or category or permission:
            entries = self._apply_filters(entries, q, category, permission)
        return entries

    async def get_tool(self, tool_identifier: str) -> Optional[ToolMarketEntry]:
        """按标识查询 Tool 市场条目；不存在返回 None（路由层映射 404）。"""
        for entry in await self.list_tools():
            if entry.tool_identifier == tool_identifier:
                return entry
        return None

    async def list_categories(self) -> List[MarketCategoryCount]:
        """分类计数视图（E-9）：按 tags 聚合计数，按分类名排序保证确定性。"""
        counts: Dict[str, int] = {}
        for entry in await self.list_tools():
            for tag in entry.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return [
            MarketCategoryCount(category=tag, count=counts[tag])
            for tag in sorted(counts)
        ]

    # ------------------------------------------------------------------
    # 工具注册（E-10，委托既有 McpToolMarket）
    # ------------------------------------------------------------------

    async def register_tool(
        self,
        descriptor: McpToolDescriptor,
        registered_by: str,
    ) -> RegistrationResult:
        """注册工具（委托既有 McpToolMarket.register，不经旁路写入工具目录，T8.6）。

        Raises:
            DomainError: 市场未装配（MARKET_STORAGE_UNAVAILABLE / 500）；
                元数据不完整经既有语义抛 MCP_TOOL_REGISTRATION_CONFLICT。
        """
        if self._mcp_market is None:
            raise DomainError(
                code=MARKET_STORAGE_UNAVAILABLE,
                message="市场存储不可用，请稍后重试",
                details={"reason": "McpToolMarket 未装配"},
            )
        return await self._mcp_market.register(descriptor, registered_by)

    # ------------------------------------------------------------------
    # 私有方法：原始数据索引（T8.2）/ 1:1 派生（T8.3）/ 筛选（T8.4）
    # ------------------------------------------------------------------

    async def _load_descriptor_index(
        self, catalog: List[ToolCatalogItem]
    ) -> Dict[str, McpToolDescriptor]:
        """按标识索引三源原始描述符（platform 声明 + MCP 注册记录；异常源降级跳过）。

        MCP 项按 catalog 条目的精确版本经市场 get_tool 取注册记录；
        不做任何全量枚举（design §2.2.3 结构层）。
        """
        index: Dict[str, McpToolDescriptor] = {}
        try:
            for descriptor in self._platform_declarations_provider():
                index[descriptor.tool_identifier] = descriptor
        except Exception as exc:  # 单源降级：跳过该源
            _logger.warning("Tool 市场 platform 声明源加载失败，已跳过：%s", exc)
        if self._mcp_market is None:
            return index
        for item in catalog:
            if item.tool_identifier in index or item.source != ToolSource.MCP:
                continue
            try:
                registration = await self._mcp_market.get_tool(
                    item.tool_identifier, item.version
                )
            except Exception as exc:  # 单源降级：跳过该条
                _logger.warning(
                    "Tool 市场 MCP 注册记录加载失败（%s），已跳过：%s",
                    item.tool_identifier,
                    exc,
                )
                continue
            if registration is not None:
                index[registration.descriptor.tool_identifier] = (
                    registration.descriptor
                )
        return index

    @staticmethod
    def _build_tag_index(
        descriptor_index: Dict[str, McpToolDescriptor]
    ) -> Dict[str, List[str]]:
        """按标识索引 tags（platform/MCP 原始声明；local 源无 tags 缺省 []，T8.2）。"""
        return {
            identifier: list(descriptor.tags)
            for identifier, descriptor in descriptor_index.items()
        }

    @staticmethod
    def _build_schema_index(
        descriptor_index: Dict[str, McpToolDescriptor]
    ) -> Dict[str, Tuple[List[ParamSummary], List[ParamSummary]]]:
        """按标识索引输入/输出 schema 摘要（仅参数名与类型占位，INV-D08）。"""
        return {
            identifier: (
                _summarize_schema(descriptor.input_schema),
                _summarize_schema(descriptor.output_schema),
            )
            for identifier, descriptor in descriptor_index.items()
        }

    @staticmethod
    def _to_entry(
        item: ToolCatalogItem,
        tags: List[str],
        schema_summaries: Tuple[List[ParamSummary], List[ParamSummary]],
    ) -> ToolMarketEntry:
        """catalog 条目 → Tool 市场条目（字段派生，不增删条目，DC-04 映射层）。"""
        return ToolMarketEntry(
            tool_identifier=item.tool_identifier,
            name=item.name,
            description=item.description,
            source=item.source,
            permission=item.permission,
            version=item.version,
            tags=list(tags),
            input_schema_summary=list(schema_summaries[0]),
            output_schema_summary=list(schema_summaries[1]),
        )

    @staticmethod
    def _apply_filters(
        entries: List[ToolMarketEntry],
        q: Optional[str],
        category: Optional[str],
        permission: Optional[ToolPermission],
    ) -> List[ToolMarketEntry]:
        """筛选（清单子集视图，T8.4）：q 名称关键字 / category 走 tags 命中 / permission 精确。"""
        result = entries
        if q:
            keyword = q.lower()
            result = [entry for entry in result if keyword in entry.name.lower()]
        if category:
            result = [entry for entry in result if category in entry.tags]
        if permission is not None:
            result = [entry for entry in result if entry.permission == permission]
        return result
