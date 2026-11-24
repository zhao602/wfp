# =============================================================================
# MCP 工具发现缓存（integrations/mcp/tool_discovery_cache.py）
#
# 职责：缓存 MCP 服务的工具清单，减少重复发现调用。
# 设计意图（对应 spec 5.3.1.7、任务 T33.4）：
#   1. 首次 list_tools 缓存工具清单；
#   2. 缓存有效期内返回缓存结果；
#   3. 过期后重新发现。
# =============================================================================

import time
from typing import Dict, List, Optional

from orchestration_platform.mcp_tool_market.models import McpToolDescriptor


class ToolDiscoveryCache:
    """MCP 工具发现缓存。

    按服务标识缓存工具清单，支持 TTL 过期刷新。
    """

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        """初始化工具发现缓存。

        Args:
            ttl_seconds: 缓存存活时间（默认 300 秒）。
        """
        self._ttl = ttl_seconds
        self._cache: Dict[str, tuple[float, List[McpToolDescriptor]]] = {}

    def get(self, service_key: str) -> Optional[List[McpToolDescriptor]]:
        """获取缓存的工具清单。

        Returns:
            缓存的工具清单，若缓存不存在或已过期则返回 None。
        """
        entry = self._cache.get(service_key)
        if entry is None:
            return None
        cached_at, tools = entry
        if (time.monotonic() - cached_at) > self._ttl:
            del self._cache[service_key]
            return None
        return tools

    def put(self, service_key: str, tools: List[McpToolDescriptor]) -> None:
        """存入工具清单缓存。"""
        self._cache[service_key] = (time.monotonic(), tools)

    def invalidate(self, service_key: str) -> bool:
        """使指定服务的缓存失效。"""
        return self._cache.pop(service_key, None) is not None

    def clear(self) -> int:
        """清空所有缓存，返回清除条数。"""
        count = len(self._cache)
        self._cache.clear()
        return count

    @property
    def size(self) -> int:
        """当前缓存条数。"""
        return len(self._cache)
