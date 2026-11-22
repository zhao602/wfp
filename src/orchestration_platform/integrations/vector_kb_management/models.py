# =============================================================================
# 向量知识库管理内部领域模型（integrations/vector_kb_management/models.py）
#
# 职责：定义本组件内部领域模型（CHG-02 / design §2.3.2）。
# 设计意图（vector_kb_management design INV-D02 / INV-D10 / INV-D17）：
#   1. 出口契约复用 vector_knowledge_base.models 的 Chunk / RetrievalOutcome /
#      VectorRetrievalError 与 local_vector_database 的 VectorRecord /
#      RecordFailure（不新造平行契约，INV-D02）；
#   2. 零凭据：KBEmbeddingBindingSummary 不含 base_url / api_key（INV-D10）；
#   3. 强绑定单值：KnowledgeBase.embedding_model 为单值 KBEmbeddingBinding
#      （非 List，INV-D17）；
#   4. 纯数据层：无 I/O、无 sqlite3、无 httpx、无 settings，可独立单测。
# =============================================================================

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.integrations.local_vector_database.models import (
    RecordFailure,
)

# 分片策略取值域（Literal，禁 StrEnum；LLM 分块本期不实现，枚举不含该项）
ChunkStrategy = Literal[
    "paragraph",
    "fixed_length",
    "recursive",
    "structural",
    "semantic",
]

# 知识库标识白名单（复用 local_vector_database/store.py 的集合标识语义，禁路径穿越）
KNOWLEDGE_BASE_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,127}$"

# 分片文本长度上限（对齐 vector_knowledge_base.models.CHUNK_TEXT_MAX_LENGTH）
CHUNK_TEXT_LIMIT = 2000


class ParsedDocument(BaseModel):
    """解析产物：统一承载四种格式（.txt/.md/.pdf/.docx）的解析结果。

    - text     —— 全文文本（零依赖直读或解析器提取）；
    - format   —— 归一格式标识（txt/md/pdf/docx）；
    - structure —— 结构信息（供 structural 分块；.md 保留标题层级；
                  纯文本/未提供结构的 PDF 为 None）。
    """

    text: str = Field(description="全文文本")
    format: str = Field(description="归一格式标识（txt/md/pdf/docx）")
    structure: Optional[Dict[str, JsonValue]] = Field(
        default=None, description="结构信息（标题层级等，供 structural 分块）"
    )


class ChunkDraft(BaseModel):
    """分片草稿（CHG-02 / design §2.7.1）。

    - chunk_id —— 幂等键，由导入流水线按 ``{document_id}:{序号:04d}``
      确定性赋值（确认后作为 record_id，INV-D20）；空串表示尚未赋值；
    - index    —— 同文档内 0 起自增索引（过滤空白片段后连续）；
    - text     —— 非空，长度 ≤ CHUNK_TEXT_LIMIT（超长确定性截断并标注
      source["truncated"]=True，由分片器组装层保证）；
    - source   —— 来源元数据（strategy / index / truncated? 等，由分片器
      与流水线逐层补充）。
    """

    chunk_id: str = Field(default="", description="幂等键（流水线赋值；空串=未赋值）")
    index: int = Field(ge=0, description="同文档内自增索引（0 起）")
    text: str = Field(min_length=1, description="分片文本（非空，≤ 2000）")
    source: Optional[Dict[str, JsonValue]] = Field(
        default=None, description="来源元数据（strategy/index/truncated? 等）"
    )


class ChunkStrategyConfig(BaseModel):
    """分片策略参数（design §2.7.1 通用前置校验的载体）。

    数值合法性由 ``chunkers.validate_chunk_params`` 以领域错误码
    ``VECTOR_KB_INVALID_CHUNK_PARAMS`` 校验；模型层仅承载、不内联抛错，
    保证 API 层错误码契约确定性（pydantic 校验不替代领域拒绝）。
    """

    strategy: ChunkStrategy = Field(description="分片策略（枚举不含 LLM 项，AC-28）")
    max_chunk_length: int = Field(
        default=500, description="单分片长度上限（默认 500，DC-10）"
    )
    overlap: int = Field(
        default=50, description="相邻分片重叠字符数（fixed_length；默认 50）"
    )
    separators: Optional[List[str]] = Field(
        default=None, description="递归分块分隔符优先级（recursive；缺省用内置清单）"
    )
    hierarchy: Optional[List[str]] = Field(
        default=None,
        description="structural 标题行正则清单（任一命中即标题行；缺省 ^#{1,6}\\s）",
    )
    similarity_threshold: Optional[float] = Field(
        default=None, description="语义相似度阈值（semantic；缺省用服务默认 0.75）"
    )


