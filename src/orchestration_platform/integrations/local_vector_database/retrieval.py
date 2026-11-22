# =============================================================================
# 检索内核（integrations/local_vector_database/retrieval.py）
#
# 职责：SearchBackend 抽象 + BruteForceIndex（暴力精确检索）+
# OptionalBackendProbe（可选增强后端延迟导入探测）（CHG-L06 / design
# DC-03、DC-05、DC-09、§2.2）。
# 设计意图（对应 local_vector_database design INV-L02 / INV-L06 / INV-L11 /
# INV-L14 / INV-L15、spec FR-14 / AC-08~AC-11 / AC-14 / AC-19 / AC-21~AC-23 /
# AC-31、OP-03 / OP-11）：
#   1. 检索管线：先过滤（source 等值，Python 侧匹配，不依赖 SQL JSON1）
#      → 度量 → 归一 → 排序 → 截断 K（DC-03）；默认 cosine；
#   2. 确定性：排序键 (score 降序, record_id 升序)，同参数结果完全一致
#      （INV-L14）；集合隔离：仅扫目标集合（INV-L06）；
#   3. 空结果 ≠ 失败（INV-L15）：集合不存在 → 空命中成功；
#      查询向量非法/维度不符 → 确定性错误码（不是空结果）；
#   4. OptionalBackendProbe：对 ("faiss","qdrant_client",
#      "sentence_transformers") 方法内延迟导入探测（独立实现，不引用
#      local_backend 模块），结果缓存、可诊断（probe()/is_ready()，
#      DC-05）；无依赖环境导入本模块不崩溃（INV-L02 / AC-19 / AC-23）。
# =============================================================================

import importlib
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import List, Optional, Sequence, Tuple

