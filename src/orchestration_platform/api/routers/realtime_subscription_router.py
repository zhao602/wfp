# =============================================================================
# 实时数据订阅路由（api/routers/realtime_subscription_router.py）
#
# 职责：提供订阅管理的 REST API 端点。
# 设计意图（对应项目构建计划书「实时数据订阅触发」要求）：
#   1. 创建/暂停/恢复/删除订阅；
#   2. 查询订阅列表与触发历史。
# =============================================================================

from fastapi import APIRouter, HTTPException, Query

from orchestration_platform.engine.realtime_subscription.models import (
    RealtimeSubscription,
    SubscriptionCreateRequest,
    SubscriptionTriggerEvent,
)
from orchestration_platform.engine.realtime_subscription.subscription_manager import (
    RealtimeSubscriptionManager,
)

router = APIRouter(prefix="/subscriptions", tags=["实时数据订阅"])

_manager: RealtimeSubscriptionManager | None = None


def set_subscription_manager(manager: RealtimeSubscriptionManager) -> None:
    """注入订阅管理器实例（由 application_factory 调用）。"""
    global _manager
    _manager = manager


def _get_manager() -> RealtimeSubscriptionManager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="订阅管理器未初始化")
    return _manager


@router.post("", response_model=RealtimeSubscription)
async def create_subscription(body: SubscriptionCreateRequest) -> RealtimeSubscription:
    """创建并启动数据流订阅。"""
    manager = _get_manager()
    return await manager.create_subscription(body)


@router.get("", response_model=list[RealtimeSubscription])
async def list_subscriptions(
    status: str | None = Query(default=None, description="按状态过滤"),
) -> list[RealtimeSubscription]:
    """列出所有订阅（可按状态过滤）。"""
    manager = _get_manager()
    return manager.list_subscriptions(status_filter=status)


@router.get("/{subscription_id}", response_model=RealtimeSubscription)
async def get_subscription(subscription_id: str) -> RealtimeSubscription:
    """查询单个订阅详情。"""
    manager = _get_manager()
    sub = manager.get_subscription(subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    return sub


@router.post("/{subscription_id}/pause", response_model=RealtimeSubscription)
async def pause_subscription(subscription_id: str) -> RealtimeSubscription:
    """暂停订阅。"""
    manager = _get_manager()
    try:
        return await manager.pause_subscription(subscription_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{subscription_id}/resume", response_model=RealtimeSubscription)
async def resume_subscription(subscription_id: str) -> RealtimeSubscription:
    """恢复已暂停的订阅。"""
    manager = _get_manager()
    try:
        return await manager.resume_subscription(subscription_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{subscription_id}")
async def delete_subscription(subscription_id: str) -> dict:
    """删除订阅。"""
    manager = _get_manager()
    await manager.delete_subscription(subscription_id)
    return {"deleted": True}


@router.get("/{subscription_id}/history", response_model=list[SubscriptionTriggerEvent])
async def get_trigger_history(
    subscription_id: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[SubscriptionTriggerEvent]:
    """查询订阅触发历史。"""
    manager = _get_manager()
    history = manager.get_trigger_history(subscription_id=subscription_id)
    return history[-limit:]