class ChunkerOutcome(BaseModel):
    """分片器统一产出（含回退/降级可见性）。

    notices / strategy_effective 承载 structural / semantic 的确定性
    回退可见性（AC-25 / AC-27：回退必须可见，禁静默）。
    """

    chunks: List[ChunkDraft] = Field(description="分片草稿序列")
    notices: List[str] = Field(
        default_factory=list, description="回退/降级提示（可空）"
    )
    strategy_effective: ChunkStrategy = Field(
        description="实际生效策略（回退时与请求策略不同）"
    )


# =============================================================================
# B3：知识库实体与摄取作业领域模型（design §2.3.2 / CHG-02 完整）
# =============================================================================

# Embedding 绑定模式（Literal，禁 StrEnum）
EmbeddingBindingMode = Literal["local", "api"]

# 文档解析状态（design §2.3.2）
ParseStatus = Literal["pending", "parsed", "failed"]

# 摄取作业状态机（DC-05：pending → running → succeeded/partial/failed）
IngestionJobStatus = Literal[
    "pending", "running", "succeeded", "failed", "partial"
]

# 摄取报告终态（design §2.2.2 T4：报告仅携带终态）
IngestionReportStatus = Literal["succeeded", "partial", "failed"]


class KBEmbeddingBinding(BaseModel):
    """Embedding 绑定（写入形态，单值强绑定，INV-D17 / AC-40~AC-43）。

    - mode ∈ {local, api}；model_identifier 非空；
    - base_url 仅写入不回读（服务地址不回显，AC-45）；
    - allow_degraded_fallback 默认 False；为 True 时降级结果必须标注
      （INV-D08，出口守护复用 lvd enforce_degraded_annotation）。
    """

    mode: EmbeddingBindingMode = Field(description="绑定模式（local/api）")
    model_identifier: str = Field(min_length=1, description="模型标识（非空）")
    base_url: Optional[str] = Field(
        default=None, description="服务地址（仅写入，不回读/不回显）"
    )
    timeout_seconds: Optional[int] = Field(
        default=None, ge=1, description="调用超时（秒，可选）"
    )
    device: Optional[str] = Field(
        default=None, description="推理设备（local 模式可选）"
    )
    allow_degraded_fallback: bool = Field(
        default=False, description="是否允许哈希兜底降级（默认 False）"
    )


class KBEmbeddingBindingSummary(BaseModel):
    """绑定摘要（零凭据：不含 base_url / api_key，INV-D10 / AC-45）。"""

    mode: EmbeddingBindingMode = Field(description="绑定模式（local/api）")
    model_identifier: str = Field(description="模型标识")
    timeout_seconds: Optional[int] = Field(
        default=None, ge=1, description="调用超时（秒，可选）"
    )
    device: Optional[str] = Field(default=None, description="推理设备（可选）")
    allow_degraded_fallback: bool = Field(
        default=False, description="是否允许降级兜底"
    )


class EmbeddingCandidateItem(BaseModel):
    """单条 Embedding 模型候选（响应字段白名单，零凭据，AC-45 / INV-D10）。

    - 白名单：model_identifier / mode / label——无 base_url / 无 api_key；
    - label 可选（缺省不回显）。
    """

    model_identifier: str = Field(min_length=1, description="模型标识")
    mode: EmbeddingBindingMode = Field(description="绑定模式（local/api）")
    label: Optional[str] = Field(default=None, description="展示标签（可选）")


class EmbeddingCandidatesResponse(BaseModel):
    """Embedding 模型候选响应（design §2.2.2 T8）。

    - 候选来源单一（settings 即时派生，AC-44）；
    - 候选为空 → allow_manual_input=true（允许手工填写标识，
      spec §5.5.3 异常场景 1，不得阻断创建）。
    """

    candidates: List[EmbeddingCandidateItem] = Field(
        default_factory=list, description="候选清单（字段白名单）"
    )
    total: int = Field(default=0, ge=0, description="候选总数")
    allow_manual_input: bool = Field(
        default=True, description="是否允许手工填写模型标识（空候选必为 true）"
    )


