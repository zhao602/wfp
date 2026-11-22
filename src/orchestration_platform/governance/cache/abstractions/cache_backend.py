# =============================================================================
# 缓存后端抽象接口（governance/cache/abstractions/cache_backend.py）
#
# 职责：定义缓存后端、分布式锁、限流计数器的统一契约。
# 设计意图（对应 design 2.2.2.4）：
#   - ICacheBackend：get/set/delete/exists
#   - IDistributedLock：acquire/release/renew
#   - IRateLimiter：check_and_incr
# =============================================================================

from typing import Optional, Protocol, runtime_checkable

from orchestration_platform.governance.cache.models import RateLimitResult


@runtime_checkable
class ICacheBackend(Protocol):
    """缓存后端抽象接口。"""

    async def get(self, namespace: str, key: str) -> Optional[bytes]:
        """获取缓存值。返回 None 表示未命中。"""
        ...

    async def set(
        self,
        namespace: str,
        key: str,
        value: bytes,
        ttl_seconds: int,
    ) -> bool:
        """设置缓存值，带 TTL。"""
        ...

    async def delete(self, namespace: str, key: str) -> bool:
        """删除缓存键。"""
        ...

    async def exists(self, namespace: str, key: str) -> bool:
        """检查键是否存在。"""
        ...


@runtime_checkable
class IDistributedLock(Protocol):
    """分布式锁抽象接口。"""

    async def acquire(
        self,
        lock_key: str,
        holder_id: str,
        ttl_seconds: int,
    ) -> bool:
        """尝试获取分布式锁。返回 True 表示获取成功。"""
        ...

    async def release(
        self,
        lock_key: str,
        holder_id: str,
    ) -> bool:
        """释放锁。仅持有者可释放。"""
        ...

    async def renew(
        self,
        lock_key: str,
        holder_id: str,
        ttl_seconds: int,
    ) -> bool:
        """续期锁。仅持有者可续期。"""
        ...


@runtime_checkable
class IRateLimiter(Protocol):
    """限流计数器抽象接口。"""

    async def check_and_incr(
        self,
        counter_key: str,
        window_seconds: int,
        threshold: int,
    ) -> RateLimitResult:
        """检查并递增计数器。返回是否超限及当前计数。"""
        ...
