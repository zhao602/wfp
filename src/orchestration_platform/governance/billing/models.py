# =============================================================================
# 计费领域模型（governance/billing/models.py）
#
# 职责：定义定价规则、成本记录与预算的领域模型。
# 设计意图（对应 spec 5.2、design 2.5）：
#   1. 定价规则按模型与 Token 类型定义单价；
#   2. 成本记录由用量记录乘以定价规则计算得出；
#   3. 预算按租户/工作流/实例维度设定阈值。
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import (
    BudgetPeriod,
    BudgetScope,
    CircuitBreakStrategy,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PricingRule(BaseModel):
    """定价规则：按模型与 Token 类型定义单价。

    单价精度为小数点后 6 位。
    """

    rule_id: str = Field(description="规则唯一标识")
    model_identifier: str = Field(description="模型标识")
    prompt_token_price: float = Field(
        default=0.0, ge=0, description="输入 Token 单价（元/Token，精度 6 位）"
    )
    completion_token_price: float = Field(
        default=0.0, ge=0, description="输出 Token 单价（元/Token，精度 6 位）"
    )
    effective_from: datetime = Field(default_factory=_utc_now, description="生效起始时间")
    effective_to: Optional[datetime] = Field(
        default=None, description="生效结束时间（None 表示长期有效）"
    )
    tenant_id: Optional[str] = Field(
        default=None, description="所属租户（None 表示全局规则）"
    )


class CostRecord(BaseModel):
    """成本记录：单次用量记录对应的费用。

    成本金额精度为小数点后 6 位。
    """

    cost_id: str = Field(description="成本记录唯一标识")
    tenant_id: str = Field(description="所属租户标识")
    usage_record_id: str = Field(description="关联的用量记录标识")
    instance_id: str = Field(description="执行实例标识")
    cost_amount: float = Field(
        description="成本金额（元，精度 6 位）"
    )
    pricing_rule_id: str = Field(description="适用的定价规则标识")
    calculated_at: datetime = Field(default_factory=_utc_now, description="计算时间")


class Budget(BaseModel):
    """预算：按租户/工作流/实例维度设定的费用阈值。

    超过告警阈值时发出告警，超过预算阈值时触发熔断。
    """

    budget_id: str = Field(description="预算唯一标识")
    tenant_id: str = Field(description="所属租户标识")
    scope: BudgetScope = Field(default=BudgetScope.TENANT, description="预算范围")
    target_id: str = Field(description="范围目标标识（租户ID/工作流ID/实例ID）")
    period: BudgetPeriod = Field(default=BudgetPeriod.MONTHLY, description="预算周期")
    budget_limit: float = Field(ge=0, description="预算阈值（元）")
    alert_threshold: float = Field(
        default=0.8, ge=0, le=1, description="告警阈值比例（0-1）"
    )
    circuit_break_strategy: CircuitBreakStrategy = Field(
        default=CircuitBreakStrategy.SOFT_ALERT, description="熔断策略"
    )
    current_usage: float = Field(default=0.0, ge=0, description="当前周期已用额度")
    is_circuit_broken: bool = Field(default=False, description="是否已熔断")
    period_start: datetime = Field(description="周期起始时间")
    period_end: datetime = Field(description="周期结束时间")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")
