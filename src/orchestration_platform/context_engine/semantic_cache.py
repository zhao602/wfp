# =============================================================================
# 语义缓存（context_engine/semantic_cache.py）
#
# 职责：基于嵌入向量的相似查询缓存，减少重复 LLM 调用。
# 设计意图（对应项目构建计划书「语义缓存」要求）：
#   1. 查询经嵌入向量化后与缓存条目做余弦相似度比较；
#   2. 相似度超过阈值时直接返回缓存响应，预期节省 60-95% Token；
#   3. 支持 TTL 过期、最大条数限制与统计指标；
#   4. 嵌入向量计算由可注入的 IEmbeddingProvider 提供，无外部依赖时降级为哈希匹配。
# =============================================================================

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class IEmbeddingProvider(Protocol):
    """嵌入向量提供者接口。"""

    def embed(self, text: str) -> List[float]:
        """将文本转换为嵌入向量。"""
        ...


class HashEmbeddingProvider:
    """基于哈希的嵌入提供者（无外部依赖的降级实现）。

    将文本 SHA256 哈希映射到固定维度的向量空间。
    相同文本产生相同向量，不同文本产生近似随机向量。
    """

    def __init__(self, dimensions: int = 128) -> None:
        self._dimensions = dimensions

    def embed(self, text: str) -> List[float]:
        """将文本哈希映射为固定维度向量。"""
        hash_bytes = hashlib.sha256(text.encode("utf-8")).digest()
        vector = []
        for i in range(self._dimensions):
            byte_idx = i % len(hash_bytes)
            value = (hash_bytes[byte_idx] / 255.0) * 2 - 1
            vector.append(value)
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]
        return vector


@dataclass
class CacheEntry:
    """缓存条目。"""

    query: str
    response: Any
    embedding: List[float]
    created_at: float
    hit_count: int = 0


@dataclass
class CacheStats:
    """缓存统计指标。"""

    total_queries: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    evictions: int = 0

    @property
    def hit_rate(self) -> float:
        """命中率。"""
        if self.total_queries == 0:
            return 0.0
        return self.cache_hits / self.total_queries

    @property
    def tokens_saved_estimate(self) -> int:
        """估算节省的 Token 数（每次命中节省约 500 Token）。"""
        return self.cache_hits * 500


class SemanticCache:
    """语义缓存：基于嵌入向量相似度的查询缓存。

    构造参数：
        similarity_threshold —— 相似度阈值（0-1），超过则命中缓存（默认 0.92）
        max_entries          —— 最大缓存条数（默认 1000）
        ttl_seconds          —— 缓存条目存活时间（默认 3600 秒）
        embedding_provider   —— 嵌入向量提供者（默认哈希实现）
    """

    def __init__(
        self,
        similarity_threshold: float = 0.92,
        max_entries: int = 1000,
        ttl_seconds: float = 3600.0,
        embedding_provider: Optional[IEmbeddingProvider] = None,
    ) -> None:
        self._threshold = similarity_threshold
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._provider: IEmbeddingProvider = embedding_provider or HashEmbeddingProvider()
        self._entries: Dict[str, CacheEntry] = {}
        self._stats = CacheStats()

    @property
    def stats(self) -> CacheStats:
        """获取缓存统计指标。"""
        return self._stats

    @property
    def size(self) -> int:
        """当前缓存条数。"""
        return len(self._entries)

    def get(self, query: str) -> Optional[Any]:
        """查询缓存。

        若找到相似度超过阈值的缓存条目则返回其响应，否则返回 None。
        过期条目在查询时惰性清除。
        """
        self._stats.total_queries += 1
        self._evict_expired()

        query_embedding = self._provider.embed(query)
        best_entry: Optional[CacheEntry] = None
        best_similarity = 0.0

        for entry in self._entries.values():
            if self._is_expired(entry):
                continue
            similarity = self._cosine_similarity(query_embedding, entry.embedding)
            if similarity > best_similarity:
                best_similarity = similarity
                best_entry = entry

        if best_entry is not None and best_similarity >= self._threshold:
            best_entry.hit_count += 1
            self._stats.cache_hits += 1
            return best_entry.response

        self._stats.cache_misses += 1
        return None

    def put(self, query: str, response: Any) -> None:
        """存入缓存条目。

        若缓存已满则淘汰最旧条目（LRU 语义）。
        """
        if len(self._entries) >= self._max_entries:
            self._evict_oldest()

        embedding = self._provider.embed(query)
        cache_key = self._make_key(query)
        self._entries[cache_key] = CacheEntry(
            query=query,
            response=response,
            embedding=embedding,
            created_at=time.monotonic(),
        )

    def invalidate(self, query: str) -> bool:
        """使指定查询的缓存条目失效。"""
        cache_key = self._make_key(query)
        return self._entries.pop(cache_key, None) is not None

    def clear(self) -> int:
        """清空缓存，返回清除的条数。"""
        count = len(self._entries)
        self._entries.clear()
        return count

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """计算两个向量的余弦相似度。"""
        if len(vec_a) != len(vec_b) or len(vec_a) == 0:
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot_product / (norm_a * norm_b)

    @staticmethod
    def _make_key(query: str) -> str:
        """生成缓存键。"""
        return hashlib.sha256(query.encode("utf-8")).hexdigest()

    def _is_expired(self, entry: CacheEntry) -> bool:
        """检查条目是否过期。"""
        return (time.monotonic() - entry.created_at) > self._ttl

    def _evict_expired(self) -> None:
        """惰性清除过期条目。"""
        expired_keys = [
            key for key, entry in self._entries.items()
            if self._is_expired(entry)
        ]
        for key in expired_keys:
            del self._entries[key]
            self._stats.evictions += 1

    def _evict_oldest(self) -> None:
        """淘汰最旧的缓存条目。"""
        if not self._entries:
            return
        oldest_key = min(self._entries, key=lambda k: self._entries[k].created_at)
        del self._entries[oldest_key]
        self._stats.evictions += 1
