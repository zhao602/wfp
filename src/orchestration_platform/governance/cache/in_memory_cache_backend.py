# =============================================================================
# 内存缓存后端实现（governance/cache/in_memory_cache_backend.py）
#
# 职责：进程内缓存、锁、限流计数器实现，用于降级模式。
# 设计意图（对应偏差修正 spec 5.4.1.4 / design 模块D）：
#   - 进程内 Dict + TTL 过期清理
#   - threading.Lock 实现本地锁（仅单实例有效）
#   - 进程内计数器 + 窗口过期重置
# =============================================================================

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from orchestration_platform.governance.cache.abstractions.cache_backend import (
    ICacheBackend,
    IDistributedLock,
    IRateLimiter,
)
from orchestration_platform.governance.cache.models import RateLimitResult

_logger = logging.getLogger(__name__)


class InMemoryCacheBackend:
    """内存缓存后端实现（降级模式）。

    进程内 Dict + TTL 过期清理，仅单实例有效。
    """

    def __init__(self) -> None:
        self._store: Dict[str, tuple[bytes, float]] = {}

    async def get(self, namespace: str, key: str) -> Optional[bytes]:
        full_key = f"{namespace}:{key}"
        entry = self._store.get(full_key)
        if entry is None:
            return None
        value, expire_at = entry
        if time.time() > expire_at:
            del self._store[full_key]
            return None
        return value

    async def set(
        self,
        namespace: str,
        key: str,
        value: bytes,
        ttl_seconds: int,
    ) -> bool:
        full_key = f"{namespace}:{key}"
        expire_at = time.time() + ttl_seconds
        self._store[full_key] = (value, expire_at)
        return True

    async def delete(self, namespace: str, key: str) -> bool:
        full_key = f"{namespace}:{key}"
        if full_key in self._store:
            del self._store[full_key]
            return True
        return False

    async def exists(self, namespace: str, key: str) -> bool:
        full_key = f"{namespace}:{key}"
        entry = self._store.get(full_key)
        if entry is None:
            return False
        _, expire_at = entry
        if time.time() > expire_at:
            del self._store[full_key]
            return False
        return True


class InMemoryDistributedLock:
    """内存分布式锁实现（降级模式）。

    使用 threading.Lock + 过期时间，仅单实例有效。
    """

    def __init__(self) -> None:
        self._locks: Dict[str, tuple[str, float]] = {}
        self._mutex = threading.Lock()

    async def acquire(
        self,
        lock_key: str,
        holder_id: str,
        ttl_seconds: int,
    ) -> bool:
        with self._mutex:
            entry = self._locks.get(lock_key)
            if entry is not None:
                current_holder, expire_at = entry
                if time.time() < expire_at:
                    return False
            self._locks[lock_key] = (holder_id, time.time() + ttl_seconds)
            return True

    async def release(
        self,
        lock_key: str,
        holder_id: str,
    ) -> bool:
        with self._mutex:
            entry = self._locks.get(lock_key)
            if entry is None:
                return False
            current_holder, _ = entry
            if current_holder != holder_id:
                return False
            del self._locks[lock_key]
            return True

    async def renew(
        self,
        lock_key: str,
        holder_id: str,
        ttl_seconds: int,
    ) -> bool:
        with self._mutex:
            entry = self._locks.get(lock_key)
            if entry is None:
                return False
            current_holder, _ = entry
            if current_holder != holder_id:
                return False
            self._locks[lock_key] = (holder_id, time.time() + ttl_seconds)
            return True


class InMemoryRateLimiter:
    """内存限流计数器实现（降级模式）。"""

    def __init__(self) -> None:
        self._counters: Dict[str, tuple[int, float]] = {}

    async def check_and_incr(
        self,
        counter_key: str,
        window_seconds: int,
        threshold: int,
    ) -> RateLimitResult:
        now = time.time()
        entry = self._counters.get(counter_key)
        if entry is not None:
            count, window_start = entry
            if now - window_start >= window_seconds:
                self._counters[counter_key] = (1, now)
                count = 1
            else:
                count += 1
                self._counters[counter_key] = (count, window_start)
        else:
            count = 1
            self._counters[counter_key] = (1, now)

        allowed = count <= threshold
        reset_at = datetime.now(timezone.utc) + timedelta(seconds=window_seconds)
        return RateLimitResult(
            allowed=allowed,
            current_count=count,
            threshold=threshold,
            reset_at=reset_at,
        )
