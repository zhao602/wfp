# =============================================================================
# 工具发现聚合服务测试（tests/unit/test_tool_catalog_service.py）
#
# 覆盖 AC-1（merge_tool_into_agent_node spec.md 第 8 节）：
#   ① 三源聚合完整；② 来源标注非空；③ 只读零副作用（两次查询一致）；
#   ④ 仓储异常降级（清单仍含其余源项且不抛出，FR-1 规则 4）；
#   ⑤ mcp 同标识多版本去重（取 version 最大）；
#   ⑥ 跨源 identifier 冲突优先级（platform > mcp > local）。
# =============================================================================

# 沙箱限制：本机 asyncio 事件循环不可用（socketpair 被拦截），_run_async
# 采用手动驱动协程方式，不创建事件循环，故无需 import asyncio。

from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.integrations.tools.local_function_tool_invoker import (
    LocalFunctionToolInvoker,
)
from orchestration_platform.integrations.tools.local_function_tool_registry import (
    LocalFunctionToolRegistry,
)
from orchestration_platform.mcp_tool_market.models import (
    McpToolDescriptor,
    McpToolRegistration,
)
from orchestration_platform.tool_discovery.models import ToolSource
from orchestration_platform.tool_discovery.tool_catalog_service import (
    ToolCatalogService,
)


def _run_async(coro):
    """手动驱动协程至完成（沙箱事件循环不可用时的 fallback）。

    沙箱限制：本机 asyncio 事件循环创建需 socketpair，被网络策略拦截
    （WinError 10051 / _WindowsSelectorEventLoop._ssock AttributeError）。
    list_tools 的 await 链（_load_mcp_items → FakeMarketRepository.
    list_by_permission）均为无真实挂起点的协程，故以 send(None) 循环
    驱动至 StopIteration，环境无关。
    """
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


def _make_platform_descriptor(identifier: str, name: str = "平台工具") -> McpToolDescriptor:
    """构造平台工具声明。"""
    return McpToolDescriptor(
        tool_identifier=identifier,
        name=name,
        description="平台能力工具",
        permission=ToolPermission.READONLY,
        version="1.0.0",
    )


def _make_mcp_registration(
    identifier: str, version: str, permission: ToolPermission = ToolPermission.READONLY
) -> McpToolRegistration:
    """构造工具市场注册记录。"""
    return McpToolRegistration(
        descriptor=McpToolDescriptor(
            tool_identifier=identifier,
            name=f"MCP 工具 {identifier}",
            description="工具市场注册工具",
            version=version,
            permission=permission,
        ),
        registered_by="tester",
    )


class FakeMarketRepository:
    """测试替身：内存版工具市场仓储（仅实现聚合所需只读方法）。"""

    def __init__(self, registrations_by_permission=None, raise_error: bool = False):
        # registrations_by_permission: {ToolPermission: [McpToolRegistration]}
        self._registrations = registrations_by_permission or {}
        self._raise_error = raise_error
        self.list_by_permission_calls = 0

    async def list_by_permission(self, permission):
        self.list_by_permission_calls += 1
        if self._raise_error:
            raise OSError("工具市场本地仓储读取失败（文件损坏）")
        return list(self._registrations.get(permission, []))


