# =============================================================================
# 触发器模板清单路由（api/routers/trigger_template_router.py）
#
# 职责：提供触发器模板清单查询 REST API（第五阶段 G6/T225）。
# 设计意图（对应 spec 5.6.6、design D6）：
#   1. GET /api/v1/realtime-subscriptions/templates 返回全部预置模板
#      类型与参数中文说明；
#   2. 只读查询无副作用；
#   3. 以 trigger_template_enabled 条件注册（关闭时不注册，返回 404
#      而非改变既有路由）。
# =============================================================================

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from orchestration_platform.engine.realtime_subscription.trigger_templates.registry import (
    TriggerTemplateRegistry,
)

router = APIRouter(tags=["触发器模板"])

_registry: TriggerTemplateRegistry | None = None


def set_trigger_template_registry(registry: TriggerTemplateRegistry) -> None:
    """注入模板注册表实例（由 application_factory 调用）。"""
    global _registry
    _registry = registry


def _get_registry() -> TriggerTemplateRegistry:
    if _registry is None:
        raise HTTPException(status_code=503, detail="触发器模板注册表未初始化")
    return _registry


@router.get("/realtime-subscriptions/templates")
async def list_trigger_templates() -> List[Dict[str, Any]]:
    """列出全部预置触发器模板类型与参数中文说明（A6.1）。"""
    return _get_registry().list_templates()
