# =============================================================================
# MCP 连接池（integrations/mcp/connection_pool.py）
#
# 职责：管理 MCP 传输连接的复用、自动重连与泄漏检测。
# 设计意图（对应 spec 5.3.1.6、任务 T33.3）：
#   1. 连接复用——同一 MCP 服务复用同一连接；
#   2. 自动重连——连接断开按重试策略重连；
#   3. 连接泄漏检测——维护借出归还记录，超时未归还标记泄漏；
#   4. 连接数上限——耗尽时返回 MCP_CONNECTION_POOL_EXHAUSTED。
# =============================================================================

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from orchestration_platform.domain.errors import (
    MCP_CONNECTION_FAILED,
    MCP_CONNECTION_POOL_EXHAUSTED,
    DomainError,
)
from orchestration_platform.integrations.mcp.transports.base import IMcpTransport


@dataclass
class PooledConnection:
    """连接池中的连接条目。"""
    transport: IMcpTransport
    service_key: str
    borrowed_at: float = 0.0
    in_use: bool = False


class McpConnectionPool:
    """MCP 连接池。

    管理多个 MCP 服务的传输连接，支持复用、重连与泄漏检测。
    """

    def __init__(
        self,
        max_connections: int = 10,
        leak_detection_timeout: float = 60.0,
        retry_max: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        """初始化连接池。

        Args:
            max_connections: 最大连接数。
            leak_detection_timeout: 连接泄漏检测超时（秒）。
            retry_max: 连接失败最大重试次数。
            retry_delay: 重试间隔（秒）。
        """
        self._max = max_connections
        self._leak_timeout = leak_detection_timeout
        self._retry_max = retry_max
        self._retry_delay = retry_delay
        self._pool: Dict[str, PooledConnection] = {}
        self._factory: Any = None

    def set_transport_factory(self, factory: Any) -> None:
        """设置传输连接工厂。

        factory 签名: def create(service_key: str) -> IMcpTransport
        """
        self._factory = factory

    async def acquire(self, service_key: str) -> IMcpTransport:
        """获取连接（复用或新建）。

        Args:
            service_key: 服务标识（如 URL 或命令）。

        Returns:
            传输连接。

        Raises:
            DomainError: 连接池耗尽或连接失败。
        """
        # 检查已有连接是否可复用
        existing = self._pool.get(service_key)
        if existing and not existing.in_use and existing.transport.is_connected:
            existing.in_use = True
            existing.borrowed_at = time.monotonic()
            return existing.transport

        # 检查连接数上限
        if len(self._pool) >= self._max:
            # 尝试回收空闲连接
            self._reclaim_idle()
            if len(self._pool) >= self._max:
                raise DomainError(MCP_CONNECTION_POOL_EXHAUSTED, "连接池已耗尽")

        # 创建新连接（带重试）
        if self._factory is None:
            raise DomainError(MCP_CONNECTION_FAILED, "传输工厂未设置")

        transport = self._factory(service_key)
        last_error = None
        for attempt in range(self._retry_max):
            try:
                await transport.connect()
                entry = PooledConnection(
                    transport=transport,
                    service_key=service_key,
                    in_use=True,
                    borrowed_at=time.monotonic(),
                )
                self._pool[service_key] = entry
                return transport
            except Exception as exc:
                last_error = exc
                if attempt < self._retry_max - 1:
                    await asyncio.sleep(self._retry_delay)

        raise DomainError(MCP_CONNECTION_FAILED, f"连接失败: {type(last_error).__name__}")

    async def release(self, service_key: str) -> None:
        """归还连接。"""
        entry = self._pool.get(service_key)
        if entry:
            entry.in_use = False

    def detect_leaks(self) -> list[str]:
        """检测泄漏连接，返回泄漏的服务标识列表。"""
        now = time.monotonic()
        leaked = []
        for key, entry in self._pool.items():
            if entry.in_use and (now - entry.borrowed_at) > self._leak_timeout:
                leaked.append(key)
        return leaked

    def _reclaim_idle(self) -> None:
        """回收空闲且断开的连接。"""
        to_remove = [
            key for key, entry in self._pool.items()
            if not entry.in_use and not entry.transport.is_connected
        ]
        for key in to_remove:
            del self._pool[key]

    async def close_all(self) -> None:
        """关闭所有连接。"""
        for entry in list(self._pool.values()):
            try:
                await entry.transport.disconnect()
            except Exception:
                pass
        self._pool.clear()

    @property
    def size(self) -> int:
        """当前连接数。"""
        return len(self._pool)

    @property
    def active_count(self) -> int:
        """使用中的连接数。"""
        return sum(1 for e in self._pool.values() if e.in_use)
