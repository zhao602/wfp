# =============================================================================
# Redis 缓存集成测试（tests/governance/test_redis_cache_integration.py）
#
# 职责：验证缓存后端、分布式锁、限流计数器功能。
# 设计意图（对应偏差修正 T114 / 验收 A91-A95, B92-B94）：
#   1. 内存缓存后端：get/set/delete/exists + TTL
#   2. 内存分布式锁：acquire/release/renew
#   3. 内存限流计数器：窗口计数+超限拒绝
#   4. 降级模式：redis_enabled=False 时使用内存后端
#   5. 缓存管理器：根据配置选择后端
# =============================================================================

import asyncio
import unittest

from orchestration_platform.governance.cache.cache_manager import CacheManager
from orchestration_platform.governance.cache.in_memory_cache_backend import (
    InMemoryCacheBackend,
    InMemoryDistributedLock,
    InMemoryRateLimiter,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestInMemoryCacheBackend(unittest.TestCase):
    """内存缓存后端测试（验收 A91, A95）。"""

    def setUp(self) -> None:
        self.cache = InMemoryCacheBackend()

    def test_set_and_get(self) -> None:
        """设置并获取缓存。"""
        _run_async(self.cache.set("ns", "key1", b"value1", 60))
        result = _run_async(self.cache.get("ns", "key1"))
        self.assertEqual(result, b"value1")

    def test_get_missing_key(self) -> None:
        """获取不存在的键返回 None。"""
        result = _run_async(self.cache.get("ns", "missing"))
        self.assertIsNone(result)

    def test_delete(self) -> None:
        """删除缓存键。"""
        _run_async(self.cache.set("ns", "key1", b"value1", 60))
        success = _run_async(self.cache.delete("ns", "key1"))
        self.assertTrue(success)
        result = _run_async(self.cache.get("ns", "key1"))
        self.assertIsNone(result)

    def test_exists(self) -> None:
        """检查键是否存在。"""
        _run_async(self.cache.set("ns", "key1", b"value1", 60))
        self.assertTrue(_run_async(self.cache.exists("ns", "key1")))
        self.assertFalse(_run_async(self.cache.exists("ns", "missing")))

    def test_ttl_expiration(self) -> None:
        """TTL 过期后键不可获取。"""
        _run_async(self.cache.set("ns", "key1", b"value1", 0))
        import time
        time.sleep(0.1)
        result = _run_async(self.cache.get("ns", "key1"))
        self.assertIsNone(result)


class TestInMemoryDistributedLock(unittest.TestCase):
    """内存分布式锁测试（验收 A93）。"""

    def setUp(self) -> None:
        self.lock = InMemoryDistributedLock()

    def test_acquire_and_release(self) -> None:
        """获取并释放锁。"""
        acquired = _run_async(self.lock.acquire("lock1", "holder1", 30))
        self.assertTrue(acquired)
        released = _run_async(self.lock.release("lock1", "holder1"))
        self.assertTrue(released)

    def test_acquire_twice_fails(self) -> None:
        """重复获取锁失败。"""
        _run_async(self.lock.acquire("lock1", "holder1", 30))
        acquired = _run_async(self.lock.acquire("lock1", "holder2", 30))
        self.assertFalse(acquired)

    def test_release_by_non_holder_fails(self) -> None:
        """非持有者释放锁失败。"""
        _run_async(self.lock.acquire("lock1", "holder1", 30))
        released = _run_async(self.lock.release("lock1", "holder2"))
        self.assertFalse(released)

    def test_renew(self) -> None:
        """续期锁。"""
        _run_async(self.lock.acquire("lock1", "holder1", 30))
        renewed = _run_async(self.lock.renew("lock1", "holder1", 60))
        self.assertTrue(renewed)


class TestInMemoryRateLimiter(unittest.TestCase):
    """内存限流计数器测试（验收 A94）。"""

    def setUp(self) -> None:
        self.limiter = InMemoryRateLimiter()

    def test_under_threshold_allowed(self) -> None:
        """阈值内允许。"""
        for _ in range(5):
            result = _run_async(self.limiter.check_and_incr("counter1", 60, 10))
            self.assertTrue(result.allowed)

    def test_over_threshold_denied(self) -> None:
        """超阈值拒绝。"""
        for _ in range(10):
            _run_async(self.limiter.check_and_incr("counter2", 60, 5))
        result = _run_async(self.limiter.check_and_incr("counter2", 60, 5))
        self.assertFalse(result.allowed)


class TestCacheManager(unittest.TestCase):
    """缓存管理器测试（验收 A95, B92）。"""

    def test_degraded_mode(self) -> None:
        """未启用 Redis 时降级为内存后端。"""
        mgr = CacheManager()
        self.assertTrue(mgr.is_degraded)
        self.assertIsNotNone(mgr.get_backend())
        self.assertIsNotNone(mgr.get_lock())
        self.assertIsNotNone(mgr.get_rate_limiter())


if __name__ == "__main__":
    unittest.main()
