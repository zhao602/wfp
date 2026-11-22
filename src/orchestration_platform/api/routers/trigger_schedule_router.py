# =============================================================================
# 定时触发器管理路由（api/routers/trigger_schedule_router.py）
#
# 职责：触发器管理端点——列出 / 启停 / 下次触发时间查询
#       （arch_review_v2_tasks_wave2 G3，CHG-49，DC-29）。
# 设计意图：
#   1. 经 trigger_schedule_api_enabled 开关门控（默认 False 不注册，
#      HC-07 + E-W2-08 范式）——关闭时端点 404，基线零变化；
#   2. 注册表实例由应用装配期注入（与扫描器共享同一内存注册表）；
#   3. 注册时解析失败 422（SCHEDULE_INVALID_CRON，经全局异常处理器）；
#      操作未注册触发器 404（SCHEDULE_TRIGGER_NOT_FOUND）；
#   4. 无删除端点（deregister 仅内部经 disable 软停用，防误删注册——
#      Wave2b F9 落库后再评估生命周期管理，DC-29 理由 ②）；
#   5. 重启后注册表为空的已知边界经本文档明示（触发器经本 API 重新注册）。
# =============================================================================

from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orchestration_platform.application.schedule.models import TriggerSchedule
from orchestration_platform.application.schedule.schedule_scanner import (
    TriggerScheduleRegistry,
)
from orchestration_platform.domain.models.enums import FrequencyPresetEnum

router = APIRouter(tags=["定时触发器管理"])

# 注册表实例（模块级注入）：由 application_factory 在装配期注入
# （schedule_scanner_enabled / trigger_schedule_api_enabled 共享同一实例）。
_registry: Optional[TriggerScheduleRegistry] = None


def set_trigger_schedule_registry(
    registry: Optional[TriggerScheduleRegistry],
) -> None:
    """注入定时触发器注册表（应用装配期调用；None 表示清理）。"""
    global _registry
    _registry = registry


def _require_registry() -> TriggerScheduleRegistry:
    """取注册表；未装配时 503（扫描器与管理 API 共享同一注册表实例）。"""
    if _registry is None:
        raise HTTPException(
            status_code=503,
            detail="定时触发器注册表未装配（schedule 服务不可用）",
        )
    return _registry


class TriggerScheduleEnableBody(BaseModel):
    """注册/启用请求体（design §4.2 契约的请求侧子集）。"""

    workflow_id: str = Field(description="工作流定义标识")
    trigger_node_id: str = Field(description="TRIGGER 节点标识")
    schedule: str = Field(default="", description="5 段式 cron（与 frequency_preset 二选一，优先）")
    frequency_preset: Optional[FrequencyPresetEnum] = Field(
        default=None, description="10 种频率预设（与 schedule 二选一）"
    )


class NextFireResponse(BaseModel):
    """下次触发时间查询响应（design §4.2 规则 3，DC-29）。"""

    schedule_id: str
    enabled: bool
    next_fire_at: Optional[str] = None


def _serialize(schedule: TriggerSchedule) -> Dict[str, object]:
    """序列化注册表项为 API 响应结构（含派生/幂等状态）。"""
    return {
        "schedule_id": schedule.schedule_id,
        "workflow_id": schedule.workflow_id,
        "trigger_node_id": schedule.trigger_node_id,
        "schedule": schedule.schedule,
        "frequency_preset": (
            schedule.frequency_preset.value
            if schedule.frequency_preset is not None
            else None
        ),
        "enabled": schedule.enabled,
        "next_fire_at": (
            schedule.next_fire_at.isoformat()
            if schedule.next_fire_at is not None
            else None
        ),
        "last_fired_key": schedule.last_fired_key,
    }


@router.get("/trigger-schedules")
async def list_trigger_schedules() -> dict:
    """列出注册的定时触发器（含 enabled/next_fire_at/last_fired_key）。"""
    registry = _require_registry()
    return {"schedules": [_serialize(s) for s in registry.list()]}


@router.post("/trigger-schedules/{schedule_id}:enable")
async def enable_trigger_schedule(
    schedule_id: str,
    body: TriggerScheduleEnableBody,
) -> dict:
    """注册并启用定时触发器。

    路径 {schedule_id} 须为 {workflow_id}:{trigger_node_id} 组合键
    （防路径参数与请求体漂移，不一致 422）；cron/预设解析失败 422
    SCHEDULE_INVALID_CRON（注册期语义）。
    """
    registry = _require_registry()
    schedule = TriggerSchedule(
        schedule_id=schedule_id,
        workflow_id=body.workflow_id,
        trigger_node_id=body.trigger_node_id,
        schedule=body.schedule,
        frequency_preset=body.frequency_preset,
        enabled=True,
    )
    registered = registry.register(schedule)
    return _serialize(registered)


@router.post("/trigger-schedules/{schedule_id}:disable")
async def disable_trigger_schedule(schedule_id: str) -> dict:
    """停用定时触发器（软停用，不删除注册；未注册 404）。"""
    registry = _require_registry()
    return _serialize(registry.disable(schedule_id))


@router.get("/trigger-schedules/{schedule_id}/next-fire")
async def get_next_fire(schedule_id: str) -> NextFireResponse:
    """查询定时触发器下次触发时间（未注册 404）。"""
    registry = _require_registry()
    schedule = registry.get(schedule_id)
    next_fire_at = schedule.next_fire_at
    return NextFireResponse(
        schedule_id=schedule.schedule_id,
        enabled=schedule.enabled,
        next_fire_at=(
            next_fire_at.isoformat() if next_fire_at is not None else None
        ),
    )
