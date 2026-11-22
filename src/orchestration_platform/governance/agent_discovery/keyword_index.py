# =============================================================================
# 关键词倒排索引（governance/agent_discovery/keyword_index.py）
#
# 职责：纯标准库实现的关键词倒排索引——分词、倒排表、TF-IDF 评分。
# 设计意图（对应偏差修正 T195）：
#   - 零外部依赖：仅使用标准库（re / collections / math）
#   - 中英文混合分词：英文按空白与标点切分，中文按字符二元组兜底
#   - 倒排表：词 → [(agent_id, 词频)]
#   - 评分：TF-IDF 变体，归一化到 [0, 1]
#   - 标签命中加权：查询标签与档案标签匹配提升分数
# =============================================================================

import math
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Set

from orchestration_platform.governance.agent_discovery.models import IndexHit

# 英文词元正则：字母数字序列
_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+")


def _tokenize(text: str) -> List[str]:
    """中英文混合分词。

    英文按空白与标点切分为词元；中文按字符二元组（bigram）兜底，
    保证无外部分词依赖时仍可对中文文本建立倒排索引。
    """
    if not text:
        return []

    tokens: List[str] = []

    # 英文词元
    for match in _WORD_PATTERN.findall(text):
        lowered = match.lower()
        if len(lowered) > 0:
            tokens.append(lowered)

    # 中文二元组：扫描非 ASCII 字母字符
    chinese_chars: List[str] = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            chinese_chars.append(ch)
    for i in range(len(chinese_chars) - 1):
        tokens.append(chinese_chars[i] + chinese_chars[i + 1])
    # 单字也作为词元（短文本兜底）
    for ch in chinese_chars:
        tokens.append(ch)

    return tokens


class KeywordInvertedIndex:
    """关键词倒排索引（纯标准库）。

    维护词元到 (agent_id, 词频) 的倒排表，
    以及每个 Agent 的文档长度与标签集合。
    """

    def __init__(self) -> None:
        """构造空倒排索引。"""
        # 倒排表：词元 → {agent_id: 词频}
        self._postings: Dict[str, Dict[str, int]] = defaultdict(dict)
        # Agent 文档词频统计：agent_id → Counter(词元 → 词频)
        self._doc_terms: Dict[str, Counter] = {}
        # Agent 标签集合：agent_id → set(标签)
        self._doc_tags: Dict[str, Set[str]] = {}
        # Agent 文档总数
        self._doc_count: int = 0

    def add_document(
        self,
        agent_id: str,
        text: str,
        tags: Optional[List[str]] = None,
    ) -> None:
        """添加或更新一个 Agent 文档。

        相同 agent_id 重复添加将先移除旧文档再插入，保证幂等。
        """
        if agent_id in self._doc_terms:
            self.remove_document(agent_id)

        tokens = _tokenize(text)
        term_freq = Counter(tokens)

        self._doc_terms[agent_id] = term_freq
        for term, freq in term_freq.items():
            self._postings[term][agent_id] = freq

        normalized_tags = {t.lower() for t in (tags or [])}
        self._doc_tags[agent_id] = normalized_tags
        self._doc_count += 1

    def remove_document(self, agent_id: str) -> bool:
        """移除 Agent 文档，返回是否实际移除。"""
        if agent_id not in self._doc_terms:
            return False

        for term in self._doc_terms[agent_id]:
            self._postings[term].pop(agent_id, None)
            if not self._postings[term]:
                del self._postings[term]

        del self._doc_terms[agent_id]
        self._doc_tags.pop(agent_id, None)
        self._doc_count -= 1
        return True

    def search(
        self,
        query: str,
        tags_filter: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[IndexHit]:
        """关键词检索。

        评分采用 TF-IDF 变体并归一化；标签命中加权。
        tags_filter 为 AND 语义：仅返回包含全部过滤标签的文档。
        """
        if self._doc_count == 0:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        # 标签过滤
        normalized_tag_filter = {t.lower() for t in (tags_filter or [])}
        candidate_ids: Set[str] = set(self._doc_terms.keys())
        if normalized_tag_filter:
            candidate_ids = {
                aid
                for aid in candidate_ids
                if normalized_tag_filter.issubset(self._doc_tags.get(aid, set()))
            }

        # 候选文档收集：包含任一查询词元的文档
        scored: Dict[str, float] = defaultdict(float)
        query_term_set = set(query_tokens)
        for term in query_term_set:
            postings = self._postings.get(term)
            if not postings:
                continue
            # IDF：log((N + 1) / (df + 1)) + 1
            df = len(postings)
            idf = math.log((self._doc_count + 1) / (df + 1)) + 1.0
            for agent_id, freq in postings.items():
                if agent_id not in candidate_ids:
                    continue
                # TF 归一化：1 + log(freq)
                tf = 1.0 + math.log(freq)
                scored[agent_id] += tf * idf

        # 标签命中加权
        query_tag_tokens = {t.lower() for t in (tags_filter or [])}
        if query_tag_tokens:
            for agent_id in list(scored.keys()):
                doc_tags = self._doc_tags.get(agent_id, set())
                overlap = len(query_tag_tokens & doc_tags)
                if overlap > 0:
                    scored[agent_id] *= (1.0 + 0.1 * overlap)

        if not scored:
            return []

        # 归一化到 [0, 1]
        max_score = max(scored.values())
        if max_score <= 0:
            return []
        normalized = {
            aid: score / max_score for aid, score in scored.items()
        }

        # 排序并截断
        ranked = sorted(
            normalized.items(), key=lambda kv: kv[1], reverse=True
        )[:limit]

        return [
            IndexHit(
                agent_id=agent_id,
                score=score,
                evidence=query,
            )
            for agent_id, score in ranked
        ]

    def document_count(self) -> int:
        """返回当前索引文档总数。"""
        return self._doc_count

    def clear(self) -> None:
        """清空索引。"""
        self._postings.clear()
        self._doc_terms.clear()
        self._doc_tags.clear()
        self._doc_count = 0