class KnowledgeBase(BaseModel):
    """知识库实体（DC-03 / design §2.3.2）。

    - knowledge_base_id 白名单（禁路径穿越，INV-D11）；
    - name 非空、去空白后非空、长度 ≤ 200；
    - embedding_model 为 KBEmbeddingBinding **单值**必填（非 List，INV-D17）；
    - default_chunk_strategy 为策略名（缺省按系统默认 recursive）；
    - 技术字段（created_at/updated_at）不入域模型，由落盘层承载
      （design §2.3.2 注）。
    """

    knowledge_base_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=KNOWLEDGE_BASE_ID_PATTERN,
        description="知识库标识（白名单约束，禁路径穿越）",
    )
    name: str = Field(
        min_length=1, max_length=200, description="名称（去空白后非空，≤ 200）"
    )
    embedding_model: KBEmbeddingBinding = Field(
        description="绑定模型（单值必填，INV-D17）"
    )
    default_chunk_strategy: Optional[ChunkStrategy] = Field(
        default=None,
        description="默认分片策略（缺省按系统默认 recursive）",
    )

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name 去空白后为空")
        return stripped

    def binding_summary(self) -> KBEmbeddingBindingSummary:
        """派生去凭据摘要（去 base_url，AC-45 / INV-D10）。"""
        binding = self.embedding_model
        return KBEmbeddingBindingSummary(
            mode=binding.mode,
            model_identifier=binding.model_identifier,
            timeout_seconds=binding.timeout_seconds,
            device=binding.device,
            allow_degraded_fallback=binding.allow_degraded_fallback,
        )


class KnowledgeBaseDocument(BaseModel):
    """文档元数据（design §2.3.2）：一次导入的受理记录。

    - knowledge_base_id / confirmed_version 为落盘层回填字段（模型构造
      可缺省，仓储读写时映射；B1 既有构造路径零破坏）；
    - confirmed_version 指向最近一次**成功确认**的草稿版本（生效基线，
      B5 分片编辑一致性 diff 的对照快照；None = 尚无生效基线）。
    """

    document_id: str = Field(
        min_length=1, max_length=128, description="文档标识（同库唯一）"
    )
    knowledge_base_id: Optional[str] = Field(
        default=None, description="所属知识库（落盘层回填；构造可缺省）"
    )
    file_name: str = Field(min_length=1, description="文件名")
    format: str = Field(min_length=1, description="归一格式（txt/md/pdf/docx）")
    parse_status: ParseStatus = Field(
        description="解析状态（pending/parsed/failed）"
    )
    chunk_strategy: ChunkStrategyConfig = Field(
        description="本次导入实际生效的策略与参数"
    )
    confirmed_version: Optional[str] = Field(
        default=None,
        description="最近成功确认的草稿版本（生效基线；None=尚无）",
    )


class ChunkRecord(BaseModel):
    """生效/草稿分片记录（design §2.3.2；chunk_id ≡ record_id 幂等键，INV-D20）。"""

    chunk_id: str = Field(min_length=1, description="分片标识（≡ record_id）")
    index: int = Field(ge=0, description="文档内索引（0 起）")
    text: str = Field(
        min_length=1,
        max_length=CHUNK_TEXT_LIMIT,
        description="分片文本（非空，≤ 2000）",
    )
    source: Optional[Dict[str, JsonValue]] = Field(
        default=None, description="来源元数据（document_id/index/strategy 等）"
    )
    vectorized: bool = Field(default=False, description="是否已向量化")
    provenance: Optional[str] = Field(
        default=None,
        description="向量化来源（api/local_runtime/caller_supplied/hash_fallback）",
    )
    local_degraded: bool = Field(
        default=False, description="是否为降级（伪语义）结果"
    )


