# =============================================================================
# 预算熔断守卫（governance/billing/budget_guard.py）
#
# 职责：实例启动前预算校验与熔断状态管理。
# 设计意图（对应 spec 5.2、design 2.5、任务 T60.2）：
#   1. 检查累计成本是否达到预算阈值；
#   2. 熔断策略：block_new（阻止新实例）、block_new_and_terminate（阻止并终止）；
#   3. 熔断不可绕过：通过 IBudgetInterceptor 在引擎启动前强制校验；
#   4. fail-closed 配置：BUDGET_FAIL_CLOSED=true 时预算服务异常拒绝实例启动。
# =============================================================================

from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.billing.budget_manager import BudgetManager
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
)


class BudgetGuard:
    """预算熔断守卫。

    在实例启动前校验预算与熔断状态。
    """

    def __init__(
        self,
        budget_manager: BudgetManager,
        fail_closed: Optional[bool] = None,
    ) -> None:
        """初始化预算守卫。

        Args:
            budget_manager: 预算管理器。
            fail_closed: 是否 fail-closed；缺省从配置读取。
        """
        self._manager = budget_manager
        self._fail_closed = (
            fail_closed if fail_closed is not None else get_settings().budget_fail_closed
        )

    async def check_budget(
        self,
        request: GovernanceRequest,
        budget_id: Optional[str] = None,
    ) -> GovernanceDecision:
        """校验预算与熔断状态。

        Args:
            request: 治理请求。
            budget_id: 预算标识；缺省按租户查找。

        Returns:
            GovernanceDecision：允许或拒绝（已熔断）。
        """
        try:
            if budget_id is not None:
                status = await self._manager.get_budget_status(budget_id)
                if status is not None:
                    return self._evaluate_status(status)
                return GovernanceDecision(allowed=True)

            # 按租户查找全部预算
            budgets = await self._manager.list_budgets(
                tenant_id=request.tenant_id
            )
            for budget in budgets:
                status = await self._manager.get_budget_status(budget.budget_id)
                if status is not None:
                    decision = self._evaluate_status(status)
                    if not decision.allowed:
                        return decision

            return GovernanceDecision(allowed=True)
        except Exception as exc:
            if self._fail_closed:
                return GovernanceDecision(
                    allowed=False,
                    deny_reason=f"预算校验异常（fail-closed）: {type(exc).__name__}: {exc}",
                )
            return GovernanceDecision(allowed=True)

    def _evaluate_status(
        self, status: dict
    ) -> GovernanceDecision:
        """评估预算状态，返回决策。"""
        if status.get("is_circuit_broken", False):
            return GovernanceDecision(
                allowed=False,
                deny_reason=f"预算已熔断: 累计成本={status['current_usage']}, 预算={status['budget_limit']}",
                metadata={
                    "budget_id": status["budget_id"],
                    "circuit_broken": True,
                    "usage_ratio": status["usage_ratio"],
                },
            )

        if status.get("alert_threshold_reached", False):
            return GovernanceDecision(
                allowed=True,
                metadata={
                    "budget_id": status["budget_id"],
                    "alert_threshold_reached": True,
                    "usage_ratio": status["usage_ratio"],
                },
            )

        return GovernanceDecision(allowed=True)
