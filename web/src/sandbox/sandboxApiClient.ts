// =============================================================================
// 沙箱 API 客户端（web/src/sandbox/sandboxApiClient.ts）
//
// 请求管线统一于 httpClient 基元（B1 批次 T3.3）；本模块仅保留端点定义。
// 设计意图：
//   1. 错误映射：非 2xx 经基元统一解析（`detail.error_message` 提取由基元三形态
//      解析承接）抛 ApiError——原裸 Error 固定中文兜底改 HTTP 兜底（登记）；
//   2. deletePolicy 成功不解析响应体（`voidResponse:true` 保 void 语义）；
//   3. 无鉴权：空 provider 压制基元默认 token 注入（Authorization 外形零变化）；
//   4. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type {
  CreatePolicyRequest,
  PolicyResponse,
  SandboxStatus,
  ViolationRecord,
} from './sandboxTypes';
import { createHttpClient } from '../api/httpClient';

const API_BASE = '/api/v1/governance/sandbox';

const client = createHttpClient({
  baseUrl: API_BASE,
  getAccessToken: () => '',
});

/** 创建沙箱策略 */
export async function createPolicy(request: CreatePolicyRequest): Promise<PolicyResponse> {
  return client.request<PolicyResponse>({
    path: '/policies',
    method: 'POST',
    body: request,
  });
}

/** 列出租户全部策略 */
export async function listPolicies(tenantId: string): Promise<PolicyResponse[]> {
  return client.request<PolicyResponse[]>({
    path: `/policies?tenant_id=${encodeURIComponent(tenantId)}`,
  });
}

/** 软删除策略 */
export async function deletePolicy(policyId: string, tenantId: string): Promise<void> {
  return client.request<void>({
    path: `/policies/${encodeURIComponent(policyId)}?tenant_id=${encodeURIComponent(tenantId)}`,
    method: 'DELETE',
    voidResponse: true,
  });
}

/** 查询违规记录 */
export async function listViolations(
  tenantId: string,
  violationType?: string,
  limit: number = 100,
): Promise<ViolationRecord[]> {
  const params = new URLSearchParams({
    tenant_id: tenantId,
    limit: String(limit),
  });
  if (violationType) {
    params.set('violation_type', violationType);
  }
  return client.request<ViolationRecord[]>({ path: `/violations?${params}` });
}

/** 获取沙箱状态 */
export async function getSandboxStatus(): Promise<SandboxStatus> {
  return client.request<SandboxStatus>({ path: '/status' });
}
