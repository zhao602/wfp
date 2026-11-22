// =============================================================================
// 评估框架 API 客户端（web/src/evaluation/evaluationApiClient.ts）
//
// 对应后端 REST 端点（偏差修正 T191）
// 设计意图（B1 批次 T3.2）：
//   1. 请求管线统一于 httpClient 基元；本模块仅保留端点定义；
//   2. 原实现无鉴权——基元默认 token 注入经空 provider 压制（禁 localStorage
//      令牌泄漏进无鉴权端点），Authorization 请求外形零变化；
//   3. 错误映射统一 ApiError 族：原固定中文文案 / error.detail?.message 混合
//      处理改为后端结构化错误统一透出（登记于迁移对照表）；
//   4. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type {
  BaselineResponse,
  CreateBaselineRequest,
  CreateScenarioSetRequest,
  EvaluationReport,
  EvaluationStatus,
  EvaluationTaskResponse,
  ScenarioSetResponse,
  StartEvaluationRequest,
} from './evaluationTypes';
import { createHttpClient } from '../api/httpClient';

const API_BASE = '/api/v1/governance/evaluation';

const client = createHttpClient({
  baseUrl: API_BASE,
  getAccessToken: () => '',
});

/** 创建评估场景集 */
export async function createScenarioSet(
  request: CreateScenarioSetRequest,
): Promise<ScenarioSetResponse> {
  return client.request<ScenarioSetResponse>({
    path: '/scenario-sets',
    method: 'POST',
    body: request,
  });
}

/** 列出租户全部场景集 */
export async function listScenarioSets(tenantId: string): Promise<ScenarioSetResponse[]> {
  return client.request<ScenarioSetResponse[]>({
    path: `/scenario-sets?tenant_id=${encodeURIComponent(tenantId)}`,
  });
}

/** 查询场景集详情 */
export async function getScenarioSet(setId: string): Promise<ScenarioSetResponse> {
  return client.request<ScenarioSetResponse>({
    path: `/scenario-sets/${encodeURIComponent(setId)}`,
  });
}

/** 删除场景集 */
export async function deleteScenarioSet(setId: string, tenantId: string): Promise<void> {
  return client.request<void>({
    path: `/scenario-sets/${encodeURIComponent(setId)}?tenant_id=${encodeURIComponent(tenantId)}`,
    method: 'DELETE',
    voidResponse: true,
  });
}

/** 启动评估任务 */
export async function startEvaluation(
  request: StartEvaluationRequest,
): Promise<EvaluationTaskResponse> {
  return client.request<EvaluationTaskResponse>({
    path: '/tasks',
    method: 'POST',
    body: request,
  });
}

/** 列出评估任务 */
export async function listTasks(
  tenantId: string,
  scenarioSetId?: string,
): Promise<EvaluationTaskResponse[]> {
  const params = new URLSearchParams({ tenant_id: tenantId });
  if (scenarioSetId) {
    params.set('scenario_set_id', scenarioSetId);
  }
  return client.request<EvaluationTaskResponse[]>({ path: `/tasks?${params}` });
}

/** 查询评估任务详情 */
export async function getTask(taskId: string): Promise<EvaluationTaskResponse> {
  return client.request<EvaluationTaskResponse>({
    path: `/tasks/${encodeURIComponent(taskId)}`,
  });
}

/** 查询评估任务完整报告 */
export async function getTaskReport(taskId: string): Promise<EvaluationReport> {
  return client.request<EvaluationReport>({
    path: `/tasks/${encodeURIComponent(taskId)}/report`,
  });
}

/** 从已完成的基线评估任务冻结基线 */
export async function createBaseline(
  request: CreateBaselineRequest,
): Promise<BaselineResponse> {
  return client.request<BaselineResponse>({
    path: '/baselines',
    method: 'POST',
    body: request,
  });
}

/** 列出基线记录 */
export async function listBaselines(
  tenantId: string,
  scenarioSetId?: string,
): Promise<BaselineResponse[]> {
  const params = new URLSearchParams({ tenant_id: tenantId });
  if (scenarioSetId) {
    params.set('scenario_set_id', scenarioSetId);
  }
  return client.request<BaselineResponse[]>({ path: `/baselines?${params}` });
}

/** 获取评估框架状态 */
export async function getEvaluationStatus(): Promise<EvaluationStatus> {
  return client.request<EvaluationStatus>({ path: '/status' });
}
