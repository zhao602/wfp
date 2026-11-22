// =============================================================================
// NL 编排 API 客户端（nlOrchestrationApiClient.ts）
//
// 职责：封装自然语言编排 API 调用。
// 设计意图（对应偏差修正 T86 端点）：
//   - createFromNl：调用 POST /api/v1/nl_orchestration/create
//   - modifyFromNl：调用 POST /api/v1/nl_orchestration/modify
//   - 后端主错误路径为带内错误（200 + success:false + error_code，经响应体呈现）；
//     传输级/HTTP 级错误（非 2xx）由基元统一抛 ApiError（原无 response.ok 检查，
//     迁移补齐——缺陷修复，登记于 design §4.3 T3.4 迁移登记段）
// =============================================================================

import { createHttpClient } from '../api/httpClient';
import type {
  CreateFromNlRequest,
  ModifyFromNlRequest,
  NLOrchestrationResponse,
} from './nlOrchestrationTypes';

// token 为显式函数入参（非 localStorage）：空 provider 压制基元默认注入，
// 经逐请求 Authorization 头携带（空 token 保形 "Bearer "，与原实现一致）
const client = createHttpClient({
  baseUrl: '/api/v1/nl_orchestration',
  getAccessToken: () => '',
});

export async function createFromNl(
  request: CreateFromNlRequest,
  token: string,
): Promise<NLOrchestrationResponse> {
  return client.request<NLOrchestrationResponse>({
    path: '/create',
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: request,
  });
}

export async function modifyFromNl(
  request: ModifyFromNlRequest,
  token: string,
): Promise<NLOrchestrationResponse> {
  return client.request<NLOrchestrationResponse>({
    path: '/modify',
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: request,
  });
}
