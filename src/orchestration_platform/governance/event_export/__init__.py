# =============================================================================
# 事件出口包（governance/event_export/__init__.py）
#
# 职责：roadmap B3「事件出口」——Webhook 投递（HMAC-SHA256 签名 + 指数退避
#       重投 + 死信队列）+ SSE 实时流的领域核心（路由/装配见 api 层）。
# 设计意图：
#   1. 契约倒置（用户裁决 2026-11-21）：本包冻结线级契约（订阅模型/签名/
#      重投语义/死信），B2 SDK 后续 1:1 包装（docs/event-export-guide.md）；
#   2. 重试态/死信为内存态 + 文档化重启边界（用户裁决）；
#   3. 0 丢失不变式（进程生命周期内）：单订阅视角每事件终态
#      ∈ {delivered, dead_lettered}，无第三态。
# =============================================================================

from orchestration_platform.governance.event_export.models import (
    DeadLetterEntry,
    DeliveryRecord,
    WebhookSubscription,
    event_type_matches,
)
from orchestration_platform.governance.event_export.signature import (
    canonical_event_body,
    sign_webhook,
    verify_webhook,
)
from orchestration_platform.governance.event_export.subscription_registry import (
    EventSubscriptionRegistry,
)
from orchestration_platform.governance.event_export.webhook_delivery_worker import (
    WebhookDeliveryWorker,
)

__all__ = [
    "DeadLetterEntry",
    "DeliveryRecord",
    "WebhookSubscription",
    "event_type_matches",
    "canonical_event_body",
    "sign_webhook",
    "verify_webhook",
    "EventSubscriptionRegistry",
    "WebhookDeliveryWorker",
]
