# TODO(tech-debt): B档候选孤儿（族群 B4），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 全局调度领域模型（governance/scheduler/models.py）
#
# 职责：定义调度策略的领域模型。
# 设计意图（对应 spec 5.4、design 2.7）：
#   1. 全局优先级队列管理跨工作流的实例调度；
#   2. 公平调度防饿死；
#   3. 策略可配置切换。
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import (
    InstancePriority,
    SchedulerPolicyType,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SchedulerPolicy(BaseModel):
    """调度策略：全局调度器的行为配置。

    支持严格优先级、公平共享与加权队列三种策略。
    """

    policy_id: str = Field(description="策略唯一标识")
    tenant_id: Optional[str] = Field(
        default=None, description="所属租户（None 表示全局策略）"
    )
    policy_type: SchedulerPolicyType = Field(
        default=SchedulerPolicyType.STRICT_PRIORITY, description="调度策略类型"
    )
    default_priority: InstancePriority = Field(
        default=InstancePriority.NORMAL, description="默认优先级"
    )
    fair_weight: int = Field(default=1, ge=1, description="公平调度权重")
    max_queue_size: int = Field(default=10000, ge=1, description="队列最大长度")
    preemption_enabled: bool = Field(default=True, description="是否启用抢占")
    preemption_max_count: int = Field(default=3, ge=0, description="单实例最大被抢占次数")
    decision_timeout_ms: int = Field(default=100, ge=1, description="调度决策超时（毫秒）")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")
