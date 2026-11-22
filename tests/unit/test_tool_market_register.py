# =============================================================================
# Tool 市场注册单测（tests/unit/test_tool_market_register.py）
#
# 覆盖（T17.5 / T8.6 验收，design §2.2.2 E-10 / §2.2.4 复用既有错误码）：
#   1. 正常注册 success=true（委托既有 McpToolMarket.register）；
#   2. 缺标识 / 缺名称 / 缺版本 → 既有 MCP_TOOL_REGISTRATION_CONFLICT；
#   3. 同标识同版本元数据不一致 → success=false + conflict_reason；
#   4. 完全一致重注册 → success=true（幂等覆盖）；
#   5. 市场未装配 → MARKET_STORAGE_UNAVAILABLE；
#   6. 注册不经旁路写入工具目录（catalog 清单不变）。
# =============================================================================

from typing import Dict, List, Optional, Tuple

import pytest

from orchestration_platform.domain.errors import (
    DomainError,
    MCP_TOOL_REGISTRATION_CONFLICT,
)
from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.market.tool_market_service import ToolMarketService
from orchestration_platform.mcp_tool_market.models import (
    McpToolDescriptor,
    McpToolRegistration,
    RegistrationResult,
)
from orchestration_platform.mcp_tool_market.tool_market import McpToolMarket
from orchestration_platform.tool_discovery.models import ToolCatalogItem, ToolSource


class _InMemoryMcpToolMarketRepository:
    """内存版 IMcpToolMarketRepository 测试替身（对齐仓储接口六方法）。"""

    def __init__(self) -> None:
        self.registrations: Dict[Tuple[str, str], McpToolRegistration] = {}

    async def save(self, registration: McpToolRegistration) -> None:
        key = (registration.descriptor.tool_identifier, registration.descriptor.version)
        self.registrations[key] = registration

    async def get(
        self, tool_identifier: str, version: str
    ) -> Optional[McpToolRegistration]:
        return self.registrations.get((tool_identifier, version))

    async def list_by_category(self, category: str) -> List[McpToolRegistration]:
        return [
            r
            for r in self.registrations.values()
            if category in r.descriptor.tags
        ]

    async def list_by_permission(
        self, permission: ToolPermission
    ) -> List[McpToolRegistration]:
        return [
            r
            for r in self.registrations.values()
            if r.descriptor.permission == permission
        ]

    async def list_versions(self, tool_identifier: str) -> List[McpToolRegistration]:
        versions = [
            r
            for r in self.registrations.values()
            if r.descriptor.tool_identifier == tool_identifier
        ]
        return sorted(versions, key=lambda r: r.descriptor.version)

    async def exists(self, tool_identifier: str, version: str) -> bool:
        return (tool_identifier, version) in self.registrations


class _StubCatalogService:
    """ToolCatalogService 测试替身：固定清单 + 调用计数。"""

    def __init__(self, items: List[ToolCatalogItem]) -> None:
        self.items = items
        self.list_tools_calls = 0

    async def list_tools(self) -> List[ToolCatalogItem]:
        self.list_tools_calls += 1
        return list(self.items)


def _descriptor(
    tool_identifier: str = "team.custom_tool",
    name: str = "团队自定义工具",
    version: str = "1.0.0",
    description: str = "团队自建能力工具",
    tags: Optional[List[str]] = None,
) -> McpToolDescriptor:
    return McpToolDescriptor(
        tool_identifier=tool_identifier,
        name=name,
        description=description,
        version=version,
        tags=tags or ["team"],
    )


def _make_service(
    repository: Optional[_InMemoryMcpToolMarketRepository] = None,
) -> Tuple[ToolMarketService, Optional[_InMemoryMcpToolMarketRepository], _StubCatalogService]:
    catalog = _StubCatalogService(
        [_make_catalog_item("get_workflow", "查询工作流定义", ToolSource.PLATFORM)]
    )
    repo = repository if repository is not None else _InMemoryMcpToolMarketRepository()
    market = McpToolMarket(repository=repo)
    service = ToolMarketService(catalog_service=catalog, mcp_market=market)
    return service, repo, catalog


