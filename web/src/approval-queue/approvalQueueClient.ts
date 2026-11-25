// =============================================================================
// 审批队列 API 客户端（web/src/approval-queue/approvalQueueClient.ts，B16 F4）
//
// 请求管线统一于 httpClient 基元（B1 批次 T3.3）；本模块仅保留端点定义。
// 设计意图：
//   1. 租户范围由服务端裁定（RBAC 激活时 request.state.tenant_id 优先），
//      前端不传 tenant_id 查询参数，防降级误越权；
//   2. 无鉴权：空 provider 压制基元默认 token 注入（Authorization 外形零变化）；
//   3. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type {
  ApprovalRow,
  BatchReviewRequest,
  BatchReviewResponse,
} from './approvalQueueTypes';
import { createHttpClient } from '../api/httpClient';

const client = createHttpClient({
  baseUrl: '/api/v1',
  getAccessToken: () => '',
});

/** 拉取待审批队列（按 suspended_at 降序） */
export async function listApprovals(): Promise<ApprovalRow[]> {
  const data = await client.request<{ approvals: ApprovalRow[] }>({
    path: '/approvals',
  });
  return data.approvals;
}

/** 批量复核（approve / reject / timeout_action；instance_ids 1..100） */
export async function batchReview(request: BatchReviewRequest): Promise<BatchReviewResponse> {
  return client.request<BatchReviewResponse>({
    path: '/approvals/batch-review',
    method: 'POST',
    body: request,
  });
}
