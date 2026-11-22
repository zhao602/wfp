// =============================================================================
// 向量知识库管理 API 客户端（src/api/kbManagementApiClient.ts）
//
// 职责：封装对后端 /api/v1/kb-management/** 接口的 HTTP 调用（T8.2 CHG-31）。
// 设计意图：
//   1. 独立模块：既有 orchestrationApiClient.ts 零改动（Z-10）；
//   2. 请求管线统一于 httpClient 基元（token 注入 / 错误映射 / multipart 支持），
//      本模块仅保留端点定义（arch_review_gap_b1_hardening B1-C2 / T3.2）；
//   3. 错误类型统一 ApiError 族：HTTP 级错误抛 KbApiException（INV-B1-3
//      instanceof 兼容），传输级错误（网络/超时）抛 ApiError 基类 transport 层兜底；
//   4. 零凭据纪律：仅发送绑定必要字段，multipart 导入不附带任何令牌之外的
//      敏感信息；全程类型安全（禁隐式类型）。
// =============================================================================

import type {
  AnnotationDraftRequest,
  BulkAnnotateInput,
  BulkAnnotateReport,
  ChunkDraftConfirmResponse,
  ChunkDraftRequest,
  ChunkDraftResponse,
  ChunkListResponse,
  ConfirmAnnotationDraftRequest,
  ConfirmChunkDraftRequest,
  ConfirmImportRequest,
  DeleteKnowledgeBaseResponse,
  DiagnosticReport,
  EmbeddingCandidatesResponse,
  ImportDocumentOptions,
  ImportDraftResponse,
  IngestionJob,
  IngestionReportReport,
  KnowledgeBaseCreateRequest,
  KnowledgeBaseListResponse,
  KnowledgeBaseUpdateRequest,
  KnowledgeBaseView,
  LowercaseReport,
  LowercaseRequest,
} from "../knowledge_base/kbManagementTypes";
import { ApiError } from "./apiError";
import { createHttpClient } from "./httpClient";
import type { ApiErrorFactory } from "./httpClient";

/** API 基础路径（与 orchestrationApiClient 一致，作为 httpClient baseUrl） */
const API_BASE = "/api/v1";

/** 向量知识库管理路由前缀（T7.4：prefix="/kb-management"；httpClient path 前缀） */
const KBM_BASE = "/kb-management";

/** API 调用异常：携带后端结构化错误信息（ApiError 族薄包装，INV-B1-3：类名与 import 路径保留） */
export class KbApiException extends ApiError {
  constructor(
    code: string,
    message: string,
    details: Record<string, unknown>,
    httpStatus: number,
  ) {
    super(code, message, details, httpStatus);
    this.name = "KbApiException";
  }
}

