# 销项（2026-11-21，roadmap B0/D3）：原 B1 族候选孤儿，已由治理拦截器链接线为生产调用点（governance_factory create_governance_chain 六槽装配），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# 预算拦截器（governance/interceptors/budget_interceptor.py）
#
# 职责：实例启动前校验预算与熔断状态。
# 设计意图（对应 spec 5.2、design 2.2.2、任务 T61.2）：
#   1. intercept_before 校验预算与熔断状态；
#   2. 调用 BudgetGuard.check_budget；
#   3. 已熔断时返回 TENANT_BUDGET_EXCEEDED 并记录审计日志；
#   4. 熔断不可绕过：在引擎启动前强制校验。
# =============================================================================

from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.billing.budget_guard import BudgetGuard
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
    GovernanceResult,
    IBudgetInterceptor,
)


class BudgetInterceptor:
    """预算拦截器实现。

    实例启动前校验预算与熔断状态。
    实现 IBudgetInterceptor 接口。
    """

    def __init__(
        self,
        budget_guard: BudgetGuard,
        enabled: Optional[bool] = None,
    ) -> None:
        """初始化预算拦截器。

        Args:
            budget_guard: 预算熔断守卫。
            enabled: 是否启用；缺省从配置读取。
        """
        self._guard = budget_guard
        self._enabled = (
            enabled if enabled is not None else get_settings().budget_fail_closed
        )

    async def check_budget(
        self, request: GovernanceRequest
    ) -> GovernanceDecision:
        """校验预算是否超限，返回是否熔断。"""
        return await self._guard.check_budget(request)

    async def intercept_before(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
    ) -> GovernanceDecision:
        """前置拦截：实例启动前校验预算与熔断状态。

        未启用治理时自动放行（降级模式）。
        已熔断时返回拒绝决策并记录审计摘要。
        """
        if not self._enabled:
            return decision

        try:
            budget_decision = await self.check_budget(request)
            if not budget_decision.allowed:
                # 已熔断：记录审计摘要
                budget_decision.metadata["budget_exceeded"] = True
                budget_decision.metadata["tenant_id"] = request.tenant_id
                budget_decision.metadata["instance_id"] = request.instance_id
            return budget_decision
        except Exception as exc:
            if self._guard._fail_closed:
                return GovernanceDecision(
                    allowed=False,
                    deny_reason=f"预算拦截器异常（fail-closed）: {type(exc).__name__}: {exc}",
                )
            return decision

    async def intercept_after(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
        result: GovernanceResult,
    ) -> GovernanceResult:
        """后置拦截：预算无需后置处理。"""
        return result