class TestToolCatalogService:
    """工具目录聚合服务测试（AC-1）。"""

    def test_three_source_aggregation(self):
        """AC-1①：三源聚合——platform/mcp/local 各若干项合并完整。"""
        platform = [_make_platform_descriptor(f"platform_tool_{i}") for i in range(2)]
        market_repo = FakeMarketRepository(
            {
                ToolPermission.READONLY: [
                    _make_mcp_registration("mcp_tool_1", "1.0.0"),
                ],
            }
        )
        local_registry = LocalFunctionToolRegistry()
        local_registry.register(
            LocalFunctionToolInvoker(tool_id="local_tool_1", func=lambda: "ok")
        )

        service = ToolCatalogService(
            platform_declarations_provider=lambda: platform,
            market_repository=market_repo,
            local_registry=local_registry,
        )
        items = _run_async(service.list_tools())

        identifiers = {item.tool_identifier for item in items}
        assert identifiers == {"platform_tool_0", "platform_tool_1", "mcp_tool_1", "local_tool_1"}

    def test_source_annotation(self):
        """AC-1②：来源标注——每项含非空 source 字段且取值合法。"""
        service = ToolCatalogService(
            platform_declarations_provider=lambda: [_make_platform_descriptor("p1")],
            market_repository=FakeMarketRepository(
                {ToolPermission.READONLY: [_make_mcp_registration("m1", "1.0.0")]}
            ),
            local_registry=None,
        )
        items = _run_async(service.list_tools())

        assert len(items) == 2
        for item in items:
            assert item.source in (ToolSource.PLATFORM, ToolSource.MCP, ToolSource.LOCAL)
        by_id = {item.tool_identifier: item for item in items}
        assert by_id["p1"].source == ToolSource.PLATFORM
        assert by_id["m1"].source == ToolSource.MCP

    def test_readonly_no_side_effects(self):
        """AC-1③：只读零副作用——连续两次查询结果一致，仓储仅被只读调用。"""
        market_repo = FakeMarketRepository(
            {
                ToolPermission.READONLY: [_make_mcp_registration("m1", "1.0.0")],
                ToolPermission.READWRITE: [
                    _make_mcp_registration("m2", "2.0.0", ToolPermission.READWRITE)
                ],
            }
        )
        service = ToolCatalogService(
            platform_declarations_provider=lambda: [_make_platform_descriptor("p1")],
            market_repository=market_repo,
        )

        first = _run_async(service.list_tools())
        second = _run_async(service.list_tools())

        assert [i.tool_identifier for i in first] == [i.tool_identifier for i in second]
        assert len(first) == 3
        # 三种权限各查询一次 × 两次调用 = 6 次只读调用
        assert market_repo.list_by_permission_calls == 6

    def test_market_repository_failure_degrades(self):
        """AC-1④ / FR-1 规则 4：仓储异常降级——跳过该源，清单仍含 platform/local 项且不抛出。"""
        local_registry = LocalFunctionToolRegistry()
        local_registry.register(
            LocalFunctionToolInvoker(tool_id="local_tool_1", func=lambda: "ok")
        )
        service = ToolCatalogService(
            platform_declarations_provider=lambda: [_make_platform_descriptor("p1")],
            market_repository=FakeMarketRepository(raise_error=True),
            local_registry=local_registry,
        )

        items = _run_async(service.list_tools())  # 不得抛出

        identifiers = {item.tool_identifier for item in items}
        assert identifiers == {"p1", "local_tool_1"}

    def test_mcp_multi_version_dedup(self):
        """AC-1⑤：mcp 多版本去重——同 identifier 两版本取 version 字符序最大。"""
        market_repo = FakeMarketRepository(
            {
                ToolPermission.READONLY: [
                    _make_mcp_registration("shared_tool", "1.0.0"),
                    _make_mcp_registration("shared_tool", "2.3.1"),
                    _make_mcp_registration("shared_tool", "1.9.0"),
                ],
            }
        )
        service = ToolCatalogService(
            platform_declarations_provider=lambda: [],
            market_repository=market_repo,
        )
        items = _run_async(service.list_tools())

        assert len(items) == 1
        assert items[0].tool_identifier == "shared_tool"
        assert items[0].version == "2.3.1"

    def test_cross_source_conflict_platform_wins(self):
        """AC-1⑥：跨源冲突——同 identifier 三源各一项时保留 platform。"""
        local_registry = LocalFunctionToolRegistry()
        local_registry.register(
            LocalFunctionToolInvoker(tool_id="conflict_tool", func=lambda: "local")
        )
        service = ToolCatalogService(
            platform_declarations_provider=lambda: [
                _make_platform_descriptor("conflict_tool", name="平台权威工具")
            ],
            market_repository=FakeMarketRepository(
                {ToolPermission.READONLY: [_make_mcp_registration("conflict_tool", "1.0.0")]}
            ),
            local_registry=local_registry,
        )
        items = _run_async(service.list_tools())

        assert len(items) == 1
        assert items[0].source == ToolSource.PLATFORM
        assert items[0].name == "平台权威工具"

    def test_mcp_source_covers_three_permissions(self):
        """AC-1① 补充：mcp 源三权限并集（READONLY/READWRITE/ADMIN 均返回）。"""
        market_repo = FakeMarketRepository(
            {
                ToolPermission.READONLY: [
                    _make_mcp_registration("ro_tool", "1.0.0", ToolPermission.READONLY)
                ],
                ToolPermission.READWRITE: [
                    _make_mcp_registration("rw_tool", "1.0.0", ToolPermission.READWRITE)
                ],
                ToolPermission.ADMIN: [
                    _make_mcp_registration("admin_tool", "1.0.0", ToolPermission.ADMIN)
                ],
            }
        )
        service = ToolCatalogService(
            platform_declarations_provider=lambda: [],
            market_repository=market_repo,
        )
        items = _run_async(service.list_tools())

        assert {i.tool_identifier for i in items} == {"ro_tool", "rw_tool", "admin_tool"}

    def test_platform_provider_failure_degrades(self):
        """AC-1④ 补充：平台声明源异常同样降级跳过，不向调用方抛出。"""
        def _raise():
            raise RuntimeError("平台声明读取失败")

        service = ToolCatalogService(platform_declarations_provider=_raise)
        items = _run_async(service.list_tools())
        assert items == []

    def test_local_source_failure_degrades(self):
        """AC-1④ 补充：本地注册表源异常同样降级跳过，不向调用方抛出。"""

        class _BrokenRegistry:
            def list_invokers(self):
                raise RuntimeError("注册表读取失败")

        service = ToolCatalogService(
            platform_declarations_provider=lambda: [],
            local_registry=_BrokenRegistry(),
        )
        items = _run_async(service.list_tools())
        assert items == []
