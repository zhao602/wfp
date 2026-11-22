# =============================================================================
# ABAC 领域模型（governance/rbac/abac/models.py）
#
# 职责：定义 ABAC 策略、条件、决策等领域模型。
# 设计意图（对应偏差修正 spec 6.2 / design 2.3.2 类图）：
#   - AbacPolicy：策略定义（条件集合 + 效果 + 失败模式）
#   - AbacCondition：单个属性匹配条件
#   - AbacDecision：ABAC 决策结果
# =============================================================================

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import (
    AttributeSource,
    ConditionOperator,
    FailMode,
    PolicyEffect,
)


def _utc_now() -> datetime:
    """UTC 当前时间工厂。"""
    return datetime.now(timezone.utc)


class AbacCondition(BaseModel):
    """ABAC 单个属性匹配条件。

    条件语义：attribute_name 通过 operator 与 expected_value 比较为真。
    多个条件以合取（AND）方式组合。
    """

    attribute_name: str = Field(description="属性名称")
    attribute_source: AttributeSource = Field(description="属性来源维度")
    operator: ConditionOperator = Field(description="比较操作符")
    expected_value: str = Field(description="期望值（字符串表示，求值时按操作符解析）")


class AbacPolicy(BaseModel):
    """ABAC 策略定义。

    一个策略包含多个条件的合取（AND），匹配后产生指定效果。
    策略按 priority 排序，首个匹配的策略决定效果。
    """

    policy_id: str = Field(description="策略唯一标识（租户内唯一）")
    tenant_id: str = Field(description="租户标识")
    conditions: List[AbacCondition] = Field(description="条件集合（合取语义）")
    effect: PolicyEffect = Field(description="策略效果：允许/拒绝")
    fail_mode: FailMode = Field(default=FailMode.DENY, description="属性求值失败时的回退行为")
    priority: int = Field(default=0, description="策略优先级（数值越小优先级越高）")
    is_active: bool = Field(default=True, description="是否启用")
    description: str = Field(default="", description="策略描述")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")


class AbacDecision(BaseModel):
    """ABAC 策略决策结果。"""

    effect: PolicyEffect = Field(description="决策效果")
    is_no_policy: bool = Field(default=False, description="是否为无策略（默认允许）")
    matched_policy_id: Optional[str] = Field(default=None, description="匹配的策略标识")
    eval_errors: List[str] = Field(default_factory=list, description="属性求值错误列表")
