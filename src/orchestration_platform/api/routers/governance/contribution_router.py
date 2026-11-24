# =============================================================================
# 贡献管理 REST API（api/routers/governance/contribution_router.py）
#
# 职责：贡献提交、状态流转、重新扫描与历史查询 REST 端点。
# 设计意图（对应偏差修正 T159 / design 2.4）：
#   - GET    /api/v1/market/contributions           —— 按状态/节点名筛选
#   - POST   /api/v1/market/contributions           —— 提交贡献包
#   - POST   /api/v1/market/contributions/{id}/transition —— 状态流转
#   - POST   /api/v1/market/contributions/{id}/rescan     —— 重新扫描
#   - GET    /api/v1/market/contributions/{id}/history    —— 流转历史
#   - node_contribution_enabled=False 时端点不暴露
# =============================================================================

import logging
import os
import tempfile
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from orchestration_platform.domain.errors import DomainError
from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.mcp_tool_market.contribution.contribution_repository_local import (
    LocalContributionRepository,
)
from orchestration_platform.mcp_tool_market.contribution.contribution_service import (
    ContributionService,
)
from orchestration_platform.mcp_tool_market.contribution.models import (
    ContributionTransitionRequest,
    NodeContribution,
    ScanReport,
    SignatureInfo,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["贡献市场管理"])

# 单例仓储与服务（进程内）
_repository = LocalContributionRepository()
_service = ContributionService(repository=_repository)


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------

class ContributionListResponse(BaseModel):
    """贡献列表响应。"""
    contributions: List[NodeContribution] = Field(description="贡献列表")
    total: int = Field(description="总数")


class SubmitResponse(BaseModel):
    """提交响应。"""
    contribution_id: str = Field(description="贡献标识")
    state: str = Field(description="状态")
    accepted: bool = Field(description="是否接受")
    rejection_reason: str = Field(default="", description="驳回原因")


class TransitionResponse(BaseModel):
    """流转响应。"""
    contribution_id: str = Field(description="贡献标识")
    from_state: str = Field(description="源状态")
    to_state: str = Field(description="目标状态")
    success: bool = Field(description="是否成功")
    error_message: str = Field(default="", description="错误说明")


class HistoryResponse(BaseModel):
    """历史响应。"""
    contribution_id: str = Field(description="贡献标识")
    history: List[dict] = Field(description="流转历史")


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.get("/market/contributions", response_model=ContributionListResponse)
async def list_contributions(
    state: Optional[str] = Query(default=None, description="状态筛选"),
    node_name: Optional[str] = Query(default=None, description="节点名称筛选"),
) -> ContributionListResponse:
    """列出贡献（按状态/节点名筛选）。"""
    if state is not None:
        try:
            contrib_state = ContributionState(state)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效状态: {state}")
        contributions = _repository.list_by_state(contrib_state)
    elif node_name is not None:
        contributions = _repository.list_by_node(node_name)
    else:
        # 全部状态
        contributions = []
        for s in ContributionState:
            contributions.extend(_repository.list_by_state(s))

    return ContributionListResponse(contributions=contributions, total=len(contributions))


@router.post("/market/contributions", response_model=SubmitResponse)
async def submit_contribution(
    package: UploadFile = File(..., description="节点包 ZIP 文件"),
    node_name: str = Form(..., description="节点名称"),
    version: str = Form(..., description="版本号"),
    contributor_id: str = Form(..., description="贡献者标识"),
    signature_value: str = Form(..., description="签名值"),
    signature_algorithm: str = Form(default="hmac-sha256", description="签名算法"),
) -> SubmitResponse:
    """提交贡献包（multipart 上传）。"""
    # 保存上传文件到临时目录
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = os.path.join(tmp_dir, "package.zip")
        with open(zip_path, "wb") as f:
            content = await package.read()
            f.write(content)

        # 解压
        package_dir = os.path.join(tmp_dir, "package")
        try:
            import zipfile
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(package_dir)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail={"error_code": "NODE_PACKAGE_INVALID", "error_message": f"解压失败: {exc}"},
            )

        # 构造提交请求
        from orchestration_platform.mcp_tool_market.contribution.models import (
            ContributionSubmitRequest,
        )

        request = ContributionSubmitRequest(
            node_name=node_name,
            version=version,
            contributor_id=contributor_id,
            package_path=package_dir,
            signature=SignatureInfo(
                algorithm=signature_algorithm,
                signature_value=signature_value,
                signed_by=contributor_id,
            ),
        )

        try:
            result = await _service.submit(request)
            return SubmitResponse(
                contribution_id=result.contribution_id,
                state=result.state.value,
                accepted=result.accepted,
                rejection_reason=result.rejection_reason,
            )
        except DomainError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error_code": exc.code, "error_message": exc.message, "detail": exc.details},
            )


@router.post("/market/contributions/{contribution_id}/transition", response_model=TransitionResponse)
async def transition_contribution(
    contribution_id: str,
    to_state: str = Form(..., description="目标状态"),
    operator_id: str = Form(..., description="操作人标识"),
    reason: str = Form(default="", description="流转原因"),
) -> TransitionResponse:
    """状态流转。"""
    try:
        target_state = ContributionState(to_state)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效状态: {to_state}")

    request = ContributionTransitionRequest(
        to_state=target_state,
        operator_id=operator_id,
        reason=reason,
    )

    try:
        result = await _service.transition(contribution_id, request)
        return TransitionResponse(
            contribution_id=result.contribution_id,
            from_state=result.from_state.value,
            to_state=result.to_state.value,
            success=result.success,
            error_message=result.error_message,
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_code": exc.code, "error_message": exc.message},
        )


@router.post("/market/contributions/{contribution_id}/rescan", response_model=ScanReport)
async def rescan_contribution(
    contribution_id: str,
    operator_id: str = Form(..., description="操作人标识"),
) -> ScanReport:
    """重新扫描。"""
    try:
        return await _service.rescan(contribution_id, operator_id)
    except DomainError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_code": exc.code, "error_message": exc.message},
        )


@router.get("/market/contributions/{contribution_id}/history", response_model=HistoryResponse)
async def get_history(contribution_id: str) -> HistoryResponse:
    """获取流转历史。"""
    try:
        history = _service.get_history(contribution_id)
        return HistoryResponse(
            contribution_id=contribution_id,
            history=[r.model_dump(mode="json") for r in history],
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error_code": exc.code, "error_message": exc.message},
        )
