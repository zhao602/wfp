# =============================================================================
# 检索内核数学层（integrations/local_vector_database/vector_math.py）
#
# 职责：度量（cosine/dot/euclidean）/ L2 归一 / 分值夹取 / Top-K 排序
# （CHG-L05 / design DC-03、§7）。
# 设计意图（对应 local_vector_database design INV-L11 / INV-L14 / INV-L15、
# spec FR-14 / AC-14 / AC-21 / AC-22 / AC-31、OP-11）：
#   1. 纯标准库（math/typing），无第三方依赖、无 I/O，可独立单测；
#   2. 度量定义（DC-03）：cosine = L2 归一后点积，结果
#      max(0.0, min(1.0, cos)) 夹取到 [0,1]；dot 须各自归一后点积再夹取
#      （与 cosine 同式）；euclidean 经确定性转换 1/(1+d) 落入 (0,1]；
#   3. score 归一到 [0,1]（INV-L11）：NaN → 0.0、越界夹取，禁 NaN 外泄；
#   4. Top-K 排序键 (score 降序, record_id 升序)（INV-L14 确定性
#      tie-break）；top_k 夹取到 [0, max_top_k]；空输入 → 空列表成功
#      （INV-L15）。
# =============================================================================

import math
from typing import Callable, Dict, Iterable, List, Sequence, Tuple, TypeVar

_T = TypeVar("_T")


def clamp_score(value: float) -> float:
    """分值夹取到 [0,1]（INV-L11）：NaN → 0.0、越界夹取，禁 NaN 外泄。"""
    if math.isnan(value):
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def l2_normalize(vector: Sequence[float]) -> List[float]:
    """L2 归一；零向量/非有限模长原样返回（除零保护，确定性）。"""
    values = list(vector)
    norm = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(norm) or norm == 0.0:
        return values
    return [v / norm for v in values]


def _check_same_length(query: Sequence[float], candidate: Sequence[float]) -> None:
    """防御性维度一致性检查（检索入口已校验，此处兜底禁静默截断）。"""
    if len(query) != len(candidate):
        raise ValueError(
            f"查询与候选向量维度不一致（查询：{len(query)}；候选：{len(candidate)}）"
        )


def cosine_score(query: Sequence[float], candidate: Sequence[float]) -> float:
    """余弦相似度：各自 L2 归一后点积，夹取到 [0,1]（DC-03 默认度量）。"""
    _check_same_length(query, candidate)
    normalized_query = l2_normalize(query)
    normalized_candidate = l2_normalize(candidate)
    return clamp_score(
        sum(a * b for a, b in zip(normalized_query, normalized_candidate))
    )


def dot_score(query: Sequence[float], candidate: Sequence[float]) -> float:
    """内积相似度：各自 L2 归一后点积再夹取（DC-03：与 cosine 同式）。"""
    return cosine_score(query, candidate)


def euclidean_score(query: Sequence[float], candidate: Sequence[float]) -> float:
    """欧氏距离相似度：确定性转换 1/(1+d) 落入 (0,1]（DC-03）。"""
    _check_same_length(query, candidate)
    distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(query, candidate)))
    if not math.isfinite(distance):
        return 0.0
    return clamp_score(1.0 / (1.0 + distance))


_METRIC_SCORERS: Dict[str, Callable[[Sequence[float], Sequence[float]], float]] = {
    "cosine": cosine_score,
    "dot": dot_score,
    "euclidean": euclidean_score,
}


def supported_metrics() -> Tuple[str, ...]:
    """支持的度量取值域（与 models.MetricName 一致）。"""
    return tuple(_METRIC_SCORERS)


def metric_score(
    metric: str, query: Sequence[float], candidate: Sequence[float]
) -> float:
    """度量分派：未知度量 → ValueError（构造/入口期已拦，此处兜底）。"""
    scorer = _METRIC_SCORERS.get(metric)
    if scorer is None:
        raise ValueError(f"未知度量取值：{metric}")
    return scorer(query, candidate)


def select_top_k(
    items: Iterable[_T],
    top_k: int,
    max_top_k: int,
    score_of: Callable[[_T], float],
    id_of: Callable[[_T], str],
) -> List[_T]:
    """Top-K 截断：排序键 (score 降序, record_id 升序)（INV-L14 确定性）。

    - top_k 夹取到 [0, max_top_k]（负值/0 → 空列表）；
    - 空输入 → 空列表成功（INV-L15）；
    - 调用方须保证 score 有限（metric_score 已夹取，VectorSearchHit 亦校验）。
    """
    materialized = list(items)
    limit = max(0, min(top_k, max_top_k))
    if not materialized or limit == 0:
        return []
    ranked = sorted(materialized, key=lambda item: (-score_of(item), id_of(item)))
    return ranked[:limit]