class ChunkDraftState(BaseModel):
    """未生效草稿容器（design UML ChunkDraft；与 T2.5 单分片 ChunkDraft 区分命名）。

    - 草稿仅存于元数据落点，检索不可见（AC-38 / INV-D19）；
    - draft_version 单调递增（如 "v1"→"v2"），base_version 空串表示无基线；
    - chunks 为 ChunkRecord 全量快照；notices / strategy_effective 承载
      回退可见性（AC-25 / AC-27，供 T6 草稿响应与 B5 编辑流水线复用）。
    """

    knowledge_base_id: str = Field(min_length=1, description="所属知识库")
    document_id: str = Field(min_length=1, description="所属文档")
    draft_version: str = Field(min_length=1, description="草稿版本（单调递增）")
    base_version: str = Field(default="", description="基线版本（空串=无基线）")
    chunks: List[ChunkRecord] = Field(
        default_factory=list, description="分片快照"
    )
    notices: List[str] = Field(
        default_factory=list, description="回退/降级提示"
    )
    strategy_effective: ChunkStrategy = Field(description="实际生效策略")


class IngestionResult(BaseModel):
    """摄取结果摘要（design §2.3.2；failed 复用 lvd RecordFailure，INV-D02）。"""

    chunk_count: int = Field(ge=0, description="分片总数")
    upserted_count: int = Field(ge=0, description="成功写入/更新条数")
    failed: List[RecordFailure] = Field(
        default_factory=list, description="失败清单（record_id + 固定文本原因）"
    )
    local_degraded_reason: Optional[str] = Field(
        default=None, description="降级原因（local_degraded=True 时必填）"
    )
    duration_ms: int = Field(ge=0, description="耗时（毫秒）")


class IngestionJob(BaseModel):
    """摄取作业（DC-05 两段式状态机载体）。

    - 状态机 pending → running → succeeded/partial/failed；failed 可重确认
      （幂等）；
    - pending/running 阶段 result / provenance 为空（None）；
    - hash_fallback 一致性（provenance=hash_fallback → local_degraded=True
      + 原因）由流水线经 lvd enforce_degraded_annotation 出口守护（INV-D02
      不重复实现）。
    """

    job_id: str = Field(min_length=1, description="作业标识")
    knowledge_base_id: str = Field(min_length=1, description="所属知识库")
    document_id: str = Field(min_length=1, description="所属文档")
    status: IngestionJobStatus = Field(
        description="状态（pending/running/succeeded/failed/partial）"
    )
    result: Optional[IngestionResult] = Field(
        default=None, description="结果摘要（pending/running 阶段为 None）"
    )
    provenance: Optional[str] = Field(
        default=None, description="向量化来源（四类之一；未完成为 None）"
    )
    local_degraded: bool = Field(
        default=False, description="是否降级（伪语义标注）"
    )


# 响应分片视图（design §2.2.2 T3/T6：与单分片草稿同构——别名复用，不造平行契约，INV-D02）
ChunkDraftView = ChunkDraft


class ImportDraftResponse(BaseModel):
    """导入受理响应（design §2.2.2 T3）。"""

    job_id: str = Field(min_length=1, description="作业标识")
    document_id: str = Field(min_length=1, description="文档标识")
    parse_status: ParseStatus = Field(description="解析状态")
    chunk_count: int = Field(ge=0, description="分片数")
    chunks: List[ChunkDraftView] = Field(
        default_factory=list, description="未生效草稿分片"
    )
    notices: List[str] = Field(
        default_factory=list, description="回退/降级可见提示"
    )
    strategy_effective: ChunkStrategy = Field(description="实际生效策略")


class ChunkFailureView(BaseModel):
    """报告失败条目视图（chunk_id + 固定文本原因，禁凭据）。"""

    chunk_id: str = Field(min_length=1, description="失败分片标识")
    reason: str = Field(min_length=1, description="失败原因（固定文本）")


class IngestionReportReport(BaseModel):
    """确认导入/作业查询报告（design §2.2.2 T4）。"""

    status: IngestionReportStatus = Field(
        description="终态（succeeded/partial/failed）"
    )
    chunk_count: int = Field(ge=0, description="分片总数")
    upserted_count: int = Field(ge=0, description="成功写入/更新条数")
    failed: List[ChunkFailureView] = Field(
        default_factory=list, description="失败条目（chunk_id + 原因）"
    )
    provenance: Optional[str] = Field(
        default=None, description="向量化来源（四类之一）"
    )
    local_degraded: bool = Field(
        default=False, description="是否降级（伪语义标注）"
    )
    local_degraded_reason: Optional[str] = Field(
        default=None, description="降级原因（local_degraded=True 时携带）"
    )
    duration_ms: int = Field(ge=0, description="耗时（毫秒）")


