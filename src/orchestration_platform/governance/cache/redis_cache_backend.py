# =============================================================================
# Redis 缓存后端实现（governance/cache/redis_cache_backend.py）
#
# 职责：通过 Redis 实现分布式缓存、分布式锁、限流计数器。
# 设计意图（对应偏差修正 spec 5.4 / design 模块D）：
#   - 延迟导入 redis.asyncio，ImportError 时由上层降级
#   - 分布式锁使用 SET NX EX
#   - 限流计数器使用 INCR + EXPIRE
# =============================================================================

import logging
from typing import Optional

from orchestration_platform.governance.cache.abstractions.cache_backend import (
    ICacheBackend,
    IDistributedLock,
    IRateLimiter,
)
from orchestration_platform.governance.cache.models import RateLimitResult

_logger = logging.getLogger(__name__)


class RedisCacheBackend:
    """Redis 缓存后端实现。

    延迟导入 redis.asyncio，连接不可用时抛出异常由上层降级。
    """

    def __init__(self, redis_url: str) -> None:
        """初始化 Redis 缓存后端。

        参数：
            redis_url：Redis 连接地址
        """
        self._redis_url = redis_url
        self._client = None

    def _get_client(self):
        """延迟导入并获取 Redis 客户端。"""
        if self._client is not None:
            return self._client
        try:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(self._redis_url)
            return self._client
        except ImportError:
            raise ImportError("redis 包未安装，请安装 redis 依赖")

    async def get(self, namespace: str, key: str) -> Optional[bytes]:
        """获取缓存值。"""
        client = self._get_client()
        full_key = f"{namespace}:{key}"
        return await client.get(full_key)

    async def set(
        self,
        namespace: str,
        key: str,
        value: bytes,
        ttl_seconds: int,
    ) -> bool:
        """设置缓存值，带 TTL。"""
        client = self._get_client()
        full_key = f"{namespace}:{key}"
        await client.set(full_key, value, ex=ttl_seconds)
        return True

    async def delete(self, namespace: str, key: str) -> bool:
        """删除缓存键。"""
        client = self._get_client()
        full_key = f"{namespace}:{key}"
        await client.delete(full_key)
        return True

    async def exists(self, namespace: str, key: str) -> bool:
        """检查键是否存在。"""
        client = self._get_client()
        full_key = f"{namespace}:{key}"
        return bool(await client.exists(full_key))


class RedisDistributedLock:
    """Redis 分布式锁实现。

    使用 SET NX EX 实现互斥，Lua 脚本校验持有者后删除。
    """

    _RELEASE_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        import redis.asyncio as aioredis
        self._client = aioredis.from_url(self._redis_url)
        return self._client

    async def acquire(
        self,
        lock_key: str,
        holder_id: str,
        ttl_seconds: int,
    ) -> bool:
        """尝试获取分布式锁。"""
        client = self._get_client()
        result = await client.set(lock_key, holder_id, nx=True, ex=ttl_seconds)
        return result is not None

    async def release(
        self,
        lock_key: str,
        holder_id: str,
    ) -> bool:
        """释放锁。仅持有者可释放。"""
        client = self._get_client()
        result = await client.eval(self._RELEASE_SCRIPT, 1, lock_key, holder_id)
        return bool(result)

    async def renew(
        self,
        lock_key: str,
        holder_id: str,
        ttl_seconds: int,
    ) -> bool:
        """续期锁。"""
        client = self._get_client()
        current = await client.get(lock_key)
        if current and current.decode() == holder_id:
            await client.expire(lock_key, ttl_seconds)
            return True
        return False


class RedisRateLimiter:
    """Redis 限流计数器实现。

    使用 INCR + EXPIRE 实现窗口限流。
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        import redis.asyncio as aioredis
        self._client = aioredis.from_url(self._redis_url)
        return self._client

    async def check_and_incr(
        self,
        counter_key: str,
        window_seconds: int,
        threshold: int,
    ) -> RateLimitResult:
        """检查并递增计数器。"""
        from datetime import datetime, timezone, timedelta
        client = self._get_client()
        current = await client.incr(counter_key)
        if current == 1:
            await client.expire(counter_key, window_seconds)
        allowed = current <= threshold
        reset_at = datetime.now(timezone.utc) + timedelta(seconds=window_seconds)
        return RateLimitResult(
            allowed=allowed,
            current_count=current,
            threshold=threshold,
            reset_at=reset_at,
        )
