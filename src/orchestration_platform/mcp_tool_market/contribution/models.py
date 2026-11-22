# =============================================================================
# 贡献领域模型（mcp_tool_market/contribution/models.py）
#
# 职责：定义节点贡献、签名信息、扫描报告、状态流转记录等领域模型。
# 设计意图（对应偏差修正 design 2.3.2）：
#   - NodeContribution：贡献主体（含状态、manifest、签名、扫描报告、流转历史）
#   - SignatureInfo：签名算法/签名值/提交方三元组
#   - ScanReport/ScanFinding：扫描结论与逐项发现
#   - StateTransitionRecord：状态流转记录（from/to/操作人/时间/原因）
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import (
    ContributionState,
    ContributorType,
)
from orchestration_platform.plugin_sdk.models import PluginManifest


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SignatureInfo(BaseModel):
    """签名信息（算法/签名值/提交方三元组）。"""

    algorithm: str = Field(description="签名算法（hmac-sha256 / rsa-sha256）")
    signature_value: str = Field(description="签名值（十六进制）")
    signed_by: str = Field(description="签名提交方标识")
    created_at: datetime = Field(default_factory=_utc_now, description="签名时间")


class ScanFinding(BaseModel):
    """扫描单项发现。"""

    rule_id: str = Field(description="规则标识")
    severity: str = Field(description="严重级别（error/warning/info）")
    message: str = Field(description="发现说明")
    location: Optional[str] = Field(default=None, description="代码位置（文件:行号）")


class ScanReport(BaseModel):
    """扫描报告（结论 + 逐项发现）。"""

    report_id: str = Field(description="报告标识")
    passed: bool = Field(description="是否通过")
    findings: List[ScanFinding] = Field(default_factory=list, description="逐项发现")
    scanned_at: datetime = Field(default_factory=_utc_now, description="扫描时间")
    scanner_version: str = Field(default="1.0.0", description="扫描器版本")
    error: Optional[str] = Field(default=None, description="扫描器异常（失败时有效）")


class StateTransitionRecord(BaseModel):
    """状态流转记录。"""

    from_state: ContributionState = Field(description="源状态")
    to_state: ContributionState = Field(description="目标状态")
    operator_id: str = Field(description="操作人标识")
    reason: str = Field(default="", description="流转原因")
    occurred_at: datetime = Field(default_factory=_utc_now, description="流转时间")


class NodeContribution(BaseModel):
    """节点贡献主体。"""

    contribution_id: str = Field(description="贡献标识（全局唯一）")
    node_name: str = Field(description="节点名称")
    version: str = Field(description="版本号")
    contributor_id: str = Field(description="贡献者标识")
    contributor_type: ContributorType = Field(
        default=ContributorType.THIRD_PARTY, description="贡献者类型"
    )
    state: ContributionState = Field(
        default=ContributionState.DRAFT, description="当前状态"
    )
    manifest: Optional[PluginManifest] = Field(
        default=None, description="manifest（提交时填充）"
    )
    package_digest: str = Field(default="", description="包内容 SHA-256 摘要")
    signature: Optional[SignatureInfo] = Field(
        default=None, description="签名信息"
    )
    scan_reports: List[ScanReport] = Field(
        default_factory=list, description="扫描报告列表（追加写，不可篡改）"
    )
    state_history: List[StateTransitionRecord] = Field(
        default_factory=list, description="状态流转历史（按时间有序）"
    )
    rejection_reason: str = Field(default="", description="驳回原因")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
    updated_at: datetime = Field(default_factory=_utc_now, description="最后更新时间")


class ContributionSubmitRequest(BaseModel):
    """贡献提交请求。"""

    node_name: str = Field(description="节点名称")
    version: str = Field(description="版本号")
    contributor_id: str = Field(description="贡献者标识")
    contributor_type: ContributorType = Field(
        default=ContributorType.THIRD_PARTY, description="贡献者类型"
    )
    package_path: str = Field(description="节点包路径")
    signature: SignatureInfo = Field(description="签名信息")


class ContributionSubmitResult(BaseModel):
    """贡献提交结果。"""

    contribution_id: str = Field(description="贡献标识")
    state: ContributionState = Field(description="提交后状态")
    accepted: bool = Field(description="是否接受")
    rejection_reason: str = Field(default="", description="驳回原因（拒绝时有效）")


class ContributionTransitionRequest(BaseModel):
    """状态流转请求。"""

    to_state: ContributionState = Field(description="目标状态")
    operator_id: str = Field(description="操作人标识")
    reason: str = Field(default="", description="流转原因")


class ContributionTransitionResult(BaseModel):
    """状态流转结果。"""

    contribution_id: str = Field(description="贡献标识")
    from_state: ContributionState = Field(description="源状态")
    to_state: ContributionState = Field(description="目标状态")
    success: bool = Field(description="是否成功")
    error_message: str = Field(default="", description="错误说明")