# =============================================================================
# B5：分片编辑一致性（合并/拆分/编辑 → 草稿；确认 → 仅重算受影响分片）
# =============================================================================

ChunkEditOperationKind = Literal["merge", "split", "edit"]


class ChunkOperation(BaseModel):
    """单条分片调整指令（design §2.2.2 T6 ChunkOp 判别联合的承载形态）。

    - 结构合法性（merge 需 chunk_ids、split 需 chunk_id+split_points、
      edit 需 chunk_id+text）由流水线按领域错误码校验
      （VECTOR_KB_INVALID_CHUNK_OPERATION / VECTOR_KB_CHUNK_NOT_FOUND），
      模型层不加 min_length 约束（避免 pydantic 校验替代领域拒绝，
      对齐 ChunkStrategyConfig 的分层意图）；
    - 空文本/超长/非相邻/非法拆分点 → VECTOR_KB_INVALID_CHUNK_OPERATION
      （AC-35：不得静默保存非法分片）。
    """

    op: ChunkEditOperationKind = Field(description="操作类型（merge/split/edit）")
    chunk_ids: Optional[List[str]] = Field(
        default=None, description="merge：待合并分片标识（≥2 个相邻分片）"
    )
    chunk_id: Optional[str] = Field(
        default=None, description="split/edit：目标分片标识"
    )
    split_points: Optional[List[int]] = Field(
        default=None, description="split：字符偏移切分点（严格递增，落于 (0, len(text))）"
    )
    text: Optional[str] = Field(
        default=None, description="edit：新文本（非空，≤ 2000）"
    )


class ChunkDraftResponse(BaseModel):
    """调整草稿响应（design §2.2.2 T6）。"""

    draft_version: str = Field(min_length=1, description="新草稿版本")
    chunks: List[ChunkDraftView] = Field(
        default_factory=list, description="调整后草稿分片"
    )
    notices: List[str] = Field(
        default_factory=list, description="回退/降级提示（可空）"
    )


class ChunkDraftConfirmResponse(BaseModel):
    """确认调整响应（design §2.2.2 T7：仅重算受影响分片）。

    - rewritten / deleted 为 chunk_id 列表（record_id 幂等键，INV-D20）；
    - failed_at / reason 承载重算失败的受影响分片与固定文本原因
      （失败保留旧记录，INV-D21）；
    - local_degraded=True 时 local_degraded_reason 必填（INV-D08）。
    """

    status: IngestionReportStatus = Field(
        description="终态（succeeded/partial/failed）"
    )
    rewritten: List[str] = Field(
        default_factory=list, description="重算写入的分片标识"
    )
    deleted: List[str] = Field(
        default_factory=list, description="删除的被替换分片标识"
    )
    failed_at: Optional[str] = Field(
        default=None, description="失败时受影响分片标识（逗号分隔）"
    )
    reason: Optional[str] = Field(
        default=None, description="失败原因（固定文本，零凭据）"
    )
    local_degraded: bool = Field(
        default=False, description="是否降级（伪语义标注）"
    )
    local_degraded_reason: Optional[str] = Field(
        default=None, description="降级原因（local_degraded=True 时携带）"
    )


# =============================================================================
# B7：知识库管理 API 请求 / 响应模型（design §2.2.2 T1/T2/T5；零凭据）
# =============================================================================


