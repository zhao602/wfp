# =============================================================================
# 调度策略实现（governance/scheduler/schedulers.py）
#
# 职责：实现公平调度与纯优先级调度两种策略。
# 设计意图（对应 spec 5.4、design 2.7、任务 T67.2-T67.4）：
#   1. FairScheduler：高优先级优先 + 租户/工作流公平分配，防饿死；
#   2. PriorityOnlyScheduler：纯按优先级排序；
#   3. StrategyRegistry：调度策略注册表，提供扩展点。
# =============================================================================

from typing import Dict, Optional, Protocol, runtime_checkable

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import (
    InstancePriority,
    SchedulerPolicyType,
)
from orchestration_platform.governance.scheduler.priority_queue import (
    PriorityQueue,
    QueueEntry,
)


@runtime_checkable
class ISchedulerStrategy(Protocol):
    """调度策略抽象接口。"""

    async def select_next(
        self, queue: PriorityQueue
    ) -> Optional[QueueEntry]:
        """从队列选择下一个调度的实例。"""
        ...


class FairScheduler:
    """公平调度策略。

    高优先级优先 + 租户/工作流公平分配，防饿死。
    通过加权轮转实现。
    """

    def __init__(self) -> None:
        """初始化公平调度器。"""
        self._settings = get_settings()
        self._tenant_weights: Dict[str, int] = {}
        self._tenant_served: Dict[str, int] = {}

    def set_tenant_weight(
        self, tenant_id: str, weight: int
    ) -> None:
        """设置租户公平调度权重。"""
        self._tenant_weights[tenant_id] = max(1, weight)
        self._tenant_served.setdefault(tenant_id, 0)

    async def select_next(
        self, queue: PriorityQueue
    ) -> Optional[QueueEntry]:
        """从队列选择下一个调度的实例。

        公平调度逻辑：
          1. 按优先级分组；
          2. 同优先级内按租户公平权重选择（已服务次数少/权重大的优先）；
          3. 异常时回退至纯优先级出队。
        """
        if queue.is_empty:
            return None

        try:
            entries = queue.list_entries()
            if not entries:
                return None

            # 按优先级分组
            priority_groups: Dict[InstancePriority, list] = {}
            for entry in entries:
                priority_groups.setdefault(entry.priority, []).append(entry)

            # 取最高优先级组
            highest_priority = min(
                priority_groups.keys(),
                key=lambda p: {
                    InstancePriority.URGENT: 0,
                    InstancePriority.HIGH: 1,
                    InstancePriority.NORMAL: 2,
                    InstancePriority.LOW: 3,
                }.get(p, 2),
            )
            candidates = priority_groups[highest_priority]

            # 同优先级内按公平权重选择
            selected = self._select_by_fairness(candidates)
            queue.cancel(selected.instance_id)
            self._tenant_served[selected.tenant_id] = (
                self._tenant_served.get(selected.tenant_id, 0) + 1
            )
            return selected
        except Exception:
            # 异常回退至纯优先级出队
            return queue.dequeue()

    def _select_by_fairness(
        self, candidates: list
    ) -> QueueEntry:
        """按公平权重从候选条目中选择。"""
        best_entry = None
        best_score = float("inf")

        for entry in candidates:
            weight = self._tenant_weights.get(
                entry.tenant_id,
                self._settings.scheduler_fair_weight_default,
            )
            served = self._tenant_served.get(entry.tenant_id, 0)
            # 公平分数：已服务次数 / 权重（越小越优先）
            score = served / weight
            if score < best_score:
                best_score = score
                best_entry = entry

        return best_entry or candidates[0]


class PriorityOnlyScheduler:
    """纯优先级调度策略。

    纯按优先级排序，高优先级始终先于低优先级。
    """

    async def select_next(
        self, queue: PriorityQueue
    ) -> Optional[QueueEntry]:
        """从队列选择下一个调度的实例（纯优先级出队）。"""
        return queue.dequeue()


class StrategyRegistry:
    """调度策略注册表。

    管理公平调度与纯优先级调度两种策略，提供扩展点。
    """

    def __init__(self) -> None:
        """初始化策略注册表。"""
        self._strategies: Dict[SchedulerPolicyType, ISchedulerStrategy] = {
            SchedulerPolicyType.STRICT_PRIORITY: PriorityOnlyScheduler(),
            SchedulerPolicyType.FAIR_SHARE: FairScheduler(),
            SchedulerPolicyType.WEIGHTED_QUEUE: FairScheduler(),
        }

    def register(
        self,
        policy_type: SchedulerPolicyType,
        strategy: ISchedulerStrategy,
    ) -> None:
        """注册调度策略。"""
        self._strategies[policy_type] = strategy

    def get_strategy(
        self, policy_type: SchedulerPolicyType
    ) -> ISchedulerStrategy:
        """获取调度策略实例。"""
        return self._strategies.get(
            policy_type, self._strategies[SchedulerPolicyType.STRICT_PRIORITY]
        )
