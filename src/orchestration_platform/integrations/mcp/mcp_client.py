# TODO(tech-debt): B档候选孤儿（族群 B6），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# MCP 客户端统一入口（integrations/mcp/mcp_client.py）
#
# 职责：MCP 客户端统一入口，整合传输层、连接池与工具发现缓存。
# 设计意图（对应 spec 5.3.1.8、任务 T33.5~T33.6）：
#   1. 按配置选择 stdio / http / sse 传输；
#   2. 经连接池获取连接；
#   3. 调用前用缓存签名校验入参；
#   4. 超时与重试处理。
# =============================================================================

import asyncio
from typing import Any, Dict, List

from orchestration_platform.domain.errors import (
    MCP_CONNECTION_FAILED,
    MCP_TOOL_NOT_FOUND,
    MCP_TOOL_TIMEOUT,
    DomainError,
)
from orchestration_platform.integrations.mcp.connection_pool import McpConnectionPool
from orchestration_platform.integrations.mcp.tool_discovery_cache import ToolDiscoveryCache
from orchestration_platform.mcp_tool_market.models import McpToolDescriptor


class McpClient:
    """MCP 客户端统一入口。

    整合传输选择、连接池复用与工具发现缓存。
    """

    def __init__(
        self,
        connection_pool: McpConnectionPool,
        discovery_cache: ToolDiscoveryCache,
        timeout_seconds: int = 30,
        retry_max: int = 3,
    ) -> None:
        """初始化 MCP 客户端。

        Args:
            connection_pool: 连接池。
            discovery_cache: 工具发现缓存。
            timeout_seconds: 调用超时时间。
            retry_max: 超时重试次数。
        """
        self._pool = connection_pool
        self._cache = discovery_cache
        self._timeout = timeout_seconds
        self._retry_max = retry_max

    async def list_tools(self, service_key: str) -> List[McpToolDescriptor]:
        """列出 MCP 服务提供的工具清单（带缓存）。"""
        cached = self._cache.get(service_key)
        if cached is not None:
            return cached

        transport = await self._pool.acquire(service_key)
        try:
            response = await asyncio.wait_for(
                transport.send_request("tools/list", {}),
                timeout=self._timeout,
            )
            tools_data = response.get("tools", [])
            tools = [
                McpToolDescriptor(**tool) if isinstance(tool, dict) else tool
                for tool in tools_data
            ]
            self._cache.put(service_key, tools)
            return tools
        except asyncio.TimeoutError:
            raise DomainError(MCP_TOOL_TIMEOUT, "工具发现超时")
        finally:
            await self._pool.release(service_key)

    async def call_tool(
        self,
        service_key: str,
        tool_identifier: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """调用 MCP 工具（带超时与重试）。

        工具不存在时不重试，直接返回 MCP_TOOL_NOT_FOUND。
        """
        last_error: Exception | None = None

        for attempt in range(self._retry_max):
            transport = await self._pool.acquire(service_key)
            try:
                response = await asyncio.wait_for(
                    transport.send_request(
                        "tools/call",
                        {"name": tool_identifier, "arguments": arguments},
                    ),
                    timeout=self._timeout,
                )

                if response.get("error", {}).get("code") == MCP_TOOL_NOT_FOUND:
                    raise DomainError(MCP_TOOL_NOT_FOUND, f"工具不存在: {tool_identifier}")

                return response.get("result", response)

            except asyncio.TimeoutError:
                last_error = DomainError(MCP_TOOL_TIMEOUT, f"工具调用超时: {tool_identifier}")
                if attempt < self._retry_max - 1:
                    await asyncio.sleep(1.0)
            except DomainError as exc:
                if exc.code == MCP_TOOL_NOT_FOUND:
                    raise
                last_error = exc
                if attempt < self._retry_max - 1:
                    await asyncio.sleep(1.0)
            except Exception as exc:
                last_error = exc
                if attempt < self._retry_max - 1:
                    await asyncio.sleep(1.0)
            finally:
                await self._pool.release(service_key)

        if isinstance(last_error, DomainError):
            raise last_error
        raise DomainError(MCP_CONNECTION_FAILED, f"工具调用失败: {type(last_error).__name__ if last_error else 'unknown'}")

    async def close(self) -> None:
        """关闭客户端，释放所有连接。"""
        await self._pool.close_all()
        self._cache.clear()