class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库请求（design §2.2.2 T1）。

    - embedding_model 为写入形态绑定（单值必填，AC-40；base_url 仅写入
      不回读，AC-45）；
    - knowledge_base_id 白名单约束由服务层以领域错误码校验
      （VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID，禁 pydantic 校验替代领域拒绝）。
    """

    knowledge_base_id: str = Field(
        min_length=1, max_length=128, description="知识库标识"
    )
    name: str = Field(min_length=1, max_length=200, description="名称（非空）")
    embedding_model: KBEmbeddingBinding = Field(
        description="绑定模型（单值必填，INV-D17）"
    )
    default_chunk_strategy: Optional[ChunkStrategy] = Field(
        default=None, description="默认分片策略（缺省按系统默认 recursive）"
    )


class KnowledgeBaseUpdateRequest(BaseModel):
    """更新知识库请求（design §2.2.2 T2）：部分更新，None 字段不变。"""

    name: Optional[str] = Field(
        default=None, min_length=1, max_length=200, description="名称（可选）"
    )
    default_chunk_strategy: Optional[ChunkStrategy] = Field(
        default=None, description="默认分片策略（可选）"
    )
    embedding_model: Optional[KBEmbeddingBinding] = Field(
        default=None, description="改绑模型（可选；显式变更路径 AC-41）"
    )


class KnowledgeBaseView(BaseModel):
    """知识库视图（design §2.2.2 T1/T2 响应；零凭据）。

    - embedding_model 为绑定摘要（KBEmbeddingBindingSummary，
      **不含 base_url**，AC-45）；
    - document_count / chunk_count 为元数据派生计数（文档数 / 最新草稿
      分片数合计；不触向量存储）；
    - created_at / updated_at 为技术时间戳（落盘层承载）。
    """

    knowledge_base_id: str = Field(description="知识库标识")
    name: str = Field(description="名称")
    embedding_model: KBEmbeddingBindingSummary = Field(
        description="绑定模型摘要（零凭据）"
    )
    default_chunk_strategy: Optional[ChunkStrategy] = Field(
        default=None, description="默认分片策略"
    )
    document_count: int = Field(
        default=0, ge=0, description="文档数（元数据派生）"
    )
    chunk_count: int = Field(
        default=0, ge=0, description="分片数（最新草稿合计，元数据派生）"
    )
    created_at: Optional[str] = Field(
        default=None, description="创建时间（ISO-8601，技术时间戳）"
    )
    updated_at: Optional[str] = Field(
        default=None, description="更新时间（ISO-8601，技术时间戳）"
    )


class KnowledgeBaseListResponse(BaseModel):
    """知识库列表响应（design §2.2.2 T2：items + total）。"""

    items: List[KnowledgeBaseView] = Field(
        default_factory=list, description="知识库视图列表（分页切片）"
    )
    total: int = Field(default=0, ge=0, description="知识库总数")


class DeleteKnowledgeBaseResponse(BaseModel):
    """删除知识库响应（design §2.2.2 T2）。

    - purged_vectors 仅在显式请求 purge_vectors=true 且清除执行完成时
      为 True（显式破坏性操作，登记实验语义）。
    """

    knowledge_base_id: str = Field(description="知识库标识")
    purged_vectors: bool = Field(
        default=False, description="是否已清除向量集合记录"
    )


class ConfirmImportRequest(BaseModel):
    """确认导入请求（design §2.2.2 T4）。

    - snapshot_version 乐观并发（草稿被修改 → 409 DRAFT_VERSION_CONFLICT）；
    - allow_degraded_fallback 请求级兜底开关（与绑定声明取或）。
    """

    job_id: str = Field(min_length=1, description="摄取作业标识")
    snapshot_version: str = Field(
        min_length=1, description="草稿快照版本（乐观并发）"
    )
    allow_degraded_fallback: bool = Field(
        default=False, description="请求级兜底开关（默认 False）"
    )


class ChunkDraftRequest(BaseModel):
    """提交调整草稿请求（design §2.2.2 T6）。"""

    base_version: str = Field(min_length=1, description="基线草稿版本")
    operations: List[ChunkOperation] = Field(
        min_length=1, description="调整指令（merge/split/edit）"
    )


class ConfirmChunkDraftRequest(BaseModel):
    """确认调整请求（design §2.2.2 T7）。"""

    draft_version: str = Field(min_length=1, description="待确认草稿版本")


# 分片视图（design §2.2.2 T5 ChunkView）：与 ChunkRecord 同构——别名复用，
# 不造平行契约（INV-D02）
ChunkView = ChunkRecord


class ChunkListResponse(BaseModel):
    """分片列表响应（design §2.2.2 T5：分页 + 向量化计数）。"""

    items: List[ChunkView] = Field(
        default_factory=list, description="分片视图（分页切片）"
    )
    total: int = Field(default=0, ge=0, description="分片总数（全量）")
    page: int = Field(default=1, ge=1, description="当前页码")
    page_size: int = Field(default=50, ge=1, description="每页条数")
    vectorized_count: int = Field(
        default=0, ge=0, description="已向量化分片数（全量）"
    )
    degraded_count: int = Field(
        default=0, ge=0, description="降级（伪语义）分片数（全量）"
    )


# =============================================================================
# vkb_write_mode：即时写入报告（TextWriteOutcome）
# =============================================================================

# 写入终态（仅成功/部分成功；整体不可写经异常抛出，不落该状态，design §2.2.2(4)）
TextWriteStatus = Literal["succeeded", "partial"]

# 失败清单截断上界（P-3 默认裁决：保留前 100 条 + failed_truncated 标注）
TEXT_WRITE_FAILED_CAP = 100


class TextWriteOutcome(BaseModel):
    """即时写入报告（vkb_write_mode / design §2.2.2(4)）。

    - 写入模式命名输出值（节点 output_value 承载 dict）；JSON 兼容、零凭据；
    - status：succeeded（全成功）/ partial（存在失败条目）；整体不可写经异常抛出；
    - failed：复用既有 RecordFailure（record_id + 固定文本原因），不新造平行契约；
    - failed_truncated：失败清单按上界（P-3，默认 100 条）截断并显式标注，
      chunk_count/upserted_count 计数不受截断影响（成功计数不失真）；
    - 不变式（出口守护）：provenance == "hash_fallback" → local_degraded is True
      且 local_degraded_reason 非空（AC-41）。
    """

    status: TextWriteStatus = Field(description="写入终态（succeeded/partial）")
    chunk_count: int = Field(ge=0, description="分片总数")
    upserted_count: int = Field(ge=0, description="成功写入/更新条数")
    failed: List[RecordFailure] = Field(
        default_factory=list, description="失败清单（record_id + 固定文本原因）"
    )
    provenance: str = Field(description="向量化来源（四类之一）")
    local_degraded: bool = Field(description="是否降级（伪语义标注）")
    local_degraded_reason: Optional[str] = Field(
        default=None, description="降级原因（local_degraded=True 时必填）"
    )
    duration_ms: int = Field(ge=0, description="耗时（毫秒）")
    document_id: str = Field(description="写入幂等键基础文档标识")
    failed_truncated: bool = Field(
        default=False, description="失败清单是否因超上界被截断（成功计数不受影响）"
    )


# =============================================================================
# vkb_doc_annotation：文档标注模型（CHG-02 / design §2.3.2 / DC-01/DC-07）
# =============================================================================

# 标注渠道（唯一差异单点：后台必填 vs 节点缺省；design §2.3.2 / DC-06）
AnnotationChannel = Literal["backend", "workflow"]
# 一键统一小写作用范围（默认仅标注值；正文须显式 + 二次确认；DC-09/OP-07）
LowercaseScope = Literal["annotations", "annotations_and_text"]
# 批量动作终态（succeeded / partial；整体不可执行经异常抛出）
AnnotationBulkStatus = Literal["succeeded", "partial"]


class AnnotationLimits(BaseModel):
    """标注上界（design §2.3.2(a)；默认键 32 / 值 512 / 项数 32）。"""

    max_items: int = Field(default=32, ge=0, description="自定义标注项数上界")
    max_value_length: int = Field(default=512, ge=0, description="自定义标注值长度上界")
    max_name_length: int = Field(default=32, ge=0, description="自定义标注名称长度上界")


class ChunkAnnotations(BaseModel):
    """切片标注子结构（写入 ``source["annotations"]``；design §2.3.2(1)）。

    - published_at：文档发布时间（ISO-8601 或 None="未知"）；
    - ingested_at：文档入库时间（系统生成 ISO-8601，最近写入时刻，AC-05/DC-04）；
    - origin：来源信息（后台必填；节点缺省"工作流写入"）；
    - custom：用户自定义标注（名称→值，JSON 兼容、零凭据）。
    """

    published_at: Optional[str] = Field(default=None, description="文档发布时间（None=未知）")
    ingested_at: Optional[str] = Field(default=None, description="文档入库时间（系统 ISO-8601）")
    origin: str = Field(description="来源信息")
    custom: Dict[str, JsonValue] = Field(default_factory=dict, description="用户自定义标注（名称→值）")


class AnnotationInput(BaseModel):
    """标注入参（UI/节点配置 → 装配单点；design §2.3.2(3)）。"""

    origin: Optional[str] = Field(default=None, description="来源信息（后台必填/节点缺省工作流写入）")
    published_at: Optional[str] = Field(default=None, description="文档发布时间（None=未知）")
    custom: Optional[Dict[str, JsonValue]] = Field(default=None, description="用户自定义标注")


class BulkAnnotateInput(BaseModel):
    """一键标注所有切片入参（design §2.3.2(6)）。"""

    origin: Optional[str] = Field(default=None, description="来源信息（覆盖）")
    published_at: Optional[str] = Field(default=None, description="文档发布时间（None=未知）")
    custom: Optional[Dict[str, JsonValue]] = Field(default=None, description="用户自定义标注（不同名保留/同名覆盖）")


class BulkAnnotateReport(BaseModel):
    """一键标注报告（零凭据；design §2.3.2(6)）。"""

    status: AnnotationBulkStatus = Field(description="终态（succeeded/partial）")
    document_id: str = Field(description="文档标识")
    targeted_count: int = Field(ge=0, description="目标切片数")
    updated_count: int = Field(ge=0, description="成功更新切片数")
    failed: List[RecordFailure] = Field(default_factory=list, description="失败清单")
    duration_ms: int = Field(default=0, ge=0, description="耗时（毫秒）")


class LowercaseRequest(BaseModel):
    """一键统一小写请求（design §2.3.2(7)）。"""

    scope: LowercaseScope = Field(default="annotations", description="作用范围（默认仅标注值）")
    confirmed: bool = Field(default=False, description="正文/不可逆操作二次确认（未确认拒绝执行）")


class LowercaseReport(BaseModel):
    """一键统一小写报告（零凭据；design §2.3.2(7)）。"""

    status: AnnotationBulkStatus = Field(description="终态（succeeded/partial）")
    document_id: str = Field(description="文档标识")
    scope: LowercaseScope = Field(description="实际作用范围")
    converted_chunks: int = Field(ge=0, description="受影响切片数")
    converted_values: int = Field(ge=0, description="转换的标注值数")
    elapsed_ms: int = Field(default=0, ge=0, description="耗时（毫秒）")
    executed_at: str = Field(description="执行时刻（ISO-8601）")
    failed: List[RecordFailure] = Field(default_factory=list, description="失败清单")


class AnnotationPatch(BaseModel):
    """标注编辑补丁（草稿式增/改/删；design §2.3.2(8)）。"""

    chunk_id: str = Field(description="目标切片标识")
    set: Optional[Dict[str, JsonValue]] = Field(default=None, description="新增/覆盖的自定义标注（名称→值）")
    unset: Optional[List[str]] = Field(default=None, description="删除的自定义标注名称清单")


class AnnotationDraftRequest(BaseModel):
    """标注编辑草稿请求（design §2.3.2(8) / DC-07）。"""

    base_version: str = Field(default="", description="基线草稿版本（空=最新）")
    patches: List[AnnotationPatch] = Field(
        default_factory=list, description="标注补丁清单（增/改/删）"
    )


class ConfirmAnnotationDraftRequest(BaseModel):
    """确认标注编辑草稿请求（design §2.3.2(8) / DC-07）。"""

    draft_version: str = Field(min_length=1, description="待确认草稿版本")


class AnnotationAuditRecord(BaseModel):
    """标注操作审计条目（落于既有元数据落点；零凭据；design §2.3.2(8)/DC-14）。"""

    audit_id: str = Field(description="审计条目标识")
    document_id: str = Field(description="文档标识")
    action: str = Field(description="动作（bulk_annotate/lowercase/apply_draft/confirm_draft）")
    scope: Optional[str] = Field(default=None, description="作用范围（lowercase 用）")
    affected_count: int = Field(default=0, ge=0, description="受影响切片数")
    executed_at: str = Field(description="执行时刻（ISO-8601）")
    detail: Dict[str, JsonValue] = Field(default_factory=dict, description="附加详情（零凭据）")
