# =============================================================================
# 本地向量数据库内部领域模型（integrations/local_vector_database/models.py）
#
# 职责：定义本组件内部领域模型（CHG-L02 / design DC-02、§2.3.2）。
# 设计意图（对应 local_vector_database design INV-L03 / INV-L08 / INV-L11 /
# INV-L12 / spec FR-13、FR-29）：
#   1. 本模块仅定义内部模型（记录/集合元信息/过滤/命中/报告）；
#      出口契约复用 vector_knowledge_base.models 的 Chunk/RetrievalOutcome/
#      VectorRetrievalError（不新造平行契约，INV-L03）；
#   2. 度量取值用 typing.Literal（禁 StrEnum，INV-L12）；
#      JsonValue 复用 domain.models.common；
#   3. score 归一到 [0,1]（INV-L11）；首写固化维度（INV-L07）；
#   4. 纯数据层：无 I/O、无 sqlite3、无 httpx、无 settings，可独立单测。
# =============================================================================

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.common import JsonValue

# 度量取值域（Literal，禁 StrEnum，INV-L12）
MetricName = Literal["cosine", "dot", "euclidean"]

# embedding 代际取值域（B2-C5 / DC-B2-05 ④：real=真实语义来源；hash=哈希伪向量代际）
EMBEDDING_KIND_REAL = "real"
EMBEDDING_KIND_HASH = "hash"
EMBEDDING_KINDS = (EMBEDDING_KIND_REAL, EMBEDDING_KIND_HASH)

# 真实语义来源 provenance 取值域（DC-B2-05 ③：api/local_runtime/caller_supplied）
_REAL_PROVENANCES = ("api", "local_runtime", "caller_supplied")

# 持久化格式版本（跨版本明确拒绝，AC-36；迁移工具为 P2 留白，DC-09）
FORMAT_VERSION = "1"


class VectorRecord(BaseModel):
    """向量记录：存储与计算的最小单元（record_id 为幂等键，AC-04/AC-05）。"""

    record_id: str = Field(min_length=1, description="记录标识（幂等键，同键覆盖更新）")
    vector: List[float] = Field(description="向量分量（维度须与集合声明一致，INV-L07）")
    text: str = Field(description="文本载荷（存储时超长按净化语义截断并标注）")
    source: Optional[Dict[str, JsonValue]] = Field(default=None, description="来源元数据")
    embedding_kind: Optional[Literal["real", "hash"]] = Field(
        default=None,
        description="embedding 代际（real=真实语义来源；hash=哈希伪向量；None=存量记录，读取时按 legacy 规则识别）",
    )


def resolve_embedding_kind(
    embedding_kind: Optional[str],
    source: Optional[Dict[str, JsonValue]],
) -> str:
    """记录级 embedding 代际识别（DC-B2-05 ④；三级回退，确定性）。

    1. 显式字段（写入时落盘的 embedding_kind）——权威；
    2. source.provenance 推导（存量委托写入记录已携带 provenance）：
       hash_fallback → hash；api/local_runtime/caller_supplied → real；
    3. 无任何信息 → hash（存量缺省 hash 代际；不冒充真实语义，INV-B2-05）。
    """
    if embedding_kind in EMBEDDING_KINDS:
        return embedding_kind  # type: ignore[return-value]
    provenance = ""
    if isinstance(source, dict):
        provenance = str(source.get("provenance") or "")
    if provenance == "hash_fallback":
        return EMBEDDING_KIND_HASH
    if provenance in _REAL_PROVENANCES:
        return EMBEDDING_KIND_REAL
    return EMBEDDING_KIND_HASH


class CollectionMeta(BaseModel):
    """集合元信息：一集合一文件隔离（INV-L06），首写固化维度（INV-L07）。"""

    collection_id: str = Field(min_length=1, description="集合标识")
    declared_dimension: int = Field(ge=1, le=4096, description="集合声明维度（首写固化）")
    metric: MetricName = Field(description="相似度度量（cosine/dot/euclidean）")
    format_version: str = Field(description="持久化格式版本")
    created_at: str = Field(description="创建时间（ISO-8601）")
    updated_at: str = Field(description="最近更新时间（ISO-8601）")


class MetadataFilter(BaseModel):
    """元数据等值过滤（source 口径，Python 侧匹配，不依赖 SQL JSON1，AC-10）。"""

    equals: Dict[str, JsonValue] = Field(default_factory=dict, description="字段-值等值条件")


class VectorSearchHit(BaseModel):
    """检索命中：组装既有 Chunk 所需字段（score 归一 [0,1]，INV-L11）。"""

    record_id: str = Field(min_length=1, description="命中记录标识")
    text: str = Field(description="命中文本")
    score: float = Field(ge=0.0, le=1.0, description="相似度得分（归一到 [0,1]）")
    source: Optional[Dict[str, JsonValue]] = Field(default=None, description="来源元数据")
    embedding_kind: Optional[Literal["real", "hash"]] = Field(
        default=None,
        description="命中记录的 embedding 代际（供出口降级标注判定，INV-B2-05）",
    )


class RecordFailure(BaseModel):
    """单条失败明细（批量写/导入）：record_id + 固定文本原因（禁凭据，INV-L09）。"""

    record_id: str = Field(description="失败记录标识")
    reason: str = Field(description="失败原因（固定文本，不含凭据）")


class UpsertReport(BaseModel):
    """批量 upsert 报告：成功计数 + 失败清单（单条非法不整体失败，AC-04/AC-05）。"""

    upserted_count: int = Field(default=0, ge=0, description="成功写入/更新条数")
    failed: List[RecordFailure] = Field(default_factory=list, description="失败清单")


class DeleteReport(BaseModel):
    """幂等删除报告：不存在条目返回“未删除”，不视为错误（AC-06/AC-07）。"""

    deleted_count: int = Field(default=0, ge=0, description="实际删除条数")
    missing_ids: List[str] = Field(default_factory=list, description="不存在的记录标识")


class ImportReport(BaseModel):
    """JSONL 导入报告：逐行校验、部分成功（AC-13）。"""

    imported_count: int = Field(default=0, ge=0, description="成功导入条数")
    failed: List[RecordFailure] = Field(default_factory=list, description="失败条目清单（含原因）")


class ExportReport(BaseModel):
    """JSONL 导出报告：导出计数（产物禁凭据，INV-L09）。"""

    exported_count: int = Field(default=0, ge=0, description="成功导出条数")


class DiagnosticReport(BaseModel):
    """一致性诊断报告（AC-35 语义载体）：完整性 + 维度一致性 + 计数 + 版本。"""

    collection_id: str = Field(description="集合标识")
    ok: bool = Field(description="整体是否健康")
    integrity_check: str = Field(description="PRAGMA integrity_check 结果摘要")
    dimension_consistent: bool = Field(default=False, description="记录维度与声明维度是否一致")
    declared_dimension: Optional[int] = Field(default=None, description="集合声明维度")
    record_count: int = Field(default=0, ge=0, description="集合条目计数")
    format_version: str = Field(default="", description="持久化格式版本")
    issues: List[str] = Field(default_factory=list, description="问题清单（固定文本）")
