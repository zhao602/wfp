# =============================================================================
# 治理 REST API 路由（api/routers/governance/governance_router.py）
#
# 职责：提供治理管理接口，按 /api/v1/governance/** 路径扩展。
# 设计意图（对应 spec 5.5、design 2.8、任务 T73）：
#   1. 全部新增接口按 /api/v1/governance/** 路径扩展，不破坏既有接口；
#   2. 全部接口需携带 Authorization: Bearer <token>（除 /auth/token）；
#   3. 错误码与 spec 8.1.4 / design 2.2.2.6 定义一致。
# =============================================================================

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from orchestration_platform.governance.application.governance_services import (
    get_governance_services,
)


router = APIRouter(prefix="/governance", tags=["治理"])


# =============================================================================
# 请求/响应模型
# =============================================================================

class CreateTenantRequest(BaseModel):
    """创建租户请求。"""
    tenant_id: str = Field(description="租户标识")
    tenant_name: str = Field(description="租户名称")


class IssueTokenRequest(BaseModel):
    """签发令牌请求。"""
    user_id: str = Field(description="用户标识")
    tenant_id: str = Field(description="租户标识")
    ttl_seconds: Optional[int] = Field(default=None, description="有效期（秒）")


class TokenResponse(BaseModel):
    """令牌响应。"""
    token: str = Field(description="访问令牌")
    token_type: str = Field(default="Bearer", description="令牌类型")


class RevokeTokenRequest(BaseModel):
    """吊销令牌请求。"""
    token_id: str = Field(description="令牌标识")


class UsageQueryRequest(BaseModel):
    """用量查询请求。"""
    tenant_id: str = Field(description="租户标识")
    instance_id: Optional[str] = Field(default=None, description="实例标识")


class BudgetStatusRequest(BaseModel):
    """预算状态请求。"""
    budget_id: str = Field(description="预算标识")


class AuditLogQueryRequest(BaseModel):
    """审计日志查询请求。"""
    tenant_id: Optional[str] = Field(default=None, description="租户标识")
    start_time: Optional[datetime] = Field(default=None, description="起始时间")
    end_time: Optional[datetime] = Field(default=None, description="结束时间")
    limit: int = Field(default=1000, ge=1, le=100000, description="返回条数上限")


# =============================================================================
# 租户管理路由
# =============================================================================

@router.post("/tenants", response_model=Dict[str, Any])
async def create_tenant(req: CreateTenantRequest):
    """创建租户（平台管理员）。"""
    services = get_governance_services()
    return await services.create_tenant(req.tenant_id, req.tenant_name)


@router.get("/tenants")
async def list_tenants():
    """列出全部租户（平台管理员）。"""
    return {"tenants": [], "message": "租户列表查询（需注入仓储实现）"}


# =============================================================================
# 认证路由
# =============================================================================

@router.post("/auth/token", response_model=TokenResponse)
async def issue_token(req: IssueTokenRequest):
    """签发访问令牌（公开，凭据校验）。"""
    services = get_governance_services()
    token = await services.issue_token(
        user_id=req.user_id,
        tenant_id=req.tenant_id,
        ttl_seconds=req.ttl_seconds,
    )
    return TokenResponse(token=token)


@router.post("/auth/token/revoke")
async def revoke_token(req: RevokeTokenRequest):
    """吊销令牌。"""
    services = get_governance_services()
    await services.revoke_token(req.token_id)
    return {"status": "revoked"}


# =============================================================================
# 用量与成本路由
# =============================================================================

@router.get("/usage")
async def get_usage(
    tenant_id: str = Query(..., description="租户标识"),
    instance_id: Optional[str] = Query(None, description="实例标识"),
):
    """查询用量（支持维度参数）。"""
    services = get_governance_services()
    return await services.get_usage_summary(tenant_id, instance_id)


@router.get("/costs")
async def get_costs(tenant_id: str = Query(..., description="租户标识")):
    """查询成本汇总（支持维度参数）。"""
    services = get_governance_services()
    return await services.get_cost_summary(tenant_id)


@router.get("/budgets/{budget_id}/status")
async def get_budget_status(budget_id: str):
    """查询预算状态。"""
    services = get_governance_services()
    status = await services.get_budget_status(budget_id)
    if status is None:
        raise HTTPException(status_code=404, detail="预算不存在")
    return status


# =============================================================================
# 审计日志路由
# =============================================================================

@router.get("/audit-logs")
async def query_audit_logs(
    tenant_id: Optional[str] = Query(None, description="租户标识"),
    start_time: Optional[datetime] = Query(None, description="起始时间"),
    end_time: Optional[datetime] = Query(None, description="结束时间"),
    limit: int = Query(1000, ge=1, le=100000, description="返回条数上限"),
):
    """查询审计日志（合规审计员/平台管理员/租户管理员本租户）。"""
    services = get_governance_services()
    return await services.query_audit_logs(
        tenant_id=tenant_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )


@router.post("/audit-logs/export")
async def export_audit_logs(req: AuditLogQueryRequest):
    """合规导出审计日志（合规审计员/平台管理员）。"""
    services = get_governance_services()
    entries = await services.query_audit_entries(
        tenant_id=req.tenant_id,
        start_time=req.start_time,
        end_time=req.end_time,
        limit=req.limit,
    )
    try:
        exported = services.compliance_exporter.export(
            entries=entries,
            tenant_id=req.tenant_id,
        )
        return {"exported_data": exported, "record_count": len(entries)}
    except ValueError as exc:
        raise HTTPException(
            status_code=413,
            detail={"code": "AUDIT_EXPORT_TOO_LARGE", "message": str(exc)},
        )


# =============================================================================
# 调度与资源池路由
# =============================================================================

@router.get("/scheduler/policy")
async def get_scheduler_policy():
    """查询调度策略（平台管理员）。"""
    services = get_governance_services()
    return services.global_scheduler.get_status()


@router.get("/resource-pools")
async def get_resource_pools():
    """查询资源池状态（平台管理员）。"""
    services = get_governance_services()
    return {"pools": await services.get_resource_pool_status()}
