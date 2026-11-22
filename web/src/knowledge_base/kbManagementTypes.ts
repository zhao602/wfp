// =============================================================================
// 向量知识库管理前端类型（src/knowledge_base/kbManagementTypes.ts）
//
// 职责：与后端 Pydantic 模型一一对应的传输类型（vector_kb_management
// design §2.2.2 / §2.3.2；T8.1 CHG-30）。
// 设计意图（INV-D10 / AC-45）：
//   1. 零凭据：KBEmbeddingBindingSummary 无 base_url / api_key 字段位
//      （前端类型层即不承载凭据字段）；
//   2. 类型显式声明（禁隐式类型）；可选字段与后端 response_model_exclude_none
//      行为对齐（None 字段不序列化 → 可选属性）；
//   3. 响应/请求形态分离：写入形态 KBEmbeddingBinding（含 base_url，
//      仅创建/改绑请求）与展示形态 KBEmbeddingBindingSummary 严格分离。
// =============================================================================

/** Embedding 绑定模式（后端 EmbeddingBindingMode） */
export type EmbeddingBindingMode = "local" | "api";

/** 分片策略（后端 ChunkStrategy；LLM 分块本期不实现，枚举不含该项） */
export type ChunkStrategy =
  | "paragraph"
  | "fixed_length"
  | "recursive"
  | "structural"
  | "semantic";

/** 文档解析状态（后端 ParseStatus） */
export type ParseStatus = "pending" | "parsed" | "failed";

/** 摄取作业状态机（后端 IngestionJobStatus，DC-05） */
export type IngestionJobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "partial";

/** 摄取报告终态（后端 IngestionReportStatus；报告仅携带终态） */
export type IngestionReportStatus = "succeeded" | "partial" | "failed";

/** 分片调整操作类型（后端 ChunkEditOperationKind） */
export type ChunkEditOperationKind = "merge" | "split" | "edit";

/** Embedding 绑定（写入形态；创建/改绑请求用，AC-40~AC-43） */
export interface KBEmbeddingBinding {
  mode: EmbeddingBindingMode;
  model_identifier: string;
  /** 服务地址（仅写入不回读，AC-45；展示形态无此字段） */
  base_url?: string;
  timeout_seconds?: number;
  device?: string;
  allow_degraded_fallback: boolean;
}

/** Embedding 绑定摘要（展示形态；零凭据：无 base_url / api_key，INV-D10） */
export interface KBEmbeddingBindingSummary {
  mode: EmbeddingBindingMode;
  model_identifier: string;
  timeout_seconds?: number;
  device?: string;
  allow_degraded_fallback: boolean;
}

/** 单条 Embedding 模型候选（响应字段白名单，零凭据，AC-45） */
export interface EmbeddingCandidateItem {
  model_identifier: string;
  mode: EmbeddingBindingMode;
  label?: string;
}

/** Embedding 模型候选响应（design §2.2.2 T8；空候选 → allow_manual_input=true） */
export interface EmbeddingCandidatesResponse {
  candidates: EmbeddingCandidateItem[];
  total: number;
  allow_manual_input: boolean;
}

/** 知识库视图（design §2.2.2 T1/T2 响应；embedding_model 为零凭据摘要） */
export interface KnowledgeBaseView {
  knowledge_base_id: string;
  name: string;
  embedding_model: KBEmbeddingBindingSummary;
  default_chunk_strategy?: ChunkStrategy;
  document_count: number;
  chunk_count: number;
  created_at?: string;
  updated_at?: string;
}

/** 创建知识库请求（design §2.2.2 T1；embedding_model 单值必填，INV-D17） */
export interface KnowledgeBaseCreateRequest {
  knowledge_base_id: string;
  name: string;
  embedding_model: KBEmbeddingBinding;
  default_chunk_strategy?: ChunkStrategy;
}

/** 更新知识库请求（design §2.2.2 T2；部分更新，缺省字段不变） */
export interface KnowledgeBaseUpdateRequest {
  name?: string;
  default_chunk_strategy?: ChunkStrategy;
  embedding_model?: KBEmbeddingBinding;
}

/** 知识库列表响应（design §2.2.2 T2：items + total） */
export interface KnowledgeBaseListResponse {
  items: KnowledgeBaseView[];
  total: number;
}