def _make_catalog_item(
    tool_identifier: str, name: str, source: ToolSource
) -> ToolCatalogItem:
    return ToolCatalogItem(
        tool_identifier=tool_identifier,
        name=name,
        description=f"{name} 描述",
        source=source,
        permission=ToolPermission.READONLY,
        version="1.0.0",
    )


class TestRegisterToolSuccess:
    """E-10 正常注册（委托既有 McpToolMarket.register）。"""

    async def test_register_tool_success(self) -> None:
        service, repository, _ = _make_service()
        result = await service.register_tool(_descriptor(), registered_by="admin-user")
        assert isinstance(result, RegistrationResult)
        assert result.success is True
        assert result.tool_identifier == "team.custom_tool"
        assert result.version == "1.0.0"
        assert result.conflict_reason is None
        assert ("team.custom_tool", "1.0.0") in repository.registrations
        saved = repository.registrations[("team.custom_tool", "1.0.0")]
        assert saved.registered_by == "admin-user"

    async def test_register_tool_new_version_records_previous_version(self) -> None:
        service, repository, _ = _make_service()
        await service.register_tool(_descriptor(version="1.0.0"), registered_by="u")
        result = await service.register_tool(
            _descriptor(version="2.0.0"), registered_by="u"
        )
        assert result.success is True
        saved = repository.registrations[("team.custom_tool", "2.0.0")]
        assert saved.previous_version == "1.0.0"


class TestRegisterToolValidation:
    """元数据完整性校验（既有 MCP_TOOL_REGISTRATION_CONFLICT 语义）。"""

    async def test_register_tool_missing_identifier_rejected(self) -> None:
        service, _, _ = _make_service()
        with pytest.raises(DomainError) as exc_info:
            await service.register_tool(_descriptor(tool_identifier=""), registered_by="u")
        assert exc_info.value.code == MCP_TOOL_REGISTRATION_CONFLICT

    async def test_register_tool_missing_name_rejected(self) -> None:
        service, _, _ = _make_service()
        with pytest.raises(DomainError) as exc_info:
            await service.register_tool(_descriptor(name=""), registered_by="u")
        assert exc_info.value.code == MCP_TOOL_REGISTRATION_CONFLICT

    async def test_register_tool_missing_version_rejected(self) -> None:
        service, _, _ = _make_service()
        with pytest.raises(DomainError) as exc_info:
            await service.register_tool(_descriptor(version=""), registered_by="u")
        assert exc_info.value.code == MCP_TOOL_REGISTRATION_CONFLICT


class TestRegisterToolConflict:
    """同标识同版本冲突语义（E-10：success=false + conflict_reason）。"""

    async def test_same_id_version_metadata_conflict_fails(self) -> None:
        service, repository, _ = _make_service()
        first = await service.register_tool(_descriptor(), registered_by="u")
        assert first.success is True
        conflicting = await service.register_tool(
            _descriptor(description="被篡改的描述"), registered_by="other"
        )
        assert conflicting.success is False
        assert conflicting.conflict_reason is not None
        assert "不一致" in conflicting.conflict_reason
        saved = repository.registrations[("team.custom_tool", "1.0.0")]
        assert saved.descriptor.description == "团队自建能力工具"

    async def test_identical_reregistration_succeeds(self) -> None:
        service, _, _ = _make_service()
        first = await service.register_tool(_descriptor(), registered_by="u")
        second = await service.register_tool(_descriptor(), registered_by="u")
        assert first.success is True
        assert second.success is True
        assert second.conflict_reason is None


class TestRegisterToolDegradation:
    """市场未装配 / 旁路写入防护。"""

    async def test_register_tool_without_market_raises_storage_unavailable(self) -> None:
        catalog = _StubCatalogService([])
        service = ToolMarketService(catalog_service=catalog, mcp_market=None)
        with pytest.raises(DomainError) as exc_info:
            await service.register_tool(_descriptor(), registered_by="u")
        assert exc_info.value.code == "MARKET_STORAGE_UNAVAILABLE"

    async def test_register_tool_does_not_write_catalog_side_channel(self) -> None:
        service, _, catalog = _make_service()
        before = list(catalog.items)
        await service.register_tool(_descriptor(), registered_by="u")
        assert catalog.items == before
        entries = await service.list_tools()
        assert {e.tool_identifier for e in entries} == {"get_workflow"}
        assert catalog.list_tools_calls == 1