/** 拼接查询串（跳过 undefined 参数） */
function buildQueryString(
  params: Record<string, string | number | boolean | undefined>,
): string {
  const search = new URLSearchParams();
  for (const key of Object.keys(params)) {
    const value = params[key];
    if (value !== undefined) {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query !== "" ? `?${query}` : "";
}

/** 错误工厂：HTTP 级错误保留 KbApiException（instanceof 兼容）；传输级错误抛 ApiError 基类（transport 层兜底） */
const errorFactory: ApiErrorFactory = (code, message, details, httpStatus, layer) =>
  httpStatus === undefined
    ? new ApiError(code, message, details, undefined, layer)
    : new KbApiException(code, message, details, httpStatus);

/** 向量知识库管理 HTTP 客户端（基元管线：token 注入 / 错误映射统一；无超时无重试沿用现值） */
const client = createHttpClient({ baseUrl: API_BASE, errorFactory });

/** 向量知识库管理 API 客户端（覆盖 kb-management 全部端点） */
export const kbManagementApiClient = {
  // ---------------- 知识库 CRUD（design §2.2.2 T1/T2） ----------------

  /** GET /kb-management/knowledge-bases —— 知识库列表（分页） */
  async listKnowledgeBases(
    page?: number,
    pageSize?: number,
  ): Promise<KnowledgeBaseListResponse> {
    const query = buildQueryString({ page, page_size: pageSize });
    return client.request<KnowledgeBaseListResponse>({
      path: `${KBM_BASE}/knowledge-bases${query}`,
    });
  },

  /** POST /kb-management/knowledge-bases —— 创建知识库（绑定模型单值必填） */
  async createKnowledgeBase(
    request: KnowledgeBaseCreateRequest,
  ): Promise<KnowledgeBaseView> {
    return client.request<KnowledgeBaseView>({
      path: `${KBM_BASE}/knowledge-bases`,
      method: "POST",
      body: request,
    });
  },

  /** GET /kb-management/knowledge-bases/{kb_id} —— 知识库详情 */
  async getKnowledgeBase(
    knowledgeBaseId: string,
  ): Promise<KnowledgeBaseView> {
    return client.request<KnowledgeBaseView>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}`,
    });
  },

  /** PUT /kb-management/knowledge-bases/{kb_id} —— 更新（部分更新 + 改绑前置校验） */
  async updateKnowledgeBase(
    knowledgeBaseId: string,
    request: KnowledgeBaseUpdateRequest,
  ): Promise<KnowledgeBaseView> {
    return client.request<KnowledgeBaseView>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}`,
      method: "PUT",
      body: request,
    });
  },

  /** DELETE /kb-management/knowledge-bases/{kb_id} —— 删除（可选向量清除） */
  async deleteKnowledgeBase(
    knowledgeBaseId: string,
    purgeVectors?: boolean,
  ): Promise<DeleteKnowledgeBaseResponse> {
    const query = buildQueryString({ purge_vectors: purgeVectors });
    return client.request<DeleteKnowledgeBaseResponse>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}${query}`,
      method: "DELETE",
    });
  },

  // ---------------- 文档导入（design §2.2.2 T3/T4，DC-09 两段式） ----------------

  /** POST /kb-management/knowledge-bases/{kb_id}/documents —— 上传解析为草稿（multipart，boundary 运行时生成） */
  async importDocument(
    knowledgeBaseId: string,
    file: File,
    options?: ImportDocumentOptions,
  ): Promise<ImportDraftResponse> {
    const form = new FormData();
    form.append("file", file, file.name);
    if (options?.strategy !== undefined) {
      form.append("strategy", options.strategy);
    }
    if (options?.max_chunk_length !== undefined) {
      form.append("max_chunk_length", String(options.max_chunk_length));
    }
    if (options?.overlap !== undefined) {
      form.append("overlap", String(options.overlap));
    }
    if (options?.similarity_threshold !== undefined) {
      form.append(
        "similarity_threshold",
        String(options.similarity_threshold),
      );
    }
    if (options?.hierarchy !== undefined) {
      for (const level of options.hierarchy) {
        form.append("hierarchy", level);
      }
    }
    if (options?.annotation_origin !== undefined) {
      form.append("annotation_origin", options.annotation_origin);
    }
    if (
      options?.annotation_published_at !== undefined &&
      options.annotation_published_at !== null
    ) {
      form.append("annotation_published_at", options.annotation_published_at);
    }
    if (options?.annotation_custom !== undefined) {
      form.append(
        "annotation_custom",
        JSON.stringify(options.annotation_custom),
      );
    }
    return client.request<ImportDraftResponse>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents`,
      method: "POST",
      multipart: true,
      body: form,
    });
  },

  /** POST .../documents/{document_id}/confirm —— 确认导入（快照版本乐观并发） */
  async confirmImport(
    knowledgeBaseId: string,
    documentId: string,
    request: ConfirmImportRequest,
  ): Promise<IngestionReportReport> {
    return client.request<IngestionReportReport>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(documentId)}/confirm`,
      method: "POST",
      body: request,
    });
  },

  /** GET .../jobs/{job_id} —— 摄取作业查询（DC-05 状态机） */
  async getIngestionJob(
    knowledgeBaseId: string,
    jobId: string,
  ): Promise<IngestionJob> {
    return client.request<IngestionJob>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/jobs/${encodeURIComponent(jobId)}`,
    });
  },

  // ---------------- 分片查看与调整（design §2.2.2 T5~T7） ----------------

  /** GET .../documents/{document_id}/chunks —— 分片列表（分页 + 向量化计数） */
  async listChunks(
    knowledgeBaseId: string,
    documentId: string,
    page?: number,
    pageSize?: number,
    includeVectorStatus?: boolean,
  ): Promise<ChunkListResponse> {
    const query = buildQueryString({
      page,
      page_size: pageSize,
      include_vector_status: includeVectorStatus,
    });
    return client.request<ChunkListResponse>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(documentId)}/chunks${query}`,
    });
  },

  /** POST .../documents/{document_id}/chunk-drafts —— 提交调整草稿（本地预览） */
  async applyChunkDraft(
    knowledgeBaseId: string,
    documentId: string,
    request: ChunkDraftRequest,
  ): Promise<ChunkDraftResponse> {
    return client.request<ChunkDraftResponse>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(documentId)}/chunk-drafts`,
      method: "POST",
      body: request,
    });
  },

  /** POST .../documents/{document_id}/chunk-drafts/confirm —— 确认草稿（触发向量重算） */
  async confirmChunkDraft(
    knowledgeBaseId: string,
    documentId: string,
    request: ConfirmChunkDraftRequest,
  ): Promise<ChunkDraftConfirmResponse> {
    return client.request<ChunkDraftConfirmResponse>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(documentId)}/chunk-drafts/confirm`,
      method: "POST",
      body: request,
    });
  },

  // ---------------- Embedding 模型候选（design §2.2.2 T8） ----------------

  /** GET /kb-management/embedding-models —— 候选列表（零凭据白名单） */
  async listEmbeddingModels(): Promise<EmbeddingCandidatesResponse> {
    return client.request<EmbeddingCandidatesResponse>({
      path: `${KBM_BASE}/embedding-models`,
    });
  },

  // ---------------- 集合诊断（design §2.2.2 T9，只读透传） ----------------

  /** GET /kb-management/knowledge-bases/{kb_id}/diagnosis —— 向量集合诊断 */
  async diagnoseKnowledgeBase(
    knowledgeBaseId: string,
  ): Promise<DiagnosticReport> {
    return client.request<DiagnosticReport>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/diagnosis`,
    });
  },

  // ---------------- 文档标注（vkb_doc_annotation / CHG-23） ----------------

  /** POST .../documents/{document_id}/annotate —— 一键标注当前文档全部切片 */
  async bulkAnnotate(
    knowledgeBaseId: string,
    documentId: string,
    request: BulkAnnotateInput,
  ): Promise<BulkAnnotateReport> {
    return client.request<BulkAnnotateReport>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(documentId)}/annotate`,
      method: "POST",
      body: request,
    });
  },

  /** POST .../documents/{document_id}/lowercase —— 一键统一小写（不可逆） */
  async lowercaseAnnotations(
    knowledgeBaseId: string,
    documentId: string,
    request: LowercaseRequest,
  ): Promise<LowercaseReport> {
    return client.request<LowercaseReport>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(documentId)}/lowercase`,
      method: "POST",
      body: request,
    });
  },

  /** POST .../documents/{document_id}/annotation-draft —— 提交标注编辑草稿 */
  async applyAnnotationDraft(
    knowledgeBaseId: string,
    documentId: string,
    request: AnnotationDraftRequest,
  ): Promise<ChunkDraftResponse> {
    return client.request<ChunkDraftResponse>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(documentId)}/annotation-draft`,
      method: "POST",
      body: request,
    });
  },

  /** POST .../documents/{document_id}/annotation-draft/confirm —— 确认标注草稿 */
  async confirmAnnotationDraft(
    knowledgeBaseId: string,
    documentId: string,
    request: ConfirmAnnotationDraftRequest,
  ): Promise<ChunkDraftConfirmResponse> {
    return client.request<ChunkDraftConfirmResponse>({
      path: `${KBM_BASE}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(documentId)}/annotation-draft/confirm`,
      method: "POST",
      body: request,
    });
  },
};