/** 删除知识库响应（design §2.2.2 T2；purged_vectors 仅显式请求且完成时 true） */
export interface DeleteKnowledgeBaseResponse {
  knowledge_base_id: string;
  purged_vectors: boolean;
}

/** 单分片草稿视图（后端 ChunkDraftView ≡ ChunkDraft） */
export interface ChunkDraftView {
  chunk_id: string;
  index: number;
  text: string;
  source?: Record<string, unknown>;
}

/** 导入受理响应（design §2.2.2 T3：草稿 + 作业 pending，零写存储） */
export interface ImportDraftResponse {
  job_id: string;
  document_id: string;
  parse_status: ParseStatus;
  chunk_count: number;
  chunks: ChunkDraftView[];
  notices: string[];
  strategy_effective: ChunkStrategy;
}

/** 确认导入请求（design §2.2.2 T4；snapshot_version 乐观并发） */
export interface ConfirmImportRequest {
  job_id: string;
  snapshot_version: string;
  allow_degraded_fallback: boolean;
}

/** 报告失败条目视图（chunk_id + 固定文本原因） */
export interface ChunkFailureView {
  chunk_id: string;
  reason: string;
}

/** 确认导入报告（design §2.2.2 T4） */
export interface IngestionReportReport {
  status: IngestionReportStatus;
  chunk_count: number;
  upserted_count: number;
  failed: ChunkFailureView[];
  provenance?: string;
  local_degraded: boolean;
  local_degraded_reason?: string;
  duration_ms: number;
}

/** 摄取结果摘要（作业内嵌；failed 复用后端 RecordFailure 形态） */
export interface IngestionResult {
  chunk_count: number;
  upserted_count: number;
  failed: Array<{ record_id: string; reason: string }>;
  local_degraded_reason?: string;
  duration_ms: number;
}

/** 摄取作业（DC-05 两段式状态机载体） */
export interface IngestionJob {
  job_id: string;
  knowledge_base_id: string;
  document_id: string;
  status: IngestionJobStatus;
  result?: IngestionResult;
  provenance?: string;
  local_degraded: boolean;
}

/** 单条分片调整指令（design §2.2.2 T6 ChunkOp 判别联合的承载形态） */
export interface ChunkOperation {
  op: ChunkEditOperationKind;
  /** merge：待合并分片标识（≥2 个相邻分片） */
  chunk_ids?: string[];
  /** split/edit：目标分片标识 */
  chunk_id?: string;
  /** split：字符偏移切分点（严格递增，落于 (0, len(text))） */
  split_points?: number[];
  /** edit：新文本（非空，≤ 2000） */
  text?: string;
}

/** 提交调整草稿请求（design §2.2.2 T6） */
export interface ChunkDraftRequest {
  base_version: string;
  operations: ChunkOperation[];
}

/** 调整草稿响应（design §2.2.2 T6） */
export interface ChunkDraftResponse {
  draft_version: string;
  chunks: ChunkDraftView[];
  notices: string[];
}

/** 确认调整请求（design §2.2.2 T7） */
export interface ConfirmChunkDraftRequest {
  draft_version: string;
}

/** 确认调整响应（design §2.2.2 T7：仅重算受影响分片） */
export interface ChunkDraftConfirmResponse {
  status: IngestionReportStatus;
  rewritten: string[];
  deleted: string[];
  failed_at?: string;
  reason?: string;
  local_degraded: boolean;
  local_degraded_reason?: string;
}

/** 分片视图（design §2.2.2 T5 ChunkView ≡ 后端 ChunkRecord） */
export interface ChunkView {
  chunk_id: string;
  index: number;
  text: string;
  source?: Record<string, unknown>;
  vectorized: boolean;
  provenance?: string;
  local_degraded: boolean;
}

/** 分片列表响应（design §2.2.2 T5：分页 + 向量化计数，全量口径） */
export interface ChunkListResponse {
  items: ChunkView[];
  total: number;
  page: number;
  page_size: number;
  vectorized_count: number;
  degraded_count: number;
}

