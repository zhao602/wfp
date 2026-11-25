// =============================================================================
// 订阅管理 API 客户端（web/src/subscription-management/subscriptionClient.ts，B16 F5）
//
// 请求管线统一于 httpClient 基元（B1 批次 T3.3）；本模块仅保留端点定义。
// 设计意图：
//   1. `:action` 冒号动作路径直接拼 URL（trigger_schedule 先例，
//      design D7「:action 冒号路径直接拼」）；
//   2. 列表不过滤租户（运营视角全量；后端 tenant_id 可选过滤保留）；
//   3. 无鉴权：空 provider 压制基元默认 token 注入（Authorization 外形零变化）；
//   4. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type {
  Subscription,
  SubscriptionUpsertRequest,
  RotatedSecretResponse,
  DeliveryRecord,
  DeadLetterEntry,
  RedriveResponse,
} from './subscriptionTypes';
import { createHttpClient } from '../api/httpClient';

const client = createHttpClient({
  baseUrl: '/api/v1',
  getAccessToken: () => '',
});

/** 列出全部订阅 */
export async function listSubscriptions(): Promise<Subscription[]> {
  const data = await client.request<{ subscriptions: Subscription[] }>({
    path: '/event-export/subscriptions',
  });
  return data.subscriptions;
}

/** 注册/更新订阅（upsert by subscription_id；校验失败 422） */
export async function upsertSubscription(
  request: SubscriptionUpsertRequest,
): Promise<Subscription> {
  return client.request<Subscription>({
    path: '/event-export/subscriptions',
    method: 'POST',
    body: request,
  });
}

/** 删除订阅（未注册 404） */
export async function deleteSubscription(subscriptionId: string): Promise<{ deleted: string }> {
  return client.request<{ deleted: string }>({
    path: `/event-export/subscriptions/${encodeURIComponent(subscriptionId)}`,
    method: 'DELETE',
  });
}

/** 启用订阅（冒号动作路径） */
export async function enableSubscription(subscriptionId: string): Promise<Subscription> {
  return client.request<Subscription>({
    path: `/event-export/subscriptions/${encodeURIComponent(subscriptionId)}:enable`,
    method: 'POST',
  });
}

/** 停用订阅（软停用，不删除注册） */
export async function disableSubscription(subscriptionId: string): Promise<Subscription> {
  return client.request<Subscription>({
    path: `/event-export/subscriptions/${encodeURIComponent(subscriptionId)}:disable`,
    method: 'POST',
  });
}

/** 轮换密钥（服务端生成；响应含一次性 secret 明文，D6） */
export async function rotateSecret(subscriptionId: string): Promise<RotatedSecretResponse> {
  return client.request<RotatedSecretResponse>({
    path: `/event-export/subscriptions/${encodeURIComponent(subscriptionId)}:rotate-secret`,
    method: 'POST',
  });
}

/** 列出订阅投递记录（新到旧） */
export async function listDeliveries(subscriptionId: string): Promise<DeliveryRecord[]> {
  const data = await client.request<{ deliveries: DeliveryRecord[] }>({
    path: `/event-export/subscriptions/${encodeURIComponent(subscriptionId)}/deliveries`,
  });
  return data.deliveries;
}

/** 列出死信条目（旧到新） */
export async function listDeadLetters(): Promise<DeadLetterEntry[]> {
  const data = await client.request<{ dead_letters: DeadLetterEntry[] }>({
    path: '/event-export/dead-letters',
  });
  return data.dead_letters;
}

/** 死信重投（成功移出 DLQ；条目不存在 404） */
export async function redriveDeadLetter(entryId: string): Promise<RedriveResponse> {
  return client.request<RedriveResponse>({
    path: `/event-export/dead-letters/${encodeURIComponent(entryId)}:redrive`,
    method: 'POST',
  });
}
