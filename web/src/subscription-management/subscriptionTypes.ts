// =============================================================================
// 订阅管理类型（web/src/subscription-management/subscriptionTypes.ts，B16 F5）
//
// 对齐后端 event_export_router.py 线级契约：
//   GET  /event-export/subscriptions                     → { subscriptions: Subscription[] }
//   POST /event-export/subscriptions                     → Subscription（upsert）
//   POST /event-export/subscriptions/{id}:rotate-secret   → RotatedSecretResponse（一次性明文）
//   GET  /event-export/subscriptions/{id}/deliveries      → { deliveries: DeliveryRecord[] }
//   GET  /event-export/dead-letters                      → { dead_letters: DeadLetterEntry[] }
//   POST /event-export/dead-letters/{entry_id}:redrive    → RedriveResponse
// 订阅响应不回显 secret（secret_set 布尔代替；轮换端点例外——D6 一次性明文）。
// =============================================================================

/** 订阅（序列化形态：无 secret，secret_set 布尔代替） */
export interface Subscription {
  subscription_id: string;
  tenant_id: string;
  url: string;
  event_types: string[];
  /** 定义钉住（null=匹配全部；B16 F5 D5） */
  definition_id: string | null;
  /** 版本钉住（definition_id 非空时生效） */
  definition_version: number | null;
  enabled: boolean;
  description: string;
  /** 密钥版本（自 1 起，轮换递增；D6） */
  secret_version: number;
  created_at: string;
  /** 最近轮换时间（未轮换 null） */
  rotated_at: string | null;
  secret_set: boolean;
}

/** 订阅注册/更新请求体（upsert by subscription_id） */
export interface SubscriptionUpsertRequest {
  subscription_id: string;
  tenant_id: string;
  url: string;
  secret: string;
  event_types: string[];
  definition_id?: string | null;
  definition_version?: number | null;
  enabled?: boolean;
  description?: string;
}

/** 轮换响应：序列化订阅 + 一次性 secret 明文（仅本响应回显，D6） */
export interface RotatedSecretResponse extends Subscription {
  secret: string;
}

/** 单事件投递记录（一个投递周期一条） */
export interface DeliveryRecord {
  subscription_id: string;
  event_id: string;
  event_type: string;
  instance_id: string;
  attempts: number;
  last_status: 'delivered' | 'failed';
  delivered_at: string | null;
  last_error: string;
}

/** 死信条目（重试耗尽的事件快照） */
export interface DeadLetterEntry {
  entry_id: string;
  subscription_id: string;
  event_id: string;
  event_type: string;
  instance_id: string;
  attempts: number;
  failed_at: string;
  last_error: string;
  payload_snapshot: Record<string, unknown>;
}

/** 死信重投响应：条目快照 + redelivered（true=成功移出 DLQ） */
export interface RedriveResponse extends DeadLetterEntry {
  redelivered: boolean;
}
