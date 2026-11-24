# =============================================================================
# ABAC 策略管理路由（api/routers/governance/abac_router.py）
#
# 职责：暴露 ABAC 策略管理端点。
# 设计意图（对应偏差修正 T95）：
#   - POST /governance/abac/policies：创建策略
#   - GET /governance/abac/policies：查询租户策略
#   - DELETE /governance/abac/policies/{policy_id}：删除策略
#   - POST /governance/abac/decide：手动触发组合决策
# =============================================================================

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import (
    AttributeSource,
    ConditionOperator,
    FailMode,
    PolicyEffect,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["ABAC 策略管理"])


# ---------------- 请求/响应模型 ----------------

class AbacConditionRequest(BaseModel):
    """ABAC 条件请求。"""
    attribute_name: str = Field(description="属性名称")
    attribute_source: AttributeSource = Field(description="属性来源")
    operator: ConditionOperator = Field(description="操作符")
    expected_value: str = Field(description="期望值")


class CreatePolicyRequest(BaseModel):
    """创建 ABAC 策略请求。"""
    policy_id: str = Field(description="策略标识")
    tenant_id: str = Field(description="租户标识")
    conditions: List[AbacConditionRequest] = Field(description="条件集合")
    effect: PolicyEffect = Field(description="效果")
    fail_mode: FailMode = Field(default=FailMode.DENY, description="失败模式")
    priority: int = Field(default=0, description="优先级")
    description: str = Field(default="", description="策略描述")


class PolicyResponse(BaseModel):
    """策略响应。"""
    policy_id: str
    tenant_id: str
    effect: PolicyEffect
    fail_mode: FailMode
    priority: int
    is_active: bool
    description: str
    conditions_count: int


# ---------------- 仓储延迟初始化 ----------------

_repository = None


def _get_repository():
    """延迟初始化 ABAC 策略仓储。"""
    global _repository
    if _repository is not None:
        return _repository
    from orchestration_platform.governance.rbac.abac.policy_repository_local import (
        LocalAbacPolicyRepository,
    )
    _repository = LocalAbacPolicyRepository()
    return _repository


# ---------------- 端点 ----------------

@router.post("/governance/abac/policies")
async def create_policy(request: CreatePolicyRequest) -> dict:
    """创建 ABAC 策略。"""
    from orchestration_platform.governance.rbac.abac.models import (
        AbacCondition,
        AbacPolicy,
    )

    repo = _get_repository()
    conditions = [
        AbacCondition(
            attribute_name=c.attribute_name,
            attribute_source=c.attribute_source,
            operator=c.operator,
            expected_value=c.expected_value,
        )
        for c in request.conditions
    ]
    policy = AbacPolicy(
        policy_id=request.policy_id,
        tenant_id=request.tenant_id,
        conditions=conditions,
        effect=request.effect,
        fail_mode=request.fail_mode,
        priority=request.priority,
        description=request.description,
    )
    try:
        policy_id = await repo.save(policy)
        return {"policy_id": policy_id, "success": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/governance/abac/policies")
async def list_policies(
    tenant_id: str = Query(description="租户标识"),
) -> List[PolicyResponse]:
    """查询租户 ABAC 策略列表。"""
    repo = _get_repository()
    policies = await repo.load_by_tenant(tenant_id)
    return [
        PolicyResponse(
            policy_id=p.policy_id,
            tenant_id=p.tenant_id,
            effect=p.effect,
            fail_mode=p.fail_mode,
            priority=p.priority,
            is_active=p.is_active,
            description=p.description,
            conditions_count=len(p.conditions),
        )
        for p in policies
    ]


@router.delete("/governance/abac/policies/{policy_id}")
async def delete_policy(
    policy_id: str,
    tenant_id: str = Query(description="租户标识"),
) -> dict:
    """软删除 ABAC 策略。"""
    repo = _get_repository()
    success = await repo.delete(policy_id, tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"策略 {policy_id} 不存在")
    return {"success": True}
