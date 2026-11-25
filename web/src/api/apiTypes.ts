// =============================================================================
// API 类型定义（src/api/apiTypes.ts）
//
// 职责：定义与后端 API 交互的请求/响应类型，与后端 api/schemas 对齐。
// =============================================================================

import type {
  WorkflowDefinition,
  ValidationIssue,
  DataSourceType,
} from "../workflow-definition/definitionTypes";

// ---------------------------------------------------------------------------
// 统一错误响应
// ---------------------------------------------------------------------------

/** 后端统一错误结构 */
export interface ApiErrorResponse {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// 工作流定义接口类型
// ---------------------------------------------------------------------------

/** POST /workflows/validate 请求体（B13 U3 修型：与后端 ValidateRequest 对齐，yaml_text 载荷） */
export interface ValidateWorkflowRequest {
  yaml_text: string;
}

/** POST /workflows/validate 响应体（B13 U3 修型：后端裸报告 model_dump，去 {report} 包装） */
export interface ValidateWorkflowResponse {
  valid: boolean;
  issues: ValidationIssue[];
  definition_id?: string | null;
}

/** POST /workflows 请求体 */
export interface CreateWorkflowRequest {
  definition: WorkflowDefinition;
}

/** POST /workflows 响应体 */
export interface CreateWorkflowResponse {
  definition_id: string;
  definition_version: number;
  created_at: string;
}

/** GET /workflows/{id} 响应体 */
export interface GetWorkflowResponse {
  definition: WorkflowDefinition;
}

// ---------------------------------------------------------------------------
// 执行实例接口类型
// ---------------------------------------------------------------------------

/** 实例状态 */
export type InstanceStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "rolled_back";

/** POST /instances 请求体 */
export interface StartInstanceRequest {
  definition_id: string;
  inputs: Record<string, unknown>;
  yaml_text?: string;
}

/** POST /instances 响应体 */
export interface StartInstanceResponse {
  instance_id: string;
  status: InstanceStatus;
  started_at: string;
}

/** GET /instances/{id} 响应体 */
export interface GetInstanceResponse {
  instance_id: string;
  definition_id: string;
  status: InstanceStatus;
  started_at: string;
  finished_at?: string;
}

/** 节点执行状态 */
export type NodeExecutionStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

/** 执行事件 */
export interface ExecutionEvent {
  event_id: string;
  instance_id: string;
  sequence: number;
  event_type: string;
  node_id?: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// 回滚与重放接口类型
// ---------------------------------------------------------------------------

/** POST /instances/{id}/rollback 请求体 */
export interface RollbackRequest {
  target_checkpoint_sequence: number;
}

/** POST /instances/{id}/replay 请求体 */
export interface ReplayRequest {
  from_checkpoint_sequence: number;
  mode: "strict_replay" | "re_execute";
}

// ---------------------------------------------------------------------------
// 范例库接口类型
// ---------------------------------------------------------------------------

/** 范例条目 */
export interface ExampleEntry {
  example_id: string;
  name: string;
  yaml_path: string;
  available: boolean;
  unavailable_reason?: string;
  missing_dependencies: string[];
}

/** GET /examples 响应体 */
export interface ListExamplesResponse {
  examples: ExampleEntry[];
}
// ---------------------------------------------------------------------------
// 企业连接器接口类型（与后端 enterprise/models.py 对齐）
// ---------------------------------------------------------------------------

/** 连接器响应（与后端 ConnectorResponse enterprise_connector_router.py:50-54 对齐） */
export interface ConnectorResponse {
  connector_id: string;
  connector_type: string;
  target_endpoint: string;
  enabled: boolean;
}

/** 创建连接器请求体 */
export interface CreateConnectorRequest {
  connector_id: string;
  tenant_id: string;
  connector_type: string;
  target_endpoint: string;
  timeout_seconds: number;
  readable_resources: string[];
  writable_resources: string[];
  enabled: boolean;
}

/** 连接测试结果（与后端 enterprise/models.py:51-56 对齐） */
export interface ConnectionTestResult {
  reachable: boolean;
  response_latency_ms: number;
  diagnosis: string;
  tested_at: string;
}

/** POST /data-source/test 请求体（与后端 models.py:47-80 DataSourceConnectionConfig 对齐） */
export interface DataSourceConnectionConfigPayload {
  source_type: DataSourceType;
  connection_reference: string;
  /** CSV / JSON */
  file_path?: string;
  encoding?: string;
  /** REST API */
  url?: string;
  method?: string;
  headers?: Record<string, string>;
  /** 数据库 */
  connection_string?: string;
  /** Hologres */
  hologres_endpoint?: string;
  hologres_port?: number;
  hologres_database?: string;
  hologres_access_key?: string;
  /** Kafka */
  kafka_brokers?: string;
  kafka_topic?: string;
  kafka_group_id?: string;
  kafka_offset?: string;
  /** Flink */
  flink_jobmanager_url?: string;
  flink_jar_id?: string;
  flink_program_args?: string;
  /** Spark */
  spark_master_url?: string;
  spark_submit_args?: string;
  spark_app_name?: string;
}

/** Schema 单字段描述（与后端 models.py:32-37 对齐） */
export interface SchemaField {
  name: string;
  type: string;
  nullable: boolean;
}

/** 数据集 Schema 推断结果（与后端 models.py:40-44 对齐） */
export interface DataSchema {
  fields: SchemaField[];
  inferred_at: string;
}

// ---------------- 模型候选传输类型（GET /api/v1/models/available，agent_model_selector） ----------------

/** 模型候选来源（后端 AvailableModelItem.source 传输契约） */
export type ModelSource = "primary" | "fallback";

/** 模型候选项（后端 AvailableModelItem 传输契约：字段白名单，无凭据字段） */
export interface AvailableModelItem {
  model_identifier: string;
  source: ModelSource;
  label?: string;
}

/** 模型候选响应（后端 AvailableModelsResponse 传输契约） */
export interface AvailableModelsResponse {
  models: AvailableModelItem[];
  total: number;
}

// ---------------------------------------------------------------------------
// 向量知识库管理（T8.3 CHG-32：仅追加；类型本体见
// ../knowledge_base/kbManagementTypes.ts，此处集中再导出便于统一引用）
// ---------------------------------------------------------------------------

export type {
  EmbeddingBindingMode,
  ChunkStrategy,
  ParseStatus,
  IngestionJobStatus,
  IngestionReportStatus,
  ChunkEditOperationKind,
  KBEmbeddingBinding,
  KBEmbeddingBindingSummary,
  EmbeddingCandidateItem,
  EmbeddingCandidatesResponse,
  KnowledgeBaseView,
  KnowledgeBaseCreateRequest,
  KnowledgeBaseUpdateRequest,
  KnowledgeBaseListResponse,
  DeleteKnowledgeBaseResponse,
  ChunkDraftView,
  ImportDraftResponse,
  ConfirmImportRequest,
  ChunkFailureView,
  IngestionReportReport,
  IngestionResult,
  IngestionJob,
  ChunkOperation,
  ChunkDraftRequest,
  ChunkDraftResponse,
  ConfirmChunkDraftRequest,
  ChunkDraftConfirmResponse,
  ChunkView,
  ChunkListResponse,
  DiagnosticReport,
  ImportDocumentOptions,
} from "../knowledge_base/kbManagementTypes";
