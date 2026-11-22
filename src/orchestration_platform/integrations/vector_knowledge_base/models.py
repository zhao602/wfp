# =============================================================================
# 向量知识库检索数据模型（integrations/vector_knowledge_base/models.py）
#
# 职责：定义向量检索集成层的出参模型与统一检索异常（CHG-04）。
# 设计意图（对应 vector_knowledge_base_node design §2.2.4 / §2.2.5）：
#   1. Chunk为外部检索载荷的净化后形态：text 非空且 ≤2000 字符、
#      score 归一到 [0,1]、source 仅保留 JSON 兼容结构；
#   2. sanitize_chunk_payload 对外部载荷做确定性净化（丢弃/截断/夹取），
#      非法结构不抛异常、不伪造语义，保证节点对下游输出可控（OP-08）；
#   3. VectorRetrievalError 携带语义化错误码，供执行器映射为节点失败
#      （检索失败 ≠ 空结果，INV-D07）；
#   4. 本模块为纯数据层：无 I/O、无 httpx、无 settings 依赖，可独立单测。
# =============================================================================

import math
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from orchestration_platform.domain.models.common import JsonValue

# 片段文本长度上限（与 query_value 上限同源：spec §6.4 / design §2.2.1）
CHUNK_TEXT_MAX_LENGTH = 2000


class Chunk(BaseModel):
    """净化后的检索片段。

    字段：
        text   —— 片段文本（非空，≤2000 字符；超长在外部净化阶段确定性截断）
        score  —— 相关性得分（归一到 [0,1]；非法/缺失取 0.0）
        source —— 来源元数据（可选，仅 JSON 兼容结构）
    """

    text: str = Field(min_length=1, max_length=CHUNK_TEXT_MAX_LENGTH, description="片段文本")
    score: float = Field(ge=0.0, le=1.0, description="相关性得分（归一）")
    source: Optional[Dict[str, JsonValue]] = Field(default=None, description="来源元数据")


class RetrievalOutcome(BaseModel):
    """一次检索调用的结果（含降级标注）。

    字段：
        chunks                  —— 净化后的片段列表（可为空：空结果 = 合法成功）
        rerank_degraded         —— rerank 已配置但执行失败，降级为初排结果
        rerank_degraded_reason  —— rerank 降级原因（固定文本，不含地址/凭据）
        local_degraded          —— local 模式确定性降级（运行时缺失/本期未实现推理）
        local_degraded_reason   —— local 降级原因说明
    """

    chunks: List[Chunk] = Field(default_factory=list, description="净化后的片段列表")
    rerank_degraded: bool = Field(default=False, description="rerank 降级标记")
    rerank_degraded_reason: Optional[str] = Field(default=None, description="rerank 降级原因")
    local_degraded: bool = Field(default=False, description="local 模式降级标记")
    local_degraded_reason: Optional[str] = Field(default=None, description="local 降级原因")


class VectorRetrievalError(Exception):
    """向量检索集成层统一异常：携带语义化错误码与固定文本消息。

    约定（design §2.2.5 降级矩阵 / SV-3 凭据隔离）：
    - error_code   —— NODE_VECTOR_RETRIEVAL_UNAVAILABLE /
                      NODE_VECTOR_EMBEDDING_UNAVAILABLE /
                      NODE_VECTOR_LOCAL_RUNTIME_MISSING；
    - error_message —— 固定文本 + 非凭据定位信息（模型标识/知识库标识），
                      严禁包含服务地址、密钥或原始响应内容。
    """

    def __init__(self, error_code: str, error_message: str) -> None:
        super().__init__(error_message)
        self.error_code = error_code
        self.error_message = error_message


def _coerce_score(raw: Any) -> float:
    """得分确定性归一：非法/NaN 取 0.0，越界夹取到 [0,1]。"""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(value):
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def sanitize_chunk_payload(items: Any) -> List[Chunk]:
    """外部检索载荷确定性净化（OP-08：非法结构不抛异常、不伪造语义）。

    规则：
    - 整体非 list → 空列表（检索后端返回结构异常按无结果处理，由调用方
      结合上下文判定；本函数不区分「空库」与「结构异常」）；
    - 单条非 dict / 无 text / text 非字符串 / text 为空 → 丢弃该条；
    - text 超过 2000 字符 → 截断到 2000，并在 source 标注 truncated=true；
    - score 缺失/非法/NaN → 0.0，越界夹取 [0,1]；
    - source 非 dict → 置空；
    - 净化后仍不满足 Chunk 模型约束的条目 → 丢弃（契约：本函数不抛异常）。
    """
    if not isinstance(items, list):
        return []
    chunks: List[Chunk] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text:
            continue
        source = item.get("source")
        if not isinstance(source, dict):
            source = None
        if len(text) > CHUNK_TEXT_MAX_LENGTH:
            text = text[:CHUNK_TEXT_MAX_LENGTH]
            annotated: Dict[str, JsonValue] = {"truncated": True}
            if source is not None:
                annotated.update(source)
                annotated["truncated"] = True
            source = annotated
        try:
            chunks.append(
                Chunk(text=text, score=_coerce_score(item.get("score")), source=source)
            )
        except ValidationError:
            continue
    return chunks
