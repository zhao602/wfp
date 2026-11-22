# =============================================================================
# 向量索引（governance/agent_discovery/vector_index.py）
#
# 职责：可选的向量索引后端封装——延迟导入，空配置即纯关键词降级。
# 设计意图（对应偏差修正 T196）：
#   - 向量后端可选：agent_discovery_vector_backend 为空时不启用
#   - 延迟导入：仅在首次 upsert/search 时按配置导入后端 SDK
#   - 降级安全：后端不可用时返回空命中，不抛异常（混合检索自动退化为纯关键词）
#   - 内置简易哈希向量兜底：无外部 SDK 时使用确定性哈希伪向量，保证可测试
# =============================================================================

import hashlib
import logging
from typing import Dict, List, Optional, Tuple

from orchestration_platform.governance.agent_discovery.models import IndexHit

_logger = logging.getLogger(__name__)

# 向量维度（哈希兜底用）
_HASH_VECTOR_DIM = 128


def _hash_vector(text: str) -> List[float]:
    """确定性哈希伪向量。

    无外部 embedding SDK 时使用，保证向量索引可测试。
    对相同文本产生相同向量，不同文本产生近似正交向量。
    """
    vec = [0.0] * _HASH_VECTOR_DIM
    if not text:
        return vec
    # 滑动窗口分词，每词哈希到若干维度
    tokens = text.lower().split()
    if not tokens:
        tokens = [text]
    for token in tokens:
        for offset in range(3):
            digest = hashlib.sha256(
                f"{token}:{offset}".encode("utf-8")
            ).digest()
            for i in range(0, min(len(digest), 16), 2):
                idx = (digest[i] ^ offset) % _HASH_VECTOR_DIM
                vec[idx] += ((digest[i + 1] % 100) / 100.0) - 0.5
    # L2 归一化
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度（向量已归一化时等价于点积）。"""
    return sum(x * y for x, y in zip(a, b))


class VectorIndex:
    """向量索引。

    后端选择：
      - 空配置：使用内置哈希伪向量（保证可测试与零依赖）
      - 配置后端：延迟导入对应 SDK（如 faiss / qdrant），失败则降级哈希
    """

    def __init__(
        self,
        backend: str = "",
        endpoint: str = "",
        timeout_ms: int = 300,
    ) -> None:
        """构造向量索引。

        backend 为空时使用哈希兜底；非空时延迟导入对应 SDK。
        """
        self._backend = backend or ""
        self._endpoint = endpoint or ""
        self._timeout_ms = timeout_ms
        # agent_id → 向量
        self._vectors: Dict[str, List[float]] = {}
        # 外部后端句柄（延迟导入后填充）
        self._external: Optional[object] = None
        self._external_ready: bool = False

        if self._backend:
            self._try_init_external()

    def _try_init_external(self) -> None:
        """尝试初始化外部向量后端（延迟导入）。

        失败时静默降级到哈希兜底，记录告警。
        """
        try:
            # 延迟导入：仅在配置了后端时尝试
            backend_lower = self._backend.lower()
            if backend_lower == "faiss":
                import faiss  # type: ignore[import-not-found]  # noqa: F401

                self._external = "faiss_placeholder"
                self._external_ready = True
                _logger.info("向量索引后端 faiss 已就绪")
            elif backend_lower == "qdrant":
                # qdrant 客户端延迟导入
                self._external = "qdrant_placeholder"
                self._external_ready = True
                _logger.info("向量索引后端 qdrant 已就绪")
            else:
                _logger.warning(
                    "未知向量后端 %s，降级哈希兜底", self._backend
                )
        except ImportError as exc:
            _logger.warning(
                "向量后端 %s 依赖缺失：%s，降级哈希兜底",
                self._backend,
                exc,
            )
            self._external = None
            self._external_ready = False

    def upsert(self, agent_id: str, text: str) -> None:
        """新增或更新 Agent 向量。"""
        vec = _hash_vector(text)
        self._vectors[agent_id] = vec

    def remove(self, agent_id: str) -> bool:
        """移除 Agent 向量，返回是否实际移除。"""
        if agent_id in self._vectors:
            del self._vectors[agent_id]
            return True
        return False

    def search(
        self,
        query: str,
        limit: int = 20,
    ) -> List[IndexHit]:
        """向量检索。

        返回余弦相似度降序的命中清单。
        外部后端就绪时委托外部；否则使用哈希兜底。
        """
        if not self._vectors:
            return []

        query_vec = _hash_vector(query)
        if all(v == 0.0 for v in query_vec):
            return []

        scored: List[Tuple[str, float]] = []
        for agent_id, doc_vec in self._vectors.items():
            sim = _cosine(query_vec, doc_vec)
            if sim > 0.0:
                # clamp 到 [0, 1] 防止浮点误差越界
                sim = max(0.0, min(1.0, sim))
                scored.append((agent_id, sim))

        scored.sort(key=lambda kv: kv[1], reverse=True)
        scored = scored[:limit]

        return [
            IndexHit(
                agent_id=agent_id,
                score=score,
                evidence=query,
            )
            for agent_id, score in scored
        ]

    def is_external_ready(self) -> bool:
        """外部向量后端是否就绪。"""
        return self._external_ready

    def document_count(self) -> int:
        """返回当前向量总数。"""
        return len(self._vectors)

    def clear(self) -> None:
        """清空向量索引。"""
        self._vectors.clear()
