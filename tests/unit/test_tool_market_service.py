# =============================================================================
# Tool 市场服务层单测（tests/unit/test_tool_market_service.py）
#
# 覆盖（T17.4 / T8 验收，design §2.2.3 AC-7 一致性保证机制 / DC-04）：
#   1. 1:1 派生不增删条目：mock catalog N 条 → 输出 N 条且标识集合相等；
#   2. 无筛选不调用 _apply_filters（契约层）；
#   3. q / category（走 tags 命中）/ permission（精确）筛选（子集视图）；
#   4. tags / schema 摘要按标识回填（platform 声明 + MCP 注册记录；local 缺省空）；
#   5. 缺失条目 → None；
#   6. 结构层静态检查：服务模块不使用按权限全量枚举仓储接口。
# =============================================================================

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.market.models import ParamSummary
from orchestration_platform.market.tool_market_service import ToolMarketService
from orchestration_platform.mcp_tool_market.models import (
    McpToolDescriptor,
    McpToolRegistration,
)
from orchestration_platform.tool_discovery.models import (
    ToolCatalogItem,
    ToolSource,
)

_SERVICE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "orchestration_platform"
    / "market"
    / "tool_market_service.py"
)


class _StubCatalogService:
    """ToolCatalogService 测试替身：固定清单 + 调用计数。"""

    def __init__(self, items: List[ToolCatalogItem]) -> None:
        self.items = items
        self.list_tools_calls = 0

    async def list_tools(self) -> List[ToolCatalogItem]:
        self.list_tools_calls += 1
        return list(self.items)


class _FakeMcpMarket:
    """McpToolMarket 测试替身：仅实现 get_tool（按标识+版本查注册记录）。"""

    def __init__(
        self, registrations: List[McpToolRegistration]
    ) -> None:
        self._registrations: Dict[Tuple[str, str], McpToolRegistration] = {
            (r.descriptor.tool_identifier, r.descriptor.version): r
            for r in registrations
        }
        self.get_tool_calls: List[Tuple[str, str]] = []

    async def get_tool(
        self, tool_identifier: str, version: str
    ) -> Optional[McpToolRegistration]:
        self.get_tool_calls.append((tool_identifier, version))
        return self._registrations.get((tool_identifier, version))


def _catalog_item(
    tool_identifier: str,
    name: str,
    source: ToolSource,
    permission: ToolPermission = ToolPermission.READONLY,
    version: str = "1.0.0",
) -> ToolCatalogItem:
    return ToolCatalogItem(
        tool_identifier=tool_identifier,
        name=name,
        description=f"{name} 描述",
        source=source,
        permission=permission,
        version=version,
    )


def _platform_descriptor(
    tool_identifier: str,
    name: str,
    tags: Optional[List[str]] = None,
    input_schema: Optional[Dict] = None,
    output_schema: Optional[Dict] = None,
) -> McpToolDescriptor:
    return McpToolDescriptor(
        tool_identifier=tool_identifier,
        name=name,
        description=f"{name} 描述",
        input_schema=input_schema or {},
        output_schema=output_schema or {},
        tags=tags or [],
    )


def _mcp_registration(
    tool_identifier: str,
    name: str,
    tags: Optional[List[str]] = None,
    version: str = "2.0.0",
) -> McpToolRegistration:
    return McpToolRegistration(
        descriptor=McpToolDescriptor(
            tool_identifier=tool_identifier,
            name=name,
            description=f"{name} 描述",
            version=version,
            tags=tags or [],
        ),
        registered_by="tester",
    )


def _make_service(
    items: List[ToolCatalogItem],
    platform_descriptors: Optional[List[McpToolDescriptor]] = None,
    mcp_registrations: Optional[List[McpToolRegistration]] = None,
) -> Tuple[ToolMarketService, _StubCatalogService]:
    catalog = _StubCatalogService(items)
    service = ToolMarketService(
        catalog_service=catalog,
        mcp_market=(
            _FakeMcpMarket(mcp_registrations) if mcp_registrations is not None else None
        ),
        platform_declarations_provider=lambda: platform_descriptors or [],
    )
    return service, catalog


