# =============================================================================
# 贡献发布服务（mcp_tool_market/contribution/contribution_service.py）
#
# 职责：协调贡献提交、签名校验、状态流转、安全扫描与发布。
# 设计意图（对应偏差修正 T158 / design 2.2.2.3）：
#   1. submit：打包校验→签名校验→状态机 SUBMITTED
#   2. transition：状态机流转→APPROVED→PUBLISHED 复用既有 McpToolMarket.register
#   3. rescan：重新扫描产出新报告，旧报告留档
#   4. node_contribution_enabled=False 时服务不装配
# =============================================================================

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    CONTRIBUTION_DUPLICATE,
    CONTRIBUTION_NOT_FOUND,
    DomainError,
)
from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.mcp_tool_market.contribution.models import (
    ContributionSubmitRequest,
    ContributionSubmitResult,
    ContributionTransitionRequest,
    ContributionTransitionResult,
    NodeContribution,
    ScanReport,
    StateTransitionRecord,
)
from orchestration_platform.mcp_tool_market.contribution.signature_verifier import (
    SignatureVerifier,
)
from orchestration_platform.mcp_tool_market.contribution.state_machine import (
    ContributionStateMachine,
)
from orchestration_platform.mcp_tool_market.contribution.static_security_scanner import (
    StaticSecurityScanner,
)
from orchestration_platform.plugin_sdk.package_validator import (
    NodePackageValidator,
)

_logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ContributionService:
    """贡献发布服务。

    协调签名校验、状态机、安全扫描与发布。
    node_contribution_enabled=False 时不应装配。
    """

    def __init__(
        self,
        repository: Any,
        signature_verifier: Optional[SignatureVerifier] = None,
        scanner: Optional[StaticSecurityScanner] = None,
        package_validator: Optional[NodePackageValidator] = None,
        tool_market: Any = None,
    ) -> None:
        """构造贡献服务。

        参数：
            repository        —— 贡献仓储
            signature_verifier —— 签名校验器（缺省自动创建）
            scanner           —— 安全扫描器（缺省自动创建）
            package_validator —— 包规范校验器（缺省自动创建）
            tool_market       —— 既有 McpToolMarket（发布时复用，可选）
        """
        self._repo = repository
        self._signature_verifier = signature_verifier or SignatureVerifier()
        self._scanner = scanner or StaticSecurityScanner()
        self._package_validator = package_validator or NodePackageValidator()
        self._tool_market = tool_market

    async def submit(self, request: ContributionSubmitRequest) -> ContributionSubmitResult:
        """提交贡献。

        流程：重复检查→打包校验→签名校验→创建贡献→状态机 SUBMITTED。
        签名无效直接进入 REJECTED。
        """
        # 重复提交检查
        if self._repo.exists_by_node_version(request.node_name, request.version):
            raise DomainError(
                CONTRIBUTION_DUPLICATE,
                f"贡献已存在: {request.node_name}@{request.version}",
                {"node_name": request.node_name, "version": request.version},
            )

        # 打包规范校验
        pkg_result = self._package_validator.validate(request.package_path)
        if not pkg_result.valid:
            contribution = self._create_rejected_contribution(request, "打包规范校验失败: " + "; ".join(pkg_result.errors))
            self._repo.save(contribution)
            return ContributionSubmitResult(
                contribution_id=contribution.contribution_id,
                state=contribution.state,
                accepted=False,
                rejection_reason=contribution.rejection_reason,
            )

        # 计算包摘要
        package_digest = self._package_validator.compute_digest(request.package_path)

        # 签名校验
        if not self._signature_verifier.verify(package_digest, request.signature):
            contribution = self._create_rejected_contribution(request, "签名校验失败")
            self._repo.save(contribution)
            _logger.warning("贡献签名校验失败: %s@%s", request.node_name, request.version)
            return ContributionSubmitResult(
                contribution_id=contribution.contribution_id,
                state=contribution.state,
                accepted=False,
                rejection_reason="签名校验失败",
            )

        # 创建贡献并流转至 SUBMITTED
        contribution_id = str(uuid.uuid4())
        contribution = NodeContribution(
            contribution_id=contribution_id,
            node_name=request.node_name,
            version=request.version,
            contributor_id=request.contributor_id,
            contributor_type=request.contributor_type,
            state=ContributionState.DRAFT,
            package_digest=package_digest,
            signature=request.signature,
        )

        # DRAFT → SUBMITTED
        contribution = ContributionStateMachine.transition(
            contribution,
            ContributionState.SUBMITTED,
            operator_id=request.contributor_id,
            reason="提交贡献",
        )

        self._repo.save(contribution)

        return ContributionSubmitResult(
            contribution_id=contribution_id,
            state=contribution.state,
            accepted=True,
        )

    async def transition(
        self,
        contribution_id: str,
        request: ContributionTransitionRequest,
    ) -> ContributionTransitionResult:
        """状态流转。

        SECURITY_SCAN→REVIEWING 迁移时自动执行安全扫描。
        APPROVED→PUBLISHED 迁移时复用既有 McpToolMarket.register 上架。
        """
        contribution = self._repo.get(contribution_id)
        if contribution is None:
            raise DomainError(
                CONTRIBUTION_NOT_FOUND,
                f"贡献不存在: {contribution_id}",
                {"contribution_id": contribution_id},
            )

        from_state = contribution.state

        # SECURITY_SCAN → REVIEWING：自动执行安全扫描
        if from_state == ContributionState.SECURITY_SCAN and request.to_state == ContributionState.REVIEWING:
            if contribution.manifest is None:
                return ContributionTransitionResult(
                    contribution_id=contribution_id,
                    from_state=from_state,
                    to_state=request.to_state,
                    success=False,
                    error_message="manifest 缺失，无法扫描",
                )
            report = self._scanner.scan(
                self._get_package_path(contribution),
                contribution.manifest,
            )
            contribution.scan_reports.append(report)
            if not report.passed:
                # 扫描不通过进入 REJECTED
                contribution = ContributionStateMachine.transition(
                    contribution,
                    ContributionState.REJECTED,
                    operator_id=request.operator_id,
                    reason=f"安全扫描不通过: {len(report.findings)} 项发现",
                )
                contribution.rejection_reason = "安全扫描不通过"
                self._repo.save(contribution)
                return ContributionTransitionResult(
                    contribution_id=contribution_id,
                    from_state=from_state,
                    to_state=ContributionState.REJECTED,
                    success=False,
                    error_message="安全扫描不通过",
                )

        # APPROVED → PUBLISHED：复用既有 McpToolMarket.register
        if from_state == ContributionState.APPROVED and request.to_state == ContributionState.PUBLISHED:
            if self._tool_market is not None:
                try:
                    await self._publish_to_market(contribution)
                except Exception as exc:
                    _logger.error("发布到市场失败: %s", exc, exc_info=True)
                    return ContributionTransitionResult(
                        contribution_id=contribution_id,
                        from_state=from_state,
                        to_state=request.to_state,
                        success=False,
                        error_message=f"发布失败: {exc}",
                    )

        # 执行状态机流转
        try:
            contribution = ContributionStateMachine.transition(
                contribution,
                request.to_state,
                operator_id=request.operator_id,
                reason=request.reason,
            )
        except DomainError as exc:
            self._repo.save(contribution)
            return ContributionTransitionResult(
                contribution_id=contribution_id,
                from_state=from_state,
                to_state=request.to_state,
                success=False,
                error_message=exc.message,
            )

        self._repo.save(contribution)

        return ContributionTransitionResult(
            contribution_id=contribution_id,
            from_state=from_state,
            to_state=request.to_state,
            success=True,
        )

    async def rescan(self, contribution_id: str, operator_id: str) -> ScanReport:
        """重新扫描。

        旧报告留档不可篡改（追加写）。
        """
        contribution = self._repo.get(contribution_id)
        if contribution is None:
            raise DomainError(
                CONTRIBUTION_NOT_FOUND,
                f"贡献不存在: {contribution_id}",
            )

        if contribution.manifest is None:
            raise DomainError(
                CONTRIBUTION_NOT_FOUND,
                "manifest 缺失，无法扫描",
            )

        report = self._scanner.scan(
            self._get_package_path(contribution),
            contribution.manifest,
        )
        contribution.scan_reports.append(report)
        contribution.updated_at = _utc_now()
        self._repo.save(contribution)

        return report

    def get_history(self, contribution_id: str) -> List[StateTransitionRecord]:
        """获取流转历史。"""
        contribution = self._repo.get(contribution_id)
        if contribution is None:
            raise DomainError(
                CONTRIBUTION_NOT_FOUND,
                f"贡献不存在: {contribution_id}",
            )
        return contribution.state_history

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _create_rejected_contribution(
        self,
        request: ContributionSubmitRequest,
        reason: str,
    ) -> NodeContribution:
        """创建被拒绝的贡献。"""
        contribution = NodeContribution(
            contribution_id=str(uuid.uuid4()),
            node_name=request.node_name,
            version=request.version,
            contributor_id=request.contributor_id,
            contributor_type=request.contributor_type,
            state=ContributionState.DRAFT,
            signature=request.signature,
            rejection_reason=reason,
        )
        # DRAFT → REJECTED
        contribution = ContributionStateMachine.transition(
            contribution,
            ContributionState.REJECTED,
            operator_id=request.contributor_id,
            reason=reason,
        )
        return contribution

    def _get_package_path(self, contribution: NodeContribution) -> str:
        """获取贡献包路径（从存储布局推断）。"""
        settings = get_settings()
        return f"{settings.node_contribution_storage_root}/{contribution.contribution_id}/package"

    async def _publish_to_market(self, contribution: NodeContribution) -> None:
        """发布到既有工具市场。

        复用 McpToolMarket.register 上架（决策D7 单一发布入口）。
        """
        if self._tool_market is None or contribution.manifest is None:
            return

        from orchestration_platform.mcp_tool_market.models import McpToolDescriptor

        descriptor = McpToolDescriptor(
            tool_identifier=contribution.node_name,
            name=contribution.node_name,
            description=contribution.manifest.description,
            version=contribution.version,
            input_schema=contribution.manifest.input_schema,
            output_schema=contribution.manifest.output_schema,
        )

        await self._tool_market.register(
            descriptor=descriptor,
            registered_by=contribution.contributor_id,
        )
