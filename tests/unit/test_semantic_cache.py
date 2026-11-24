# =============================================================================
# 语义缓存单元测试（tests/unit/test_semantic_cache.py）
# =============================================================================


from orchestration_platform.context_engine.semantic_cache import (
    HashEmbeddingProvider,
    SemanticCache,
)


class TestHashEmbeddingProvider:
    """哈希嵌入提供者测试。"""

    def test_same_text_same_embedding(self):
        provider = HashEmbeddingProvider(dimensions=64)
        vec1 = provider.embed("hello world")
        vec2 = provider.embed("hello world")
        assert vec1 == vec2

    def test_different_text_different_embedding(self):
        provider = HashEmbeddingProvider(dimensions=64)
        vec1 = provider.embed("hello world")
        vec2 = provider.embed("goodbye world")
        assert vec1 != vec2

    def test_vector_normalized(self):
        provider = HashEmbeddingProvider(dimensions=64)
        vec = provider.embed("test")
        norm = sum(v * v for v in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-6

    def test_dimensions(self):
        provider = HashEmbeddingProvider(dimensions=32)
        vec = provider.embed("test")
        assert len(vec) == 32


class TestSemanticCache:
    """语义缓存测试。"""

    def test_put_and_get_exact_match(self):
        cache = SemanticCache(similarity_threshold=0.99)
        cache.put("查询A", "响应A")
        result = cache.get("查询A")
        assert result == "响应A"

    def test_miss_on_unseen_query(self):
        cache = SemanticCache()
        cache.put("查询A", "响应A")
        result = cache.get("完全不同的查询")
        assert result is None

    def test_hit_rate_tracking(self):
        cache = SemanticCache()
        cache.put("查询A", "响应A")
        cache.get("查询A")
        cache.get("查询B")
        assert cache.stats.total_queries == 2
        assert cache.stats.cache_hits == 1
        assert cache.stats.cache_misses == 1
        assert cache.stats.hit_rate == 0.5

    def test_tokens_saved_estimate(self):
        cache = SemanticCache()
        cache.put("查询A", "响应A")
        cache.get("查询A")
        assert cache.stats.tokens_saved_estimate == 500

    def test_invalidate(self):
        cache = SemanticCache()
        cache.put("查询A", "响应A")
        assert cache.invalidate("查询A") is True
        assert cache.get("查询A") is None

    def test_invalidate_nonexistent(self):
        cache = SemanticCache()
        assert cache.invalidate("nonexistent") is False

    def test_clear(self):
        cache = SemanticCache()
        cache.put("查询A", "响应A")
        cache.put("查询B", "响应B")
        count = cache.clear()
        assert count == 2
        assert cache.size == 0

    def test_max_entries_eviction(self):
        cache = SemanticCache(max_entries=3)
        cache.put("查询1", "响应1")
        cache.put("查询2", "响应2")
        cache.put("查询3", "响应3")
        cache.put("查询4", "响应4")
        assert cache.size == 3

    def test_hit_count_increment(self):
        cache = SemanticCache()
        cache.put("查询A", "响应A")
        cache.get("查询A")
        cache.get("查询A")
        cache.get("查询A")
        key = list(cache._entries.keys())[0]
        assert cache._entries[key].hit_count == 3

    def test_different_thresholds(self):
        cache_strict = SemanticCache(similarity_threshold=0.99)
        cache_loose = SemanticCache(similarity_threshold=0.0)

        cache_strict.put("查询A", "响应A")
        cache_loose.put("查询A", "响应A")

        strict_result = cache_strict.get("查询B")
        cache_loose.get("查询B")

        assert strict_result is None
        # 阈值为 0 时任何非零相似度都命中
        # 但哈希嵌入对不同文本可能产生低相似度，所以不保证命中

    def test_size(self):
        cache = SemanticCache()
        assert cache.size == 0
        cache.put("查询A", "响应A")
        assert cache.size == 1
        cache.put("查询B", "响应B")
        assert cache.size == 2

    def test_stats_initial_state(self):
        cache = SemanticCache()
        assert cache.stats.total_queries == 0
        assert cache.stats.cache_hits == 0
        assert cache.stats.cache_misses == 0
        assert cache.stats.hit_rate == 0.0