class TestOneToOneDerivation:
    """AC-7 映射层：1:1 派生不增删条目（T8.3 / design §2.2.3）。"""

    async def test_no_filter_output_identifier_set_equals_catalog(self) -> None:
        items = [
            _catalog_item("get_workflow", "查询工作流定义", ToolSource.PLATFORM),
            _catalog_item("team.tool", "团队工具", ToolSource.MCP),
            _catalog_item("local_fn", "本地函数", ToolSource.LOCAL),
        ]
        service, catalog = _make_service(
            items,
            platform_descriptors=[_platform_descriptor("get_workflow", "查询工作流定义")],
            mcp_registrations=[_mcp_registration("team.tool", "团队工具")],
        )
        entries = await service.list_tools()
        assert catalog.list_tools_calls == 1
        assert len(entries) == len(items)
        assert {e.tool_identifier for e in entries} == {
            i.tool_identifier for i in items
        }

    async def test_entry_count_equals_catalog_count(self) -> None:
        items = [
            _catalog_item(f"tool_{index}", f"工具 {index}", ToolSource.LOCAL)
            for index in range(5)
        ]
        service, _ = _make_service(items)
        entries = await service.list_tools()
        assert len(entries) == 5

    async def test_entry_order_follows_catalog_order(self) -> None:
        items = [
            _catalog_item("b_tool", "B 工具", ToolSource.LOCAL),
            _catalog_item("a_tool", "A 工具", ToolSource.LOCAL),
        ]
        service, _ = _make_service(items)
        entries = await service.list_tools()
        assert [e.tool_identifier for e in entries] == ["b_tool", "a_tool"]

    async def test_base_fields_derived_from_catalog_item(self) -> None:
        items = [
            _catalog_item(
                "get_workflow",
                "查询工作流定义",
                ToolSource.PLATFORM,
                permission=ToolPermission.READONLY,
                version="1.0.0",
            )
        ]
        service, _ = _make_service(
            items, platform_descriptors=[_platform_descriptor("get_workflow", "查询工作流定义")]
        )
        entry = (await service.list_tools())[0]
        assert entry.name == "查询工作流定义"
        assert entry.description == "查询工作流定义 描述"
        assert entry.source == ToolSource.PLATFORM
        assert entry.permission == ToolPermission.READONLY
        assert entry.version == "1.0.0"


class TestApplyFiltersContract:
    """AC-7 契约层：无筛选不调用 _apply_filters（design §2.2.3）。"""

    async def test_no_filter_skips_apply_filters(self) -> None:
        items = [_catalog_item("t1", "工具一", ToolSource.LOCAL)]

        class _SpyService(ToolMarketService):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                self.apply_filters_calls: List[Tuple] = []

            def _apply_filters(self, entries, q, category, permission):
                self.apply_filters_calls.append((q, category, permission))
                return ToolMarketService._apply_filters(entries, q, category, permission)

        catalog = _StubCatalogService(items)
        service = _SpyService(catalog_service=catalog)
        await service.list_tools()
        assert service.apply_filters_calls == []

        await service.list_tools(q="工具")
        assert service.apply_filters_calls == [("工具", None, None)]

    async def test_q_filter_matches_name_substring_case_insensitive(self) -> None:
        items = [
            _catalog_item("wf", "Workflow 工具", ToolSource.LOCAL),
            _catalog_item("other", "其他工具", ToolSource.LOCAL),
        ]
        service, _ = _make_service(items)
        entries = await service.list_tools(q="workflow")
        assert {e.tool_identifier for e in entries} == {"wf"}

    async def test_category_filter_hits_via_tags(self) -> None:
        items = [
            _catalog_item("get_workflow", "查询工作流定义", ToolSource.PLATFORM),
            _catalog_item("local_fn", "本地函数", ToolSource.LOCAL),
        ]
        service, _ = _make_service(
            items,
            platform_descriptors=[
                _platform_descriptor("get_workflow", "查询工作流定义", tags=["workflow", "query"])
            ],
        )
        entries = await service.list_tools(category="workflow")
        assert {e.tool_identifier for e in entries} == {"get_workflow"}

    async def test_permission_filter_exact(self) -> None:
        items = [
            _catalog_item("ro", "只读工具", ToolSource.LOCAL, permission=ToolPermission.READONLY),
            _catalog_item("rw", "读写工具", ToolSource.LOCAL, permission=ToolPermission.READWRITE),
        ]
        service, _ = _make_service(items)
        entries = await service.list_tools(permission=ToolPermission.READWRITE)
        assert {e.tool_identifier for e in entries} == {"rw"}

    async def test_filtered_result_is_subset_view(self) -> None:
        items = [
            _catalog_item("get_workflow", "查询工作流定义", ToolSource.PLATFORM),
            _catalog_item("local_fn", "本地函数", ToolSource.LOCAL),
        ]
        service, _ = _make_service(
            items,
            platform_descriptors=[
                _platform_descriptor("get_workflow", "查询工作流定义", tags=["workflow"])
            ],
        )
        full = await service.list_tools()
        filtered = await service.list_tools(category="workflow")
        assert {e.tool_identifier for e in filtered} <= {e.tool_identifier for e in full}


