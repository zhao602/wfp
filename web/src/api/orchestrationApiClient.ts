// =============================================================================
// 编排平台 API 客户端（src/api/orchestrationApiClient.ts）
//
// 职责：封装对后端 /api/v1/** 接口的 HTTP 调用，提供类型安全的请求方法。
// 设计意图：
//   1. 请求管线统一于 httpClient 基元（token 注入 / 错误映射 / signal 透传），
//      本模块仅保留端点定义（arch_review_gap_b1_hardening B1-C2 / T3.1）；
//   2. 错误类型统一 ApiError 族：HTTP 级错误抛 ApiException（INV-B1-3
//      instanceof 兼容），传输级错误（网络/超时）抛 ApiError 基类 transport 层兜底；
//   3. 超时/重试沿用现值：不设超时、重试默认关闭（HC-01 红线）。
// =============================================================================

import type {
  ValidateWorkflowRequest,
  ValidateWorkflowResponse,
  CreateWorkflowRequest,
  CreateWorkflowResponse,
  GetWorkflowResponse,
  StartInstanceRequest,
  StartInstanceResponse,
  GetInstanceResponse,
  ExecutionEvent,
  RollbackRequest,
  ReplayRequest,
  ListExamplesResponse,
  ConnectorResponse,
  CreateConnectorRequest,
  ConnectionTestResult,
  DataSourceConnectionConfigPayload,
  DataSchema,
  AvailableModelsResponse,
} from "./apiTypes";
import type { WorkflowDefinition } from "../workflow-definition/definitionTypes";
import { ApiError } from "./apiError";
import { createHttpClient } from "./httpClient";
import type { ApiErrorFactory } from "./httpClient";

/** API 基础路径 */
const API_BASE = "/api/v1";

/** API 调用异常：携带后端结构化错误信息（ApiError 族薄包装，INV-B1-3：类名与 import 路径保留） */
export class ApiException extends ApiError {
  constructor(
    code: string,
    message: string,
    details: Record<string, unknown>,
    httpStatus: number,
  ) {
    super(code, message, details, httpStatus);
    this.name = "ApiException";
  }
}

// ---------------- 工具发现传输类型（GET /api/v1/tools，与 definitionTypes 分离） ----------------

/** 工具来源（后端三源聚合标注） */
export type ToolSource = "platform" | "mcp" | "local";

/** 工具清单项（后端 ToolCatalogItem 传输契约） */
export interface ToolCatalogItem {
  tool_identifier: string;
  name: string;
  description: string;
  source: ToolSource;
  permission: string;
  version: string;
}

/** 工具清单响应 */
export interface ToolCatalogResponse {
  tools: ToolCatalogItem[];
  total: number;
}

/** 从 localStorage 读取租户 ID（Q-1 默认裁决：键名 wfp_tenant_id，缺失回退 "default"） */
function getTenantId(): string {
  try {
    const tenantId = (localStorage.getItem("wfp_tenant_id") ?? "").trim();
    return tenantId !== "" ? tenantId : "default";
  } catch {
    return "default";
  }
}

/** 错误工厂：HTTP 级错误保留 ApiException（instanceof 兼容）；传输级错误抛 ApiError 基类（transport 层兜底） */
const errorFactory: ApiErrorFactory = (code, message, details, httpStatus, layer) =>
  httpStatus === undefined
    ? new ApiError(code, message, details, undefined, layer)
    : new ApiException(code, message, details, httpStatus);

/** 编排平台 HTTP 客户端（基元管线：token 注入 / 错误映射统一；无超时无重试沿用现值） */
const client = createHttpClient({ baseUrl: API_BASE, errorFactory });

