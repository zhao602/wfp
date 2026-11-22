# =============================================================================
# 事件出口管理路由（api/routers/event_export_router.py）
#
# 职责：订阅 CRUD / 启停 / 投递记录 / 死信查询与重投 / SSE 实时流
#       （roadmap B3，design §5 API 契约）。
# 设计意图：
#   1. 经 event_export_api_enabled 开关门控（默认 False 不注册——
#      HC-07 + E-W2-08 范式，关闭时端点 404，基线零变化）；
#   2. 上下文实例（注册表/工作器/总线/topic）由应用装配期注入
#      （set_event_export_context；trigger_schedule_router 先例）；
#   3. 校验失败 422（EVENT_SUBSCRIPTION_INVALID）/ 未找到 404
#      （EVENT_SUBSCRIPTION_NOT_FOUND / EVENT_DEAD_LETTER_NOT_FOUND），
#      经全局异常处理器映射统一错误结构；
#   4. 响应不回显 secret（secret_set 布尔代替——线级契约，guide 冻结）；
#   5. SSE 每连接独立消费组（uuid），断连经 unsubscribe 清理（仅内存
#      总线实有；Kafka 后端时该清理为 no-op，消费组生命周期交由 Kafka）。
# =============================================================================

import json
import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from orchestration_platform.domain.errors import (
    EVENT_DEAD_LETTER_NOT_FOUND,
    EVENT_SUBSCRIPTION_INVALID,
    EVENT_SUBSCRIPTION_NOT_FOUND,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.domain_event import DomainEvent
from orchestration_platform.governance.event_bus.abstractions.event_bus import IEventBus
from orchestration_platform.governance.event_export.models import (
    WebhookSubscription,
    event_type_matches,
)
from orchestration_platform.governance.event_export.subscription_registry import (
    EventSubscriptionRegistry,
)
from orchestration_platform.governance.event_export.webhook_delivery_worker import (
    WebhookDeliveryWorker,
)

router = APIRouter(tags=["事件出口"])

# 模块级上下文（装配期注入）：注册表/工作器与装配共享同一内存实例。
_registry: Optional[EventSubscriptionRegistry] = None
_worker: Optional[WebhookDeliveryWorker] = None
_event_bus: Optional[IEventBus] = None
_topic: str = ""


def set_event_export_context(
    registry: Optional[EventSubscriptionRegistry],
    worker: Optional[WebhookDeliveryWorker],
    event_bus: Optional[IEventBus] = None,
    topic: str = "",
) -> None:
    """注入事件出口上下文（应用装配期调用；None 表示清理）。"""
    global _registry, _worker, _event_bus, _topic
    _registry = registry
    _worker = worker
    _event_bus = event_bus
    _topic = topic


def _require_registry() -> EventSubscriptionRegistry:
    """取注册表；未装配时 503（与扫描器共享同一注册表实例范式）。"""
    if _registry is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="事件出口注册表未装配（event_export 服务不可用）",
        )
    return _registry


def _require_worker() -> WebhookDeliveryWorker:
    """取工作器；未装配时 503。"""
    if _worker is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="事件出口工作器未装配（event_export 服务不可用）",
        )
    return _worker


def _require_event_bus() -> IEventBus:
    """取事件总线；未装配时 503（SSE 流依赖）。"""
    if _event_bus is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="事件总线未装配（事件出口流不可用）",
        )
    return _event_bus


class SubscriptionBody(BaseModel):
    """订阅注册/更新请求体（宽校验：语义校验经 WebhookSubscription，422 带错误码）。"""

    subscription_id: str = Field(description="订阅标识（upsert 主键）")
    tenant_id: str = Field(description="租户标识")
    url: str = Field(description="Webhook 回调地址（仅 http/https）")
    secret: str = Field(description="HMAC-SHA256 签名密钥")
    event_types: List[str] = Field(
        default_factory=list,
        description='事件类型过滤（空=全部；以 "." 结尾=前缀通配，如 "node."）',
    )
    enabled: bool = Field(default=True, description="是否启用")
    description: str = Field(default="", description="订阅描述")


def _serialize_subscription(subscription: WebhookSubscription) -> Dict[str, object]:
    """序列化订阅为响应结构（不回显 secret，线级契约）。"""
    data: Dict[str, object] = dict(subscription.model_dump(mode="json"))
    data.pop("secret", None)
    data["secret_set"] = True
    return data


def _subscription_or_404(
    registry: EventSubscriptionRegistry,
    subscription_id: str,
) -> WebhookSubscription:
    """取订阅；未注册 404（EVENT_SUBSCRIPTION_NOT_FOUND）。"""
    try:
        return registry.get(subscription_id)
    except KeyError:
        raise ResourceNotFoundError(
            code=EVENT_SUBSCRIPTION_NOT_FOUND,
            message=f"订阅不存在: {subscription_id}",
            details={"subscription_id": subscription_id},
        )


# =============================================================================
# 订阅管理
# =============================================================================