class TestTagBackfill:
    """tags 按标识回填（T8.2：platform 声明 + MCP 注册记录；local 缺省 []）。"""

    async def test_platform_tags_backfilled(self) -> None:
        items = [_catalog_item("get_workflow", "查询工作流定义", ToolSource.PLATFORM)]
        service, _ = _make_service(
            items,
            platform_descriptors=[
                _platform_descriptor(
                    "get_workflow", "查询工作流定义", tags=["workflow", "query"]
                )
            ],
        )
        entry = (await service.list_tools())[0]
        assert entry.tags == ["workflow", "query"]

    async def test_mcp_tags_backfilled_by_identifier_and_version(self) -> None:
        items = [_catalog_item("team.tool", "团队工具", ToolSource.MCP, version="2.0.0")]
        service, _ = _make_service(
            items,
            mcp_registrations=[_mcp_registration("team.tool", "团队工具", tags=["team"], version="2.0.0")],
        )
        entry = (await service.list_tools())[0]
        assert entry.tags == ["team"]

    async def test_local_source_tags_default_empty(self) -> None:
        items = [_catalog_item("local_fn", "本地函数", ToolSource.LOCAL)]
        service, _ = _make_service(items)
        entry = (await service.list_tools())[0]
        assert entry.tags == []

    async def test_mcp_market_absent_degrades_to_empty_tags(self) -> None:
        items = [_catalog_item("team.tool", "团队工具", ToolSource.MCP)]
        service, _ = _make_service(items)
        entry = (await service.list_tools())[0]
        assert entry.tags == []

    async def test_platform_provider_error_degrades_without_dropping_entries(self) -> None:
        items = [_catalog_item("get_workflow", "查询工作流定义", ToolSource.PLATFORM)]

        def _boom() -> List[McpToolDescriptor]:
            raise RuntimeError("declaration source down")

        catalog = _StubCatalogService(items)
        service = ToolMarketService(
            catalog_service=catalog, platform_declarations_provider=_boom
        )
        entries = await service.list_tools()
        assert len(entries) == 1
        assert entries[0].tags == []


