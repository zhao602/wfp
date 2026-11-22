# =============================================================================
# 墓碑与被遗忘权 REST API（api/routers/governance/tombstone_router.py）
# =============================================================================

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["墓碑与被遗忘权"])


class MarkTombstoneRequest(BaseModel):
    entry_id: str = Field(description="审计日志条目标识")
    operator_id: str = Field(description="操作人标识")
    tenant_id: str = Field(description="租户标识")


class ForgetUserRequest(BaseModel):
    user_id: str = Field(description="待遗忘的用户标识")
    operator_id: str = Field(description="操作人标识")
    tenant_id: str = Field(description="租户标识")


class TombstoneMetadataResponse(BaseModel):
    entry_id: str
    deleted_at: Optional[str] = None
    deleted_by: Optional[str] = None
    forget_operation_id: Optional[str] = None
    tenant_id: str
    occurred_at: str


_tombstone_service = None
_rtbf_service = None
_tombstone_query = None


def _get_services():
    """延迟初始化墓碑服务。"""
    global _tombstone_service, _rtbf_service, _tombstone_query
    if _tombstone_service is not None:
        return _tombstone_service, _rtbf_service, _tombstone_query

    from orchestration_platform.governance.audit.tombstone.tombstone_service import (
        TombstoneService,
    )
    from orchestration_platform.governance.audit.tombstone.right_to_be_forgotten_service import (
        RightToBeForgottenService,
    )
    from orchestration_platform.governance.audit.tombstone.tombstone_query_service import (
        TombstoneQueryService,
    )
    from orchestration_platform.governance.persistence.local_file.local_file_audit_log_repository import (
        LocalFileAuditLogRepository,
    )

    repo = LocalFileAuditLogRepository()
    _tombstone_service = TombstoneService(repo)
    _rtbf_service = RightToBeForgottenService(_tombstone_service, repo)
    _tombstone_query = TombstoneQueryService(repo)
    return _tombstone_service, _rtbf_service, _tombstone_query


@router.post("/governance/audit/tombstone/mark")
async def mark_tombstone(request: MarkTombstoneRequest) -> dict:
    """单条墓碑标记。"""
    svc, _, _ = _get_services()
    result = await svc.mark_tombstone(
        entry_id=request.entry_id,
        operator_id=request.operator_id,
        tenant_id=request.tenant_id,
    )
    return {"success": result.success, "entry_id": result.entry_id, "error_code": result.error_code}


@router.post("/governance/audit/tombstone/forget-user")
async def forget_user(request: ForgetUserRequest) -> dict:
    """被遗忘权批量标记。"""
    _, svc, _ = _get_services()
    result = await svc.forget_user(
        user_id=request.user_id,
        operator_id=request.operator_id,
        tenant_id=request.tenant_id,
    )
    return {
        "success_count": result.success_count,
        "failed_count": result.failed_count,
        "total_count": result.total_count,
        "operation_log_id": result.operation_log_id,
    }


@router.get("/governance/audit/tombstone")
async def list_tombstones(
    tenant_id: str = Query(description="租户标识"),
) -> List[TombstoneMetadataResponse]:
    """查询墓碑记录。"""
    _, _, query_svc = _get_services()
    tombstones = await query_svc.query_tombstones(tenant_id)
    return [
        TombstoneMetadataResponse(
            entry_id=t.entry_id,
            deleted_at=t.deleted_at.isoformat() if t.deleted_at else None,
            deleted_by=t.deleted_by,
            forget_operation_id=t.forget_operation_id,
            tenant_id=t.tenant_id,
            occurred_at=t.occurred_at.isoformat(),
        )
        for t in tombstones
    ]


@router.delete("/governance/audit/logs/{entry_id}")
async def physical_delete(entry_id: str) -> dict:
    """物理删除拒绝端点（始终返回403）。"""
    raise HTTPException(
        status_code=403,
        detail="物理删除被禁止，请使用墓碑标记（POST /governance/audit/tombstone/mark）",
    )