@router.post("/event-export/subscriptions")
async def register_subscription(body: SubscriptionBody) -> dict:
    """注册/更新订阅（upsert by subscription_id）；校验失败 422。"""
    registry = _require_registry()
    try:
        subscription = WebhookSubscription(**body.model_dump())
    except ValidationError as exc:
        errors = exc.errors()
        first = errors[0] if errors else {}
        raise WorkflowValidationError(
            code=EVENT_SUBSCRIPTION_INVALID,
            message=f"订阅校验失败: {first.get('msg', 'invalid')}",
            details={"field": str(first.get("loc", ("?",))[0])},
        )
    registered = registry.register(subscription)
    return _serialize_subscription(registered)


@router.get("/event-export/subscriptions")
async def list_subscriptions(tenant_id: Optional[str] = None) -> dict:
    """列出订阅（tenant_id 给定时按租户过滤）。"""
    registry = _require_registry()
    return {
        "subscriptions": [
            _serialize_subscription(subscription)
            for subscription in registry.list(tenant_id=tenant_id)
        ]
    }


@router.get("/event-export/subscriptions/{subscription_id}")
async def get_subscription(subscription_id: str) -> dict:
    """查询单个订阅；未注册 404。"""
    registry = _require_registry()
    subscription = _subscription_or_404(registry, subscription_id)
    return _serialize_subscription(subscription)


@router.delete("/event-export/subscriptions/{subscription_id}")
async def delete_subscription(subscription_id: str) -> dict:
    """删除订阅（幂等：未注册同样 404，不区分是否曾存在）。"""
    registry = _require_registry()
    _subscription_or_404(registry, subscription_id)
    registry.delete(subscription_id)
    return {"deleted": subscription_id}


@router.post("/event-export/subscriptions/{subscription_id}:enable")
async def enable_subscription(subscription_id: str) -> dict:
    """启用订阅（冒号动作路径，trigger_schedule 先例）；未注册 404。"""
    registry = _require_registry()
    _subscription_or_404(registry, subscription_id)
    return _serialize_subscription(registry.enable(subscription_id))


@router.post("/event-export/subscriptions/{subscription_id}:disable")
async def disable_subscription(subscription_id: str) -> dict:
    """停用订阅（软停用，不删除注册）；未注册 404。"""
    registry = _require_registry()
    _subscription_or_404(registry, subscription_id)
    return _serialize_subscription(registry.disable(subscription_id))


@router.get("/event-export/subscriptions/{subscription_id}/deliveries")
async def list_subscription_deliveries(subscription_id: str) -> dict:
    """列出订阅的投递记录（新到旧）；未注册 404。"""
    registry = _require_registry()
    worker = _require_worker()
    _subscription_or_404(registry, subscription_id)
    return {
        "deliveries": [
            record.model_dump(mode="json")
            for record in worker.list_deliveries(subscription_id)
        ]
    }


# =============================================================================
# 死信队列
# =============================================================================


@router.get("/event-export/dead-letters")
async def list_dead_letters() -> dict:
    """列出死信条目（旧到新，含载荷快照）。"""
    worker = _require_worker()
    return {
        "dead_letters": [
            entry.model_dump(mode="json") for entry in worker.list_dead_letters()
        ]
    }


@router.post("/event-export/dead-letters/{entry_id}:redrive")
async def redrive_dead_letter(entry_id: str) -> dict:
    """死信重投：完整投递周期，成功移出 DLQ；条目不存在 404。"""
    worker = _require_worker()
    try:
        entry = await worker.redrive(entry_id)
    except KeyError:
        raise ResourceNotFoundError(
            code=EVENT_DEAD_LETTER_NOT_FOUND,
            message=f"死信条目不存在: {entry_id}",
            details={"entry_id": entry_id},
        )
    response = dict(entry.model_dump(mode="json"))
    response["redelivered"] = not any(
        item.entry_id == entry_id for item in worker.list_dead_letters()
    )
    return response


# =============================================================================
# SSE 实时流
# =============================================================================


@router.get("/event-export/stream")
async def stream_events(
    instance_id: Optional[str] = None,
    event_types: Optional[str] = None,
) -> StreamingResponse:
    """SSE 实时流（text/event-stream；data: <DomainEvent JSON>\\n\\n）。

    查询参数过滤：instance_id 精确；event_types 逗号分隔（同 event_type_matches
    语义：条目以 "." 结尾=前缀通配）。每连接独立消费组（uuid），断连清理。
    """
    bus = _require_event_bus()
    worker = _require_worker()
    patterns = [
        pattern.strip()
        for pattern in (event_types or "").split(",")
        if pattern.strip()
    ]

    async def _event_stream():
        group_id = uuid.uuid4().hex
        try:
            async for message in bus.subscribe(_topic, group_id):
                try:
                    event = DomainEvent.model_validate(
                        message.payload.get("domain_event")
                    )
                except Exception:
                    continue
                if instance_id and event.instance_id != instance_id:
                    continue
                if patterns and not event_type_matches(patterns, event.event_type):
                    continue
                data = json.dumps(
                    event.model_dump(mode="json"),
                    separators=(",", ":"),
                )
                yield f"data: {data}\n\n"
        finally:
            unsubscribe = getattr(bus, "unsubscribe", None)
            if callable(unsubscribe):
                try:
                    unsubscribe(_topic, group_id)
                except Exception:
                    pass

    del worker  # 上下文装配完整性守卫（未装配 503），流本身不依赖工作器
    return StreamingResponse(_event_stream(), media_type="text/event-stream")
