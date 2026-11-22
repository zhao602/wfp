# =============================================================================
# 混合检索器（governance/agent_discovery/hybrid_retriever.py）
#
# 职责：关键词索引 + 向量索引的混合检索，采用 RRF 融合两路排序。
# 设计意图（对应偏差修正 T197）：
#   - RRF（Reciprocal Rank Fusion）：对每路排序按 1/(k+rank) 计分求和
#   - k 为平滑常数（默认 60），抑制头部过度主导
#   - 向量索引可选：未就绪时自动退化为纯关键词检索
#   - 命中证据合并：融合两路的 evidence
# =============================================================================

import logging
from typing import Dict, List, Optional, Tuple

from orchestration_platform.governance.agent_discovery.keyword_index import (
    KeywordInvertedIndex,
)
from orchestration_platform.governance.agent_discovery.models import IndexHit
from orchestration_platform.governance.agent_discovery.vector_index import (
    VectorIndex,
)

_logger = logging.getLogger(__name__)

# RRF 平滑常数（经验值 60，源自 Cormack et al. 2009）
_RRF_K = 60


class HybridRetriever:
    """混合检索器。

    融合关键词倒排索引与向量索引的排序结果。
    向量索引为 None 或未就绪时退化为纯关键词检索。
    """

    def __init__(
        self,
        keyword_index: KeywordInvertedIndex,
        vector_index: Optional[VectorIndex] = None,
        rrf_k: int = _RRF_K,
    ) -> None:
        """构造混合检索器。

        vector_index 为 None 时纯关键词检索。
        """
        self._keyword_index = keyword_index
        self._vector_index = vector_index
        self._rrf_k = rrf_k

    def retrieve(
        self,
        query: str,
        tags_filter: Optional[List[str]] = None,
        limit: int = 20,
    ) -> Tuple[List[IndexHit], str]:
        """混合检索并返回 (融合命中, 后端标识)。

        后端标识：
          - "keyword"：纯关键词检索
          - "hybrid"：关键词 + 向量混合
        """
        keyword_hits = self._keyword_index.search(
            query=query,
            tags_filter=tags_filter,
            limit=limit,
        )

        vector_hits: List[IndexHit] = []
        if self._vector_index is not None and self._vector_index.document_count() > 0:
            vector_hits = self._vector_index.search(
                query=query,
                limit=limit,
            )

        # 无向量命中 → 纯关键词
        if not vector_hits:
            return keyword_hits, "keyword"

        # RRF 融合
        fused = self._rrf_fuse(keyword_hits, vector_hits, limit)
        return fused, "hybrid"

    def _rrf_fuse(
        self,
        keyword_hits: List[IndexHit],
        vector_hits: List[IndexHit],
        limit: int,
    ) -> List[IndexHit]:
        """RRF 融合两路命中。

        对每路排序按 1/(k+rank) 计分求和；命中证据合并去重。
        """
        scores: Dict[str, float] = {}
        evidences: Dict[str, List[str]] = {}

        # 关键词路
        for rank, hit in enumerate(keyword_hits):
            rrf_score = 1.0 / (self._rrf_k + rank + 1)
            scores[hit.agent_id] = scores.get(hit.agent_id, 0.0) + rrf_score
            evidences.setdefault(hit.agent_id, [])
            if hit.evidence and hit.evidence not in evidences[hit.agent_id]:
                evidences[hit.agent_id].append(hit.evidence)

        # 向量路
        for rank, hit in enumerate(vector_hits):
            rrf_score = 1.0 / (self._rrf_k + rank + 1)
            scores[hit.agent_id] = scores.get(hit.agent_id, 0.0) + rrf_score
            evidences.setdefault(hit.agent_id, [])
            if hit.evidence and hit.evidence not in evidences[hit.agent_id]:
                evidences[hit.agent_id].append(hit.evidence)

        # 归一化到 [0, 1]
        max_score = max(scores.values()) if scores else 0.0
        if max_score <= 0:
            return []
        normalized = {
            aid: score / max_score for aid, score in scores.items()
        }

        ranked = sorted(
            normalized.items(), key=lambda kv: kv[1], reverse=True
        )[:limit]

        return [
            IndexHit(
                agent_id=agent_id,
                score=score,
                evidence=" | ".join(evidences.get(agent_id, [])),
            )
            for agent_id, score in ranked
        ]
