// =============================================================================
// ABAC API 客户端（abacApiClient.ts）
//
// 职责：封装 /api/v1/governance/abac/** 策略管理接口调用。
// 设计意图（B1 批次 T3.2）：
//   1. 请求管线统一于 httpClient 基元；本模块仅保留端点定义；
//   2. 令牌为逐调用显式入参（非 localStorage 模式）——基元默认 token 注入
//      经空 provider 压制，Authorization 由逐请求头携带（空 token 时
//      "Bearer " 原样保形）；
//   3. 错误映射统一 ApiError 族：原裸 Error 固定中文文案改为后端结构化
//      错误透出（登记于迁移对照表）；
//   4. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type {
  CreatePolicyRequest,
  PolicyResponse,
} from './abacTypes';
import { createHttpClient } from '../api/httpClient';

const API_BASE = '/api/v1/governance/abac';

const client = createHttpClient({
  baseUrl: API_BASE,
  getAccessToken: () => '',
});

/** 创建 ABAC 策略 */
export async function createPolicy(
  request: CreatePolicyRequest,
  token: string,
): Promise<{ policy_id: string; success: boolean }> {
  return client.request<{ policy_id: string; success: boolean }>({
    path: '/policies',
    method: 'POST',
    body: request,
    headers: { Authorization: `Bearer ${token}` },
  });
}

/** 查询租户策略列表 */
export async function listPolicies(
  tenantId: string,
  token: string,
): Promise<PolicyResponse[]> {
  return client.request<PolicyResponse[]>({
    path: `/policies?tenant_id=${encodeURIComponent(tenantId)}`,
    headers: { Authorization: `Bearer ${token}` },
  });
}

/** 删除 ABAC 策略 */
export async function deletePolicy(
  policyId: string,
  tenantId: string,
  token: string,
): Promise<{ success: boolean }> {
  return client.request<{ success: boolean }>({
    path: `/policies/${encodeURIComponent(policyId)}?tenant_id=${encodeURIComponent(tenantId)}`,
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  });
}