/** 集合诊断报告（design §2.2.2 T9；只读透传，集合缺失 → ok=false） */
export interface DiagnosticReport {
  collection_id: string;
  ok: boolean;
  integrity_check: string;
  dimension_consistent: boolean;
  declared_dimension?: number;
  record_count: number;
  format_version: string;
  issues: string[];
}

/** 文档导入可选参数（multipart 表单字段；design §2.2.2 T3） */
export interface ImportDocumentOptions {
  strategy?: ChunkStrategy;
  max_chunk_length?: number;
  overlap?: number;
  similarity_threshold?: number;
  hierarchy?: string[];
  /** vkb_doc_annotation：标注-来源（后台必填，AC-11） */
  annotation_origin?: string;
  /** vkb_doc_annotation：标注-发布时间（null=未知，AC-13） */
  annotation_published_at?: string | null;
  /** vkb_doc_annotation：标注-自定义（名称→值，AC-38） */
  annotation_custom?: Record<string, unknown>;
}

// =============================================================================
// 文档标注类型（vkb_doc_annotation / CHG-22；禁 any，INV-15）
// =============================================================================

/** 切片标注子结构（后端 ChunkAnnotations；published_at=null 表示"未知"） */
export interface ChunkAnnotations {
  published_at: string | null;
  ingested_at?: string;
  origin: string;
  custom?: Record<string, unknown>;
}

/** 一键标注入参（后端 BulkAnnotateInput） */
export interface BulkAnnotateInput {
  origin?: string;
  published_at?: string | null;
  custom?: Record<string, unknown>;
}

/** 一键标注报告（后端 BulkAnnotateReport；零凭据） */
export interface BulkAnnotateReport {
  status: IngestionReportStatus;
  document_id: string;
  targeted_count: number;
  updated_count: number;
  failed: ChunkFailureView[];
  duration_ms: number;
}

/** 一键统一小写作用范围（默认仅标注值，OP-07） */
export type LowercaseScope = "annotations" | "annotations_and_text";

/** 一键统一小写请求（confirmed=false → 确定性拒绝执行） */
export interface LowercaseRequest {
  scope: LowercaseScope;
  confirmed: boolean;
}

/** 一键统一小写报告（零凭据） */
export interface LowercaseReport {
  status: IngestionReportStatus;
  document_id: string;
  scope: LowercaseScope;
  converted_chunks: number;
  converted_values: number;
  elapsed_ms: number;
  executed_at: string;
  failed: ChunkFailureView[];
}

/** 标注编辑补丁（增/改/删自定义标注） */
export interface AnnotationPatch {
  chunk_id: string;
  set?: Record<string, unknown>;
  unset?: string[];
}

/** 标注编辑草稿请求（base_version 空=最新） */
export interface AnnotationDraftRequest {
  base_version: string;
  patches: AnnotationPatch[];
}

/** 确认标注编辑草稿请求 */
export interface ConfirmAnnotationDraftRequest {
  draft_version: string;
}

/** 文档导入标注选项（并入 multipart 表单字段） */
export interface ImportAnnotationOptions {
  annotation_origin?: string;
  annotation_published_at?: string | null;
  annotation_custom?: Record<string, unknown>;
}

/**
 * 从切片 source 中安全窄化出 annotations（类型守卫；禁 any，INV-15）。
 * 缺项 / 形态不符 → undefined（安全降级，不抛错）。
 */
export function resolveChunkAnnotations(
  source: Record<string, unknown> | undefined,
): ChunkAnnotations | undefined {
  if (!source) return undefined;
  const raw = source["annotations"];
  if (raw === null || typeof raw !== "object") return undefined;
  const record = raw as Record<string, unknown>;
  const origin = record["origin"];
  if (typeof origin !== "string") return undefined;
  const published = record["published_at"];
  const ingested = record["ingested_at"];
  const custom = record["custom"];
  return {
    published_at: typeof published === "string" ? published : null,
    ingested_at: typeof ingested === "string" ? ingested : undefined,
    origin,
    custom:
      custom !== null && typeof custom === "object"
        ? (custom as Record<string, unknown>)
        : undefined,
  };
}

/** 标注发布时间展示文案（null → "未知"，DC-05） */
export function formatPublishedAt(annotations: ChunkAnnotations): string {
  return annotations.published_at === null ? "未知" : annotations.published_at;
}
