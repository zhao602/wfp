# =============================================================================
# MCP 工具市场测试（tests/unit/test_mcp_tool_market.py）
#
# 覆盖 A32（工具市场）、A33（权限标注）、B25（权限校验）。
# =============================================================================

import asyncio
import os
import tempfile
import pytest

from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.mcp_tool_market.models import (
    CallerIdentity,
    McpToolDescriptor,
    McpToolRegistration,
    RegistrationResult,
)
from orchestration_platform.mcp_tool_market.tool_market import McpToolMarket
from orchestration_platform.mcp_tool_market.version_manager import VersionManager
from orchestration_platform.mcp_tool_market.permission_checker import PermissionChecker
from orchestration_platform.mcp_tool_market.persistence.local_file_tool_market_repository import (
    LocalFileToolMarketRepository,
)


def run_coro(coro):
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def temp_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield LocalFileToolMarketRepository(storage_dir=tmpdir)


class TestMcpToolMarket:
    """工具市场测试。"""

    def test_register_new_tool(self, temp_repo):
        market = McpToolMarket(temp_repo)
        descriptor = McpToolDescriptor(
            tool_identifier="test_tool",
            name="测试工具",
            version="1.0.0",
        )
        result = run_coro(market.register(descriptor, "user_001"))
        assert result.success is True

    def test_register_duplicate_same_metadata(self, temp_repo):
        market = McpToolMarket(temp_repo)
        descriptor = McpToolDescriptor(
            tool_identifier="test_tool",
            name="测试工具",
            version="1.0.0",
        )
        run_coro(market.register(descriptor, "user_001"))
        result = run_coro(market.register(descriptor, "user_001"))
        assert result.success is True

    def test_register_duplicate_different_metadata(self, temp_repo):
        market = McpToolMarket(temp_repo)
        desc1 = McpToolDescriptor(tool_identifier="test_tool", name="名称A", version="1.0.0")
        desc2 = McpToolDescriptor(tool_identifier="test_tool", name="名称B", version="1.0.0")
        run_coro(market.register(desc1, "user_001"))
        result = run_coro(market.register(desc2, "user_001"))
        assert result.success is False
        assert result.conflict_reason is not None

    def test_register_multiple_versions(self, temp_repo):
        market = McpToolMarket(temp_repo)
        for v in ["1.0.0", "1.1.0", "2.0.0"]:
            desc = McpToolDescriptor(tool_identifier="tool", name="工具", version=v)
            result = run_coro(market.register(desc, "user"))
            assert result.success is True

    def test_list_by_category(self, temp_repo):
        market = McpToolMarket(temp_repo)
        desc = McpToolDescriptor(
            tool_identifier="tool1",
            name="工具1",
            version="1.0.0",
            tags=["analytics"],
        )
        run_coro(market.register(desc, "user"))
        results = run_coro(market.list_by_category("analytics"))
        assert len(results) == 1

    def test_list_versions(self, temp_repo):
        market = McpToolMarket(temp_repo)
        for v in ["1.0.0", "2.0.0"]:
            desc = McpToolDescriptor(tool_identifier="tool", name="工具", version=v)
            run_coro(market.register(desc, "user"))
        versions = run_coro(market.list_versions("tool"))
        assert len(versions) == 2


class TestVersionManager:
    """版本管理测试。"""

    def test_list_versions(self, temp_repo):
        vm = VersionManager(temp_repo)
        for v in ["1.0.0", "1.1.0", "2.0.0"]:
            desc = McpToolDescriptor(tool_identifier="tool", name="工具", version=v)
            run_coro(temp_repo.save(McpToolRegistration(descriptor=desc, registered_by="user")))
        versions = run_coro(vm.list_versions("tool"))
        assert len(versions) == 3

    def test_get_latest_version(self, temp_repo):
        vm = VersionManager(temp_repo)
        for v in ["1.0.0", "1.1.0", "2.0.0"]:
            desc = McpToolDescriptor(tool_identifier="tool", name="工具", version=v)
            run_coro(temp_repo.save(McpToolRegistration(descriptor=desc, registered_by="user")))
        latest = run_coro(vm.get_latest_version("tool"))
        assert latest.descriptor.version == "2.0.0"

    def test_check_version_exists(self, temp_repo):
        vm = VersionManager(temp_repo)
        desc = McpToolDescriptor(tool_identifier="tool", name="工具", version="1.0.0")
        run_coro(temp_repo.save(McpToolRegistration(descriptor=desc, registered_by="user")))
        assert run_coro(vm.check_version_exists("tool", "1.0.0")) is True
        assert run_coro(vm.check_version_exists("tool", "2.0.0")) is False


class TestPermissionChecker:
    """权限校验测试。"""

    def test_readonly_pass(self):
        checker = PermissionChecker()
        identity = CallerIdentity(caller_id="user", permissions=[ToolPermission.READONLY])
        tool = McpToolDescriptor(tool_identifier="t", name="t", permission=ToolPermission.READONLY)
        checker.check(identity, tool)

    def test_admin_required_insufficient(self):
        checker = PermissionChecker()
        identity = CallerIdentity(caller_id="user", permissions=[ToolPermission.READONLY])
        tool = McpToolDescriptor(tool_identifier="t", name="t", permission=ToolPermission.ADMIN)
        from orchestration_platform.domain.errors import MCP_TOOL_PERMISSION_DENIED, DomainError
        with pytest.raises(DomainError) as exc_info:
            checker.check(identity, tool)
        assert exc_info.value.code == MCP_TOOL_PERMISSION_DENIED

    def test_admin_pass(self):
        checker = PermissionChecker()
        identity = CallerIdentity(
            caller_id="admin",
            permissions=[ToolPermission.READONLY, ToolPermission.READWRITE, ToolPermission.ADMIN],
        )
        tool = McpToolDescriptor(tool_identifier="t", name="t", permission=ToolPermission.ADMIN)
        checker.check(identity, tool)

    def test_batch_check(self):
        checker = PermissionChecker()
        identity = CallerIdentity(caller_id="user", permissions=[ToolPermission.READONLY])
        tools = [
            McpToolDescriptor(tool_identifier="t1", name="t1", permission=ToolPermission.READONLY),
            McpToolDescriptor(tool_identifier="t2", name="t2", permission=ToolPermission.READWRITE),
        ]
        accessible = checker.check_batch(identity, tools)
        assert len(accessible) == 1
        assert accessible[0].tool_identifier == "t1"
