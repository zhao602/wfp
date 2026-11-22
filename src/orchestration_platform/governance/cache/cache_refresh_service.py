# TODO(tech-debt): B档候选孤儿（族群 B4），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 缓存一致性刷新服务（governance/cache/cache_refresh_service.py）
# =============================================================================

import logging
from typing import Optional

from orchestration_platform.governance.cache.abstractions.cache_backend import (
    ICacheBackend,
)

_logger = logging.getLogger(__name__)


class CacheRefreshService:
    """缓存一致性刷新服务。

    PostgreSQL 写入完成后异步刷新对应 Redis 缓存键。
    """

    def __init__(self, cache_backend: ICacheBackend) -> None:
        self._backend = cache_backend

    async def refresh_on_write(
        self,
        namespace: str,
        key: str,
        value: bytes,
        ttl_seconds: int = 300,
    ) -> bool:
        """写入完成后异步刷新缓存键。

        刷新失败时记录告警，不影响写入结果。
        """
        try:
            return await self._backend.set(namespace, key, value, ttl_seconds)
        except Exception as exc:
            _logger.warning("缓存刷新失败: %s:%s - %s", namespace, key, exc)
            return False

    async def invalidate(self, namespace: str, key: str) -> bool:
        """失效缓存键。"""
        try:
            return await self._backend.delete(namespace, key)
        except Exception as exc:
            _logger.warning("缓存失效失败: %s:%s - %s", namespace, key, exc)
            return False
