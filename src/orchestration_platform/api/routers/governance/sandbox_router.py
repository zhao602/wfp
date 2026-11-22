# =============================================================================
# 沙箱管理 REST API（api/routers/governance/sandbox_router.py）
#
# 职责：沙箱策略管理与违规查询 REST 端点。
# 设计意图（对应偏差修正 design 2.4）：
#   - 策略 CRUD：POST/GET/PUT/DELETE /api/v1/governance/sandbox/policies
#   - 违规查询：GET /api/v1/governance/sandbox/violations
#   - sandbox_enabled=False 时路由不注册
# =============================================================================

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.integrations.sandbox.models import SandboxPolicy
from orchestration_platform.integrations.sandbox.policy_manager import (
    SandboxPolicyManager,
)
from orchestration_platform.integrations.sandbox.policy_repository_local import (
    LocalSandboxPolicyRepository,
)
from orchestration_platform.integrations.sandbox.violation_publisher import (
    ViolationPublisher,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["沙箱管理"])

# 单例仓储与发布器（进程内）
_repository = LocalSandboxPolicyRepository()
_policy_manager = SandboxPolicyManager(_repository)
_violation_publisher = ViolationPublisher()


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------

class CreatePolicyRequest(BaseModel):
    """创建策略请求。"""
    tenant_id: str = Field(description="租户标识")
    name: str = Field(default="", description="策略名称")
    description: str = Field(default="", description="策略描述")
    isolation_mode: str = Field(default="logical", description="隔离模式")
    allow_degrade: bool = Field(default=True, description="允许降级")
    cpu_seconds_limit: float = Field(default=10.0, gt=0, description="CPU 时间上限")
    memory_mb_limit: int = Field(default=512, gt=0, description="内存上限")
    output_bytes_limit: int = Field(default=1048576, gt=0, description="输出尺寸上限")
    execution_timeout_seconds: int = Field(default=60, gt=0, description="执行超时")
    workdir: str = Field(description="工作目录")
    network_policy: str = Field(default="default_deny", description="网络策略")
    network_whitelist: List[str] = Field(default_factory=list, description="网络白名单")
    allowed_path_prefixes: List[str] = Field(default_factory=list, description="额外允许路径前缀")


class PolicyResponse(BaseModel):
    """策略响应。"""
    policy_id: str
    tenant_id: str
    name: str
    description: str
    isolation_mode: str
    is_active: bool


class ViolationResponse(BaseModel):
    """违规记录响应。"""
    record_id: str
    session_id: str
    tenant_id: str
    policy_id: str
    violation_type: str
    detail: str
    occurred_at: str


# ---------------------------------------------------------------------------
# 策略管理端点
# ---------------------------------------------------------------------------

@router.post("/governance/sandbox/policies", response_model=PolicyResponse)
async def create_policy(request: CreatePolicyRequest):
    """创建沙箱策略。"""
    from orchestration_platform.domain.models.enums import (
        IsolationMode,
        NetworkPolicyMode,
    )

    policy_id = SandboxPolicyManager.generate_policy_id()

    try:
        policy = SandboxPolicy(
            policy_id=policy_id,
            tenant_id=request.tenant_id,
            name=request.name,
            description=request.description,
            isolation_mode=IsolationMode(request.isolation_mode),
            allow_degrade=request.allow_degrade,
            cpu_seconds_limit=request.cpu_seconds_limit,
            memory_mb_limit=request.memory_mb_limit,
            output_bytes_limit=request.output_bytes_limit,
            execution_timeout_seconds=request.execution_timeout_seconds,
            workdir=request.workdir,
            network_policy=NetworkPolicyMode(request.network_policy),
            network_whitelist=request.network_whitelist,
            allowed_path_prefixes=request.allowed_path_prefixes,
        )
        await _policy_manager.save_policy(policy)
        return PolicyResponse(
            policy_id=policy.policy_id,
            tenant_id=policy.tenant_id,
            name=policy.name,
            description=policy.description,
            isolation_mode=policy.isolation_mode.value,
            is_active=policy.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={
            "error_code": "SANDBOX_POLICY_INVALID",
            "error_message": str(exc),
        })


@router.get("/governance/sandbox/policies", response_model=List[PolicyResponse])
async def list_policies(tenant_id: str = Query(..., description="租户标识")):
    """列出租户全部启用策略。"""
    policies = await _policy_manager.list_policies(tenant_id)
    return [
        PolicyResponse(
            policy_id=p.policy_id,
            tenant_id=p.tenant_id,
            name=p.name,
            description=p.description,
            isolation_mode=p.isolation_mode.value,
            is_active=p.is_active,
        )
        for p in policies
    ]


@router.delete("/governance/sandbox/policies/{policy_id}")
async def delete_policy(policy_id: str, tenant_id: str = Query(...)):
    """软删除策略。"""
    success = await _policy_manager.delete_policy(policy_id, tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail={
            "error_code": "SANDBOX_POLICY_INVALID",
            "error_message": "策略不存在",
        })
    return {"success": True}


# ---------------------------------------------------------------------------
# 违规查询端点
# ---------------------------------------------------------------------------

@router.get("/governance/sandbox/violations", response_model=List[ViolationResponse])
async def list_violations(
    tenant_id: str = Query(..., description="租户标识"),
    violation_type: Optional[str] = Query(None, description="违规类型筛选"),
    limit: int = Query(100, ge=1, le=1000, description="最大返回条数"),
):
    """查询违规记录。"""
    records = _violation_publisher.query_violations(
        tenant_id=tenant_id,
        violation_type=violation_type,
        limit=limit,
    )
    return [
        ViolationResponse(
            record_id=r.get("record_id", ""),
            session_id=r.get("session_id", ""),
            tenant_id=r.get("tenant_id", ""),
            policy_id=r.get("policy_id", ""),
            violation_type=r.get("violation_type", ""),
            detail=r.get("detail", ""),
            occurred_at=r.get("occurred_at", ""),
        )
        for r in records
    ]


@router.get("/governance/sandbox/status")
async def sandbox_status():
    """沙箱状态。"""
    from orchestration_platform.governance.degraded_mode import (
        SandboxDegradedState,
    )
    settings = get_settings()
    return {
        "sandbox_enabled": settings.sandbox_enabled,
        "default_isolation": settings.sandbox_default_isolation,
        "subprocess_mode": settings.sandbox_subprocess_mode,
        "degraded": SandboxDegradedState.get_degraded_summary(),
    }
