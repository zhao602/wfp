# =============================================================================
# 事件总线管理路由（api/routers/governance/event_bus_router.py）
# =============================================================================

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["事件总线管理"])


class EventBusStatusResponse(BaseModel):
    backend: str = Field(description="后端类型：kafka/in_memory")
    is_degraded: bool = Field(description="是否降级")


_event_bus_manager = None


def _get_event_bus_manager():
    global _event_bus_manager
    if _event_bus_manager is not None:
        return _event_bus_manager
    from orchestration_platform.governance.event_bus.event_bus_manager import (
        EventBusManager,
    )
    _event_bus_manager = EventBusManager()
    return _event_bus_manager


@router.get("/governance/event-bus/status")
async def event_bus_status() -> EventBusStatusResponse:
    """查询事件总线状态。"""
    mgr = _get_event_bus_manager()
    return EventBusStatusResponse(
        backend="in_memory" if mgr.is_degraded else "kafka",
        is_degraded=mgr.is_degraded,
    )


@router.get("/governance/event-bus/health")
async def event_bus_health() -> dict:
    """事件总线健康检查。"""
    mgr = _get_event_bus_manager()
    return {"healthy": True, "degraded": mgr.is_degraded}