/** 编排平台 API 客户端 */
export const orchestrationApiClient = {
  // ---------------- 工作流定义 ----------------

  /** POST /workflows/validate —— 校验工作流定义 */
  async validateWorkflow(
    request: ValidateWorkflowRequest,
  ): Promise<ValidateWorkflowResponse> {
    return client.request<ValidateWorkflowResponse>({
      path: "/workflows/validate",
      method: "POST",
      body: request,
    });
  },

  /** POST /workflows —— 创建工作流定义 */
  async createWorkflow(
    request: CreateWorkflowRequest,
  ): Promise<CreateWorkflowResponse> {
    return client.request<CreateWorkflowResponse>({
      path: "/workflows",
      method: "POST",
      body: request,
    });
  },

  /** GET /workflows/{id} —— 查询工作流定义 */
  async getWorkflow(definitionId: string): Promise<GetWorkflowResponse> {
    return client.request<GetWorkflowResponse>({
      path: `/workflows/${definitionId}`,
    });
  },

  // ---------------- 执行实例 ----------------

  /** POST /instances —— 启动执行实例 */
  async startInstance(
    request: StartInstanceRequest,
  ): Promise<StartInstanceResponse> {
    return client.request<StartInstanceResponse>({
      path: "/instances",
      method: "POST",
      body: request,
    });
  },

  /** GET /instances/{id} —— 查询实例状态 */
  async getInstance(instanceId: string): Promise<GetInstanceResponse> {
    return client.request<GetInstanceResponse>({
      path: `/instances/${instanceId}`,
    });
  },

  /** GET /instances/{id}/events —— 查询实例事件列表 */
  async getInstanceEvents(instanceId: string): Promise<ExecutionEvent[]> {
    return client.request<ExecutionEvent[]>({
      path: `/instances/${instanceId}/events`,
    });
  },

  // ---------------- 回滚与重放 ----------------

  /** POST /instances/{id}/rollback —— 回滚至指定检查点 */
  async rollback(
    instanceId: string,
    request: RollbackRequest,
  ): Promise<GetInstanceResponse> {
    return client.request<GetInstanceResponse>({
      path: `/instances/${instanceId}/rollback`,
      method: "POST",
      body: request,
    });
  },

  /** POST /instances/{id}/replay —— 从检查点重放 */
  async replay(
    instanceId: string,
    request: ReplayRequest,
  ): Promise<GetInstanceResponse> {
    return client.request<GetInstanceResponse>({
      path: `/instances/${instanceId}/replay`,
      method: "POST",
      body: request,
    });
  },

  // ---------------- 范例库 ----------------

  /** GET /examples —— 列出预置范例 */
  async listExamples(): Promise<ListExamplesResponse> {
    return client.request<ListExamplesResponse>({
      path: "/examples",
    });
  },

  /** GET /examples/{id} —— 加载范例工作流定义 */
  async loadExample(exampleId: string): Promise<WorkflowDefinition> {
    return client.request<WorkflowDefinition>({
      path: `/examples/${exampleId}`,
    });
  },

  // ---------------- 工具发现 ----------------

  /** GET /tools —— 三源聚合工具清单（平台内置 + MCP 市场 + 本地注册） */
  async listTools(): Promise<ToolCatalogResponse> {
    return client.request<ToolCatalogResponse>({
      path: "/tools",
    });
  },

  // ---------------- 模型候选（agent_model_selector） ----------------

  /** GET /models/available —— 运行环境已配置模型候选（主/备派生，只读） */
  async listAvailableModels(): Promise<AvailableModelsResponse> {
    return client.request<AvailableModelsResponse>({
      path: "/models/available",
    });
  },

  // ---------------- 实时数据订阅 ----------------

  /** POST /subscriptions —— 创建订阅 */
  async createSubscription(request: unknown): Promise<unknown> {
    return client.request<unknown>({
      path: "/subscriptions",
      method: "POST",
      body: request,
    });
  },

  /** GET /subscriptions —— 列出订阅 */
  async listSubscriptions(status?: string): Promise<unknown[]> {
    return client.request<unknown[]>({
      path: status ? `/subscriptions?status=${status}` : "/subscriptions",
    });
  },

  /** GET /subscriptions/{id} —— 查询订阅详情 */
  async getSubscription(subscriptionId: string): Promise<unknown> {
    return client.request<unknown>({
      path: `/subscriptions/${subscriptionId}`,
    });
  },

  /** POST /subscriptions/{id}/pause —— 暂停订阅 */
  async pauseSubscription(subscriptionId: string): Promise<unknown> {
    return client.request<unknown>({
      path: `/subscriptions/${subscriptionId}/pause`,
      method: "POST",
    });
  },

  /** POST /subscriptions/{id}/resume —— 恢复订阅 */
  async resumeSubscription(subscriptionId: string): Promise<unknown> {
    return client.request<unknown>({
      path: `/subscriptions/${subscriptionId}/resume`,
      method: "POST",
    });
  },

  /** DELETE /subscriptions/{id} —— 删除订阅（错误映射统一标准 {code,message,details} 解析；原 DELETE_FAILED 硬编码旁路并入统一管线，无调用方无测试，对照表登记） */
  async deleteSubscription(subscriptionId: string): Promise<void> {
    return client.request<void>({
      path: `/subscriptions/${subscriptionId}`,
      method: "DELETE",
      voidResponse: true,
    });
  },

  // ---------------- 企业连接器 ----------------

  /** GET /connectors/enterprise —— 列出企业连接器 */
  async listEnterpriseConnectors(tenantId: string): Promise<ConnectorResponse[]> {
    return client.request<ConnectorResponse[]>({
      path: `/connectors/enterprise?tenant_id=${encodeURIComponent(tenantId)}`,
    });
  },

  /** POST /connectors/enterprise/{id}/test —— 测试连接（signal 透传至 fetch 实现超时取消） */
  async testEnterpriseConnection(
    connectorId: string,
    signal?: AbortSignal,
  ): Promise<ConnectionTestResult> {
    return client.request<ConnectionTestResult>({
      path: `/connectors/enterprise/${connectorId}/test`,
      method: "POST",
      signal,
    });
  },

  /** POST /data-source/test —— 本地数据源连接测试（完整连接配置，signal 透传） */
  async testDataSourceConnection(
    config: DataSourceConnectionConfigPayload,
    signal?: AbortSignal,
  ): Promise<ConnectionTestResult> {
    return client.request<ConnectionTestResult>({
      path: "/data-source/test",
      method: "POST",
      body: config,
      signal,
    });
  },

  /** POST /connectors/enterprise —— 创建企业连接器（本期不主动调用，Q-3） */
  async createEnterpriseConnector(
    request: CreateConnectorRequest,
  ): Promise<ConnectorResponse> {
    return client.request<ConnectorResponse>({
      path: "/connectors/enterprise",
      method: "POST",
      body: request,
    });
  },

  /** POST /connectors/enterprise/{id}/schema —— 推断数据 Schema（signal 透传至 fetch） */
  async inferEnterpriseConnectorSchema(
    connectorId: string,
    signal?: AbortSignal,
  ): Promise<DataSchema> {
    return client.request<DataSchema>({
      path: `/connectors/enterprise/${connectorId}/schema`,
      method: "POST",
      signal,
    });
  },
};

/** 获取租户 ID（导出供组件使用） */
export { getTenantId };
