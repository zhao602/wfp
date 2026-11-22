// =============================================================================
// 墓碑 API 客户端（tombstoneApiClient.ts）
//
// 职责：封装 /api/v1/governance/audit/** 墓碑与遗忘接口调用。
// 设计意图（B1 批次 T3.2）：
//   1. 请求管线统一于 httpClient 基元；本模块仅保留端点定义；
//   2. 令牌为逐调用显式入参（非 localStorage 模式）——基元默认 token 注入
//      经空 provider 压制，Authorization 由逐请求头携带（空 token 时
//      "Bearer " 原样保形）；
//   3. 补齐错误处理：原实现未检查 response.ok（非 2xx 错误体会被当作结果
//      返回，缺陷），迁移后统一 ApiError 错误映射（缺陷修复，登记）；
//   4. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type {
  ForgetResult,
  ForgetUserRequest,
  MarkTombstoneRequest,
  TombstoneMetadataView,
  TombstoneResult,
} from './tombstoneTypes';
import { createHttpClient } from '../api/httpClient';

const API_BASE = '/api/v1/governance/audit';

const client = createHttpClient({
  baseUrl: API_BASE,
  getAccessToken: () => '',
});

/** 标记墓碑 */
export async function markTombstone(
  request: MarkTombstoneRequest,
  token: string,
): Promise<TombstoneResult> {
  return client.request<TombstoneResult>({
    path: '/tombstone/mark',
    method: 'POST',
    body: request,
    headers: { Authorization: `Bearer ${token}` },
  });
}

/** 遗忘用户（执行被标记的遗忘请求） */
export async function forgetUser(
  request: ForgetUserRequest,
  token: string,
): Promise<ForgetResult> {
  return client.request<ForgetResult>({
    path: '/tombstone/forget-user',
    method: 'POST',
    body: request,
    headers: { Authorization: `Bearer ${token}` },
  });
}

/** 列出租户全部墓碑 */
export async function listTombstones(
  tenantId: string,
  token: string,
): Promise<TombstoneMetadataView[]> {
  return client.request<TombstoneMetadataView[]>({
    path: `/tombstone?tenant_id=${encodeURIComponent(tenantId)}`,
    headers: { Authorization: `Bearer ${token}` },
  });
}