from orchestration_platform.domain.errors import (
    VECTOR_DB_DIMENSION_MISMATCH,
    VECTOR_DB_INVALID_VECTOR,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.integrations.local_vector_database.models import (
    DiagnosticReport,
    MetadataFilter,
    VectorRecord,
    VectorSearchHit,
    resolve_embedding_kind,
)
from orchestration_platform.integrations.local_vector_database.store import (
    SqliteVectorStore,
)
from orchestration_platform.integrations.local_vector_database.vector_math import (
    metric_score,
    select_top_k,
    supported_metrics,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    VectorRetrievalError,
)


def _validate_query_vector(query: List[float], collection_id: str) -> None:
    """查询向量校验：非空且全部分量有限（AC-08；确定性拒绝）。"""
    if not query:
        raise VectorRetrievalError(
            VECTOR_DB_INVALID_VECTOR,
            f"查询向量为空（集合：{collection_id}）",
        )
    for component in query:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise VectorRetrievalError(
                VECTOR_DB_INVALID_VECTOR,
                f"查询向量含非数值分量（集合：{collection_id}）",
            )
        if not math.isfinite(float(component)):
            raise VectorRetrievalError(
                VECTOR_DB_INVALID_VECTOR,
                f"查询向量含非有限分量 NaN/Inf（集合：{collection_id}）",
            )


def _matches_filter(
    record: VectorRecord, filter_: Optional[MetadataFilter]
) -> bool:
    """source 等值过滤（Python 侧匹配，AC-10）：空过滤 → 全通过。

    vkb_doc_annotation（CHG-21 / DC-08 方案 A）：支持**点路径键**（如
    ``"annotations.origin"``）——键含 ``.`` 时逐段下钻嵌套 dict 后等值比较；
    **无 ``.`` 的键行为逐字不变**（向后兼容）。
    """
    if filter_ is None or not filter_.equals:
        return True
    if record.source is None:
        return False
    return all(
        _resolve_dotted(record.source, key) == value
        for key, value in filter_.equals.items()
    )


def _resolve_dotted(source: Mapping, key: str) -> JsonValue:
    """按点路径键下钻取值（无 ``.`` → 顶层取值，行为与既有逐字一致）。"""
    if "." not in key:
        return source.get(key)
    current: JsonValue = source
    for segment in key.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


class SearchBackend(ABC):
    """检索后端抽象（DC-03；P2 可替换，本期仅 BruteForceIndex）。"""

    @abstractmethod
    def search(
        self,
        collection_id: str,
        query_vector: Sequence[float],
        top_k: int,
        filter_: Optional[MetadataFilter],
        metric: str,
    ) -> List[VectorSearchHit]:
        """Top-K 检索：先过滤 → 度量 → 归一 → 排序 → 截断 K。"""

    @abstractmethod
    def rebuild(self, collection_id: str) -> DiagnosticReport:
        """结构重建（P2 ANN 预留，DC-09）：本期暴力无结构，语义 = 一致性校验。"""


class BruteForceIndex(SearchBackend):
    """暴力精确检索（flat，O(N·D)，召回 1.0；DC-03 默认实现）。"""

    def __init__(self, store: SqliteVectorStore, max_top_k: int = 100) -> None:
        if max_top_k < 1:
            raise ValueError("max_top_k 须为正整数")
        self._store = store
        self._max_top_k = max_top_k

    @property
    def max_top_k(self) -> int:
        return self._max_top_k

    def search(
        self,
        collection_id: str,
        query_vector: Sequence[float],
        top_k: int,
        filter_: Optional[MetadataFilter],
        metric: str,
    ) -> List[VectorSearchHit]:
        """先过滤 → 度量 → 排序 → 截断 K（集合不存在 → 空命中成功）。"""
        if metric not in supported_metrics():
            raise ValueError(f"未知度量取值：{metric}")
        query = list(query_vector)
        _validate_query_vector(query, collection_id)
        meta = self._store.read_meta(collection_id)
        if meta is None:
            # 集合不存在 → 空命中成功（INV-L15；不建集合、零副作用）
            return []
        if len(query) != meta.declared_dimension:
            raise VectorRetrievalError(
                VECTOR_DB_DIMENSION_MISMATCH,
                (
                    f"查询向量维度与集合声明不一致（集合：{collection_id}；"
                    f"声明维度：{meta.declared_dimension}；查询维度：{len(query)}）"
                ),
            )
        # 先过滤（不改变 K 语义：K 作用域 = 过滤后候选集，AC-10）
        candidates = [
            record
            for record in self._store.load_records(collection_id)
            if _matches_filter(record, filter_)
        ]
        # 度量（vector_math 已夹取到 [0,1]，禁 NaN 外泄）；代际随命中透出
        # （DC-B2-05 ④：两代均真实计分，hash 代际由出口层保持降级标注）
        hits = [
            VectorSearchHit(
                record_id=record.record_id,
                text=record.text,
                score=metric_score(metric, query, record.vector),
                source=record.source,
                embedding_kind=resolve_embedding_kind(
                    record.embedding_kind, record.source
                ),  # type: ignore[arg-type]
            )
            for record in candidates
        ]
        # 排序 + 截断（tie-break = record_id 升序，INV-L14）
        return select_top_k(
            hits,
            top_k,
            self._max_top_k,
            score_of=lambda hit: hit.score,
            id_of=lambda hit: hit.record_id,
        )

    def rebuild(self, collection_id: str) -> DiagnosticReport:
        """本期语义 = 一致性校验（暴力无结构；P2 ANN 重建预留，DC-09）。"""
        return self._store.diagnose(collection_id)


class OptionalBackendProbe:
    """可选增强后端探测（DC-05）：方法内延迟导入，结果缓存、可诊断。

    独立实现（不引用 local_backend 模块）；构造零副作用（不探测）；
    无依赖环境导入/构造不崩溃（INV-L02 / AC-19 / AC-23）。
    """

    DEFAULT_CANDIDATES: Tuple[str, ...] = (
        "faiss",
        "qdrant_client",
        "sentence_transformers",
    )

    def __init__(self, candidates: Optional[Sequence[str]] = None) -> None:
        self._candidates: Tuple[str, ...] = (
            tuple(candidates) if candidates is not None else self.DEFAULT_CANDIDATES
        )
        self._probed = False
        self._result: Optional[str] = None

    def probe(self) -> Optional[str]:
        """探测就绪后端名（首个可导入候选）；无可用后端 → None（结果缓存）。"""
        if not self._probed:
            self._result = self._probe_once()
            self._probed = True
        return self._result

    def is_ready(self) -> bool:
        """是否有任一增强后端就绪。"""
        return self.probe() is not None

    def _probe_once(self) -> Optional[str]:
        for name in self._candidates:
            try:
                importlib.import_module(name)
                return name
            except Exception:
                # ImportError 及模块级副作用异常一律 fail-safe 视为不可用
                continue
        return None
