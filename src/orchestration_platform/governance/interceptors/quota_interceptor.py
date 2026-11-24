# =============================================================================
# 配额拦截器（governance/interceptors/quota_interceptor.py）
#
# 职责：校验租户资源配额。
# 设计意图（对应 spec 5.1、design 2.2.2、任务 T56.3）：
#   1. intercept_before 在实例启动前校验租户资源配额；
#   2. 调用 QuotaChecker.check_quota；
#   3. 配额超限返回 TENANT_QUOTA_EXCEEDED 并记录审计日志；
#   4. 未启用治理时自动放行（降级模式）。
# =============================================================================

from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
    GovernanceResult,
)
from orchestration_platform.governance.tenant.quota_checker import QuotaChecker


class QuotaInterceptor:
    """配额拦截器实现。

    校验租户资源使用是否超出配额上限。
    实现 IQuotaInterceptor 接口。
    """

    def __init__(
        self,
        quota_checker: QuotaChecker,
        enabled: Optional[bool] = None,
    ) -> None:
        """初始化配额拦截器。

        Args:
            quota_checker: 配额校验器实例。
            enabled: 是否启用；缺省从配置读取 tenant_isolation_enabled。
        """
        self._checker = quota_checker
        self._enabled = (
            enabled if enabled is not None else get_settings().tenant_isolation_enabled
        )

    async def check_quota(
        self, request: GovernanceRequest
    ) -> GovernanceDecision:
        """校验租户资源使用是否超出配额上限。"""
        return await self._checker.check_quota(request)

    async def intercept_before(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
    ) -> GovernanceDecision:
        """前置拦截：实例启动前校验租户资源配额。

        未启用治理时自动放行（降级模式）。
        配额超限返回拒绝决策并记录审计摘要至 metadata。
        """
        if not self._enabled:
            return decision

        try:
            quota_decision = await self.check_quota(request)
            if not quota_decision.allowed:
                # 配额超限：记录审计摘要
                quota_decision.metadata["quota_exceeded"] = True
                quota_decision.metadata["tenant_id"] = request.tenant_id
                quota_decision.metadata["resource_type"] = request.resource_type.value
                quota_decision.metadata["operation"] = request.operation.value
            return quota_decision
        except Exception as exc:
            return GovernanceDecision(
                allowed=False,
                deny_reason=f"配额拦截器异常: {type(exc).__name__}: {exc}",
            )

    async def intercept_after(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
        result: GovernanceResult,
    ) -> GovernanceResult:
        """后置拦截：配额无需后置处理。"""
        return result
