# =============================================================================
# 缓存管理器（governance/cache/cache_manager.py）
#
# 职责：根据配置选择缓存后端，提供统一访问入口。
# 设计意图（对应偏差修正 spec 5.4 / design 模块D CacheManager）：
#   - redis_enabled=True 且驱动可用→RedisCacheBackend
#   - 否则→InMemoryCacheBackend（降级）
# =============================================================================

import logging
from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.cache.abstractions.cache_backend import (
    ICacheBackend,
    IDistributedLock,
    IRateLimiter,
)
from orchestration_platform.governance.cache.in_memory_cache_backend import (
    InMemoryCacheBackend,
    InMemoryDistributedLock,
    InMemoryRateLimiter,
)

_logger = logging.getLogger(__name__)


class CacheManager:
    """缓存管理器。

    根据配置选择 Redis 或内存后端，提供缓存、锁、限流统一访问入口。
    """

    def __init__(self) -> None:
        """初始化缓存管理器，根据配置选择后端。"""
        settings = get_settings()
        self._is_degraded = True

        if settings.redis_enabled and settings.redis_url:
            try:
                from orchestration_platform.governance.cache.redis_cache_backend import (
                    RedisCacheBackend,
                    RedisDistributedLock,
                    RedisRateLimiter,
                )
                self._cache_backend = RedisCacheBackend(settings.redis_url)
                self._lock_backend = RedisDistributedLock(settings.redis_url)
                self._rate_limiter = RedisRateLimiter(settings.redis_url)
                self._is_degraded = False
                _logger.info("缓存管理器初始化: Redis 后端")
            except ImportError:
                self._init_degraded()
        else:
            self._init_degraded()

    def _init_degraded(self) -> None:
        """初始化降级内存后端。"""
        self._cache_backend = InMemoryCacheBackend()
        self._lock_backend = InMemoryDistributedLock()
        self._rate_limiter = InMemoryRateLimiter()
        self._is_degraded = True
        _logger.warning("缓存管理器初始化: 内存降级模式（REDIS_DEGRADED）")

    @property
    def is_degraded(self) -> bool:
        """是否处于降级模式。"""
        return self._is_degraded

    def get_backend(self) -> ICacheBackend:
        """获取缓存后端。"""
        return self._cache_backend

    def get_lock(self) -> IDistributedLock:
        """获取分布式锁。"""
        return self._lock_backend

    def get_rate_limiter(self) -> IRateLimiter:
        """获取限流计数器。"""
        return self._rate_limiter
