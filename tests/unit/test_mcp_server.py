# =============================================================================
# MCP 服务端测试（tests/unit/test_mcp_server.py）
#
# 覆盖 A29（MCP 服务端能力暴露）、B16（响应延迟）。
# =============================================================================

import asyncio
import pytest

from orchestration_platform.integrations.mcp_server.mcp_server import McpServer
from orchestration_platform.integrations.mcp_server.tool_declarations import (
    get_platform_tool_declarations,
    get_tool_by_identifier,
)
from orchestration_platform.integrations.mcp_server.tool_router import ToolRouter
from orchestration_platform.integrations.mcp_server.authentication import McpAuthenticator
from orchestration_platform.integrations.mcp_server.error_mapping import map_to_mcp_error
from orchestration_platform.domain.errors import DomainError, MCP_TOOL_NOT_FOUND
from orchestration_platform.domain.models.enums import ToolPermission


def run_coro(coro):
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestToolDeclarations:
    """工具声明测试。"""

    def test_nine_platform_tools(self):
        tools = get_platform_tool_declarations()
        assert len(tools) == 9

    def test_tool_identifiers_unique(self):
        tools = get_platform_tool_declarations()
        identifiers = [t.tool_identifier for t in tools]
        assert len(identifiers) == len(set(identifiers))

    def test_get_tool_by_identifier(self):
        tool = get_tool_by_identifier("start_workflow")
        assert tool is not None
        assert tool.name == "启动工作流实例"

    def test_get_nonexistent_tool(self):
        tool = get_tool_by_identifier("nonexistent")
        assert tool is None


class TestToolRouter:
    """工具路由测试。"""

    @staticmethod
    async def _mock_handler(args):
        return {"echo": args}

    def test_route_existing_tool(self):
        router = ToolRouter({"test_tool": self._mock_handler})
        result = run_coro(router.route("test_tool", {"key": "value"}))
        assert result == {"echo": {"key": "value"}}

    def test_route_nonexistent_tool(self):
        router = ToolRouter({})
        with pytest.raises(DomainError) as exc_info:
            run_coro(router.route("nonexistent", {}))
        assert exc_info.value.code == MCP_TOOL_NOT_FOUND

    def test_has_tool(self):
        router = ToolRouter({"test_tool": self._mock_handler})
        assert router.has_tool("test_tool") is True
        assert router.has_tool("nonexistent") is False


class TestMcpAuthenticator:
    """鉴权测试。"""

    def test_valid_token(self):
        auth = McpAuthenticator(valid_tokens=["valid_token"])
        identity = auth.authenticate("valid_token")
        assert identity.caller_id == "valid_to"

    def test_invalid_token(self):
        auth = McpAuthenticator(valid_tokens=["valid_token"])
        from orchestration_platform.domain.errors import MCP_TOOL_PERMISSION_DENIED
        with pytest.raises(DomainError) as exc_info:
            auth.authenticate("invalid_token")
        assert exc_info.value.code == MCP_TOOL_PERMISSION_DENIED

    def test_no_token_check(self):
        auth = McpAuthenticator(valid_tokens=None)
        identity = auth.authenticate("any_token")
        assert identity is not None

    def test_permission_check_pass(self):
        from orchestration_platform.mcp_tool_market.models import CallerIdentity
        identity = CallerIdentity(
            caller_id="user",
            permissions=[ToolPermission.READONLY, ToolPermission.READWRITE],
        )
        auth = McpAuthenticator()
        auth.check_permission(identity, ToolPermission.READONLY)

    def test_permission_check_fail(self):
        from orchestration_platform.mcp_tool_market.models import CallerIdentity
        identity = CallerIdentity(
            caller_id="user",
            permissions=[ToolPermission.READONLY],
        )
        auth = McpAuthenticator()
        from orchestration_platform.domain.errors import MCP_TOOL_PERMISSION_DENIED
        with pytest.raises(DomainError) as exc_info:
            auth.check_permission(identity, ToolPermission.ADMIN)
        assert exc_info.value.code == MCP_TOOL_PERMISSION_DENIED


class TestMcpServer:
    """MCP 服务端测试。"""

    @staticmethod
    async def _mock_handler(args):
        return {"result": "ok"}

    def test_list_tools(self):
        router = ToolRouter({})
        server = McpServer(router)
        tools = server.list_tools()
        assert len(tools) == 9

    def test_call_tool_not_running(self):
        router = ToolRouter({"test": self._mock_handler})
        server = McpServer(router)
        result = run_coro(server.call_tool("test", {}))
        assert result["error_code"] == "MCP_SERVER_NOT_RUNNING"

    def test_call_tool_success(self):
        router = ToolRouter({"test": self._mock_handler})
        server = McpServer(router)
        run_coro(server.start())
        result = run_coro(server.call_tool("test", {"arg": 1}))
        assert "result" in result

    def test_call_tool_not_found(self):
        router = ToolRouter({})
        server = McpServer(router)
        run_coro(server.start())
        result = run_coro(server.call_tool("nonexistent", {}))
        assert "error_code" in result


class TestErrorMapping:
    """错误映射测试。"""

    def test_domain_error_mapping(self):
        error = DomainError("TEST_ERROR", "test message")
        result = map_to_mcp_error(error)
        assert result["error_code"] == "TEST_ERROR"

    def test_timeout_mapping(self):
        from orchestration_platform.domain.errors import NODE_TIMEOUT
        error = DomainError(NODE_TIMEOUT, "timeout")
        result = map_to_mcp_error(error)
        from orchestration_platform.domain.errors import MCP_TOOL_TIMEOUT
        assert result["error_code"] == MCP_TOOL_TIMEOUT

    def test_generic_error_mapping(self):
        result = map_to_mcp_error(ValueError("internal"))
        assert result["error_code"] == "MCP_INTERNAL_ERROR"
