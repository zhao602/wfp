# =============================================================================
# 预算管理器（governance/billing/budget_manager.py）
#
# 职责：管理预算配置、累计成本更新与周期重置。
# 设计意图（对应 spec 5.2、design 2.5、任务 T60.1）：
#   1. 预算配置（租户/工作流级，含周期、阈值、熔断策略）；
#   2. 累计成本更新与周期重置；
#   3. 预算查询返回累计成本、剩余额度、使用率、是否已熔断。
# =============================================================================

from datetime import datetime, timezone
from typing import Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import (
    BudgetPeriod,
    BudgetScope,
    CircuitBreakStrategy,
)
from orchestration_platform.governance.billing.models import Budget


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BudgetManager:
    """预算管理器。

    管理预算配置、累计成本更新与周期重置。
    """

    def __init__(self) -> None:
        """初始化预算管理器（内存存储，生产环境使用仓储）。"""
        self._budgets: Dict[str, Budget] = {}

    async def create_budget(self, budget: Budget) -> Budget:
        """创建预算配置。

        前置条件：budget_id 不重复。
        后置条件：预算持久化，可被 get_budget 查询到。
        """
        self._budgets[budget.budget_id] = budget
        return budget

    async def get_budget(self, budget_id: str) -> Optional[Budget]:
        """按标识查询预算。"""
        return self._budgets.get(budget_id)

    async def list_budgets(
        self,
        tenant_id: Optional[str] = None,
        scope: Optional[BudgetScope] = None,
    ) -> List[Budget]:
        """列出预算配置，可按租户与范围过滤。"""
        result = list(self._budgets.values())
        if tenant_id is not None:
            result = [b for b in result if b.tenant_id == tenant_id]
        if scope is not None:
            result = [b for b in result if b.scope == scope]
        return result

    async def update_budget(self, budget: Budget) -> Budget:
        """更新预算配置。"""
        self._budgets[budget.budget_id] = budget
        return budget

    async def delete_budget(self, budget_id: str) -> None:
        """删除预算配置。"""
        self._budgets.pop(budget_id, None)

    async def add_cost(
        self, budget_id: str, cost_amount: float
    ) -> Budget:
        """累加成本至预算。

        后置条件：current_usage 增加，可能触发熔断。
        """
        budget = self._budgets.get(budget_id)
        if budget is None:
            raise ValueError(f"预算 {budget_id} 不存在")

        # 检查周期是否需要重置
        now = _utc_now()
        if now >= budget.period_end:
            self._reset_period(budget, now)

        budget.current_usage = round(budget.current_usage + cost_amount, 6)
        budget.updated_at = now

        # 检查是否触发熔断
        if budget.current_usage >= budget.budget_limit:
            if budget.circuit_break_strategy != CircuitBreakStrategy.SOFT_ALERT:
                budget.is_circuit_broken = True

        return budget

    async def get_budget_status(
        self, budget_id: str
    ) -> Optional[Dict[str, any]]:
        """查询预算状态。

        Returns:
            {budget_id, current_usage, budget_limit, remaining, usage_ratio,
             is_circuit_broken, alert_threshold_reached}。
        """
        budget = self._budgets.get(budget_id)
        if budget is None:
            return None

        remaining = max(0, budget.budget_limit - budget.current_usage)
        usage_ratio = (
            budget.current_usage / budget.budget_limit
            if budget.budget_limit > 0
            else 0
        )

        return {
            "budget_id": budget.budget_id,
            "current_usage": budget.current_usage,
            "budget_limit": budget.budget_limit,
            "remaining": remaining,
            "usage_ratio": round(usage_ratio, 4),
            "is_circuit_broken": budget.is_circuit_broken,
            "alert_threshold_reached": usage_ratio >= budget.alert_threshold,
        }

    async def reset_period(self, budget_id: str) -> Budget:
        """手动重置预算周期。"""
        budget = self._budgets.get(budget_id)
        if budget is None:
            raise ValueError(f"预算 {budget_id} 不存在")
        self._reset_period(budget, _utc_now())
        return budget

    async def clear_circuit_break(self, budget_id: str) -> Budget:
        """清除熔断状态。"""
        budget = self._budgets.get(budget_id)
        if budget is None:
            raise ValueError(f"预算 {budget_id} 不存在")
        budget.is_circuit_broken = False
        budget.updated_at = _utc_now()
        return budget

    def _reset_period(self, budget: Budget, now: datetime) -> None:
        """重置预算周期。"""
        budget.current_usage = 0.0
        budget.is_circuit_broken = False
        budget.period_start = now

        # 计算下一个周期结束时间
        if budget.period == BudgetPeriod.DAILY:
            from datetime import timedelta
            budget.period_end = now + timedelta(days=1)
        elif budget.period == BudgetPeriod.WEEKLY:
            from datetime import timedelta
            budget.period_end = now + timedelta(weeks=1)
        elif budget.period == BudgetPeriod.MONTHLY:
            from datetime import timedelta
            budget.period_end = now + timedelta(days=30)
        elif budget.period == BudgetPeriod.YEARLY:
            from datetime import timedelta
            budget.period_end = now + timedelta(days=365)
        else:
            from datetime import timedelta
            budget.period_end = now + timedelta(days=30)

        budget.updated_at = now