class TestSchemaSummaries:
    """schema 摘要（INV-D08：仅参数名与类型占位，对齐 design E-7 样例）。"""

    async def test_platform_schema_summaries_match_design_example(self) -> None:
        items = [_catalog_item("get_workflow", "查询工作流定义", ToolSource.PLATFORM)]
        service, _ = _make_service(
            items,
            platform_descriptors=[
                _platform_descriptor(
                    "get_workflow",
                    "查询工作流定义",
                    input_schema={
                        "definition_id": {"type": "string"},
                        "version": {"type": "integer"},
                    },
                    output_schema={"definition": {"type": "object"}},
                )
            ],
        )
        entry = (await service.list_tools())[0]
        assert entry.input_schema_summary == [
            ParamSummary(param="definition_id", type="string"),
            ParamSummary(param="version", type="integer"),
        ]
        assert entry.output_schema_summary == [
            ParamSummary(param="definition", type="object")
        ]

    async def test_flat_schema_description_not_leaked(self) -> None:
        items = [_catalog_item("validate_workflow", "校验工作流定义", ToolSource.PLATFORM)]
        service, _ = _make_service(
            items,
            platform_descriptors=[
                _platform_descriptor(
                    "validate_workflow",
                    "校验工作流定义",
                    input_schema={
                        "definition": {"type": "object", "description": "工作流定义"}
                    },
                )
            ],
        )
        entry = (await service.list_tools())[0]
        assert entry.input_schema_summary == [
            ParamSummary(param="definition", type="object")
        ]

    async def test_properties_wrapped_schema_supported(self) -> None:
        items = [_catalog_item("team.tool", "团队工具", ToolSource.MCP)]
        service, _ = _make_service(
            items,
            mcp_registrations=[
                McpToolRegistration(
                    descriptor=McpToolDescriptor(
                        tool_identifier="team.tool",
                        name="团队工具",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                        output_schema={},
                    ),
                    registered_by="tester",
                )
            ],
        )
        entry = (await service.list_tools())[0]
        assert entry.input_schema_summary == [ParamSummary(param="query", type="string")]

    async def test_empty_schema_returns_empty_summary(self) -> None:
        items = [_catalog_item("local_fn", "本地函数", ToolSource.LOCAL)]
        service, _ = _make_service(items)
        entry = (await service.list_tools())[0]
        assert entry.input_schema_summary == []
        assert entry.output_schema_summary == []


class TestGetTool:
    """缺失条目 → None（T17.4 / T8.5）。"""

    async def test_get_tool_found(self) -> None:
        items = [_catalog_item("get_workflow", "查询工作流定义", ToolSource.PLATFORM)]
        service, _ = _make_service(items)
        entry = await service.get_tool("get_workflow")
        assert entry is not None
        assert entry.tool_identifier == "get_workflow"

    async def test_get_tool_missing_returns_none(self) -> None:
        items = [_catalog_item("get_workflow", "查询工作流定义", ToolSource.PLATFORM)]
        service, _ = _make_service(items)
        assert await service.get_tool("no_such_tool") is None


class TestListCategories:
    """E-9：按 tags 聚合计数。"""

    async def test_list_categories_tag_counts(self) -> None:
        items = [
            _catalog_item("get_workflow", "查询工作流定义", ToolSource.PLATFORM),
            _catalog_item("local_fn", "本地函数", ToolSource.LOCAL),
        ]
        service, _ = _make_service(
            items,
            platform_descriptors=[
                _platform_descriptor(
                    "get_workflow", "查询工作流定义", tags=["workflow", "query"]
                )
            ],
        )
        categories = await service.list_categories()
        assert {c.category: c.count for c in categories} == {"query": 1, "workflow": 1}

    async def test_list_categories_sorted_deterministically(self) -> None:
        items = [
            _catalog_item("b_tool", "B 工具", ToolSource.PLATFORM),
            _catalog_item("a_tool", "A 工具", ToolSource.PLATFORM),
        ]
        service, _ = _make_service(
            items,
            platform_descriptors=[
                _platform_descriptor("b_tool", "B 工具", tags=["zeta"]),
                _platform_descriptor("a_tool", "A 工具", tags=["alpha"]),
            ],
        )
        names = [c.category for c in await service.list_categories()]
        assert names == sorted(names)

    async def test_list_categories_empty_when_no_tags(self) -> None:
        items = [_catalog_item("local_fn", "本地函数", ToolSource.LOCAL)]
        service, _ = _make_service(items)
        assert await service.list_categories() == []


class TestStructuralGuard:
    """AC-7 结构层静态检查（design §2.2.3：不 import 按权限全量枚举接口）。"""

    def test_service_module_avoids_permission_enumeration(self) -> None:
        source = _SERVICE_PATH.read_text(encoding="utf-8")
        assert "list_by_permission" not in source
