# =============================================================================
# 全局调度器（governance/scheduler/global_scheduler.py）
#
# 职责：全局实例调度，资源充足时放行，不足时入队列。
# 设计意图（对应 spec 5.4、design 2.7、任务 T69.1）：
#   1. submit：提交实例调度请求，资源充足时放行，不足时入全局队列返回 202；
#   2. on_resource_released：资源释放时取出队首实例分配资源启动；
#   3. try_preempt：高优先级实例无法获得资源时触发抢占决策；
#   4. 调度兼容：未启用全局调度器时提交即调度（降级模式）。
# =============================================================================

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import (
    InstancePriority,
    ResourcePoolType,
    SchedulerPolicyType,
)
from orchestration_platform.governance.resource.models import ResourceRequirement
from orchestration_platform.governance.resource.resource_pool import (
    ResourcePoolManager,
)
from orchestration_platform.governance.scheduler.priority_queue import (
    PriorityQueue,
)
from orchestration_platform.governance.scheduler.schedulers import (
    StrategyRegistry,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleResult:
    """调度结果。

    表示实例调度的决策结果。
    """

    def __init__(
        self,
        instance_id: str,
        scheduled: bool,
        queued: bool = False,
        reason: Optional[str] = None,
    ) -> None:
        self.instance_id = instance_id
        self.scheduled = scheduled
        self.queued = queued
        self.reason = reason


class GlobalScheduler:
    """全局调度器。

    管理跨工作流的实例调度，资源充足时放行，不足时入全局队列。
    """

    def __init__(
        self,
        pool_manager: Optional[ResourcePoolManager] = None,
        policy_type: SchedulerPolicyType = SchedulerPolicyType.FAIR_SHARE,
        enabled: Optional[bool] = None,
    ) -> None:
        """初始化全局调度器。

        Args:
            pool_manager: 资源池管理器；缺省创建并初始化默认池。
            policy_type: 调度策略类型。
            enabled: 是否启用；缺省从配置读取。
        """
        self._settings = get_settings()
        self._enabled = (
            enabled if enabled is not None else self._settings.global_scheduler_enabled
        )
        self._pool_manager = pool_manager or ResourcePoolManager()
        self._pool_manager.init_default_pools()
        self._queue = PriorityQueue(
            max_size=self._settings.scheduler_queue_max_size
        )
        self._strategy_registry = StrategyRegistry()
        self._policy_type = policy_type
        self._running_instances: Dict[str, Dict[str, Any]] = {}

    async def submit(
        self,
        instance_id: str,
        tenant_id: str,
        priority: InstancePriority = InstancePriority.NORMAL,
        resource_requirements: Optional[List[ResourceRequirement]] = None,
    ) -> ScheduleResult:
        """提交实例调度请求。

        资源充足时放行启动，资源不足时入全局队列返回 202。
        未启用全局调度器时提交即调度（降级模式）。
        """
        if not self._enabled:
            return ScheduleResult(
                instance_id=instance_id,
                scheduled=True,
                reason="降级模式：全局调度器未启用",
            )

        requirements = resource_requirements or [
            ResourceRequirement(
                requirement_id=f"req_{uuid.uuid4().hex[:12]}",
                instance_id=instance_id,
                pool_type=ResourcePoolType.CONCURRENCY_SLOT,
                requested_amount=self._settings.resource_estimate_default_slots,
            )
        ]

        # 尝试分配资源
        all_allocated = True
        for req in requirements:
            pool = self._find_pool_for_requirement(req)
            if pool is None:
                all_allocated = False
                break
            success = await self._pool_manager.allocate(pool.pool_id, req)
            if not success:
                all_allocated = False
                break

        if all_allocated:
            self._running_instances[instance_id] = {
                "tenant_id": tenant_id,
                "priority": priority,
                "requirements": requirements,
                "started_at": _utc_now(),
            }
            return ScheduleResult(
                instance_id=instance_id,
                scheduled=True,
                reason="资源充足，已放行启动",
            )

        # 资源不足：入全局队列
        enqueued = self._queue.enqueue(
            instance_id=instance_id,
            tenant_id=tenant_id,
            priority=priority,
            weight=self._settings.scheduler_fair_weight_default,
        )
        if enqueued:
            return ScheduleResult(
                instance_id=instance_id,
                scheduled=False,
                queued=True,
                reason="资源不足，已入全局队列",
            )
        return ScheduleResult(
            instance_id=instance_id,
            scheduled=False,
            reason="队列已满，调度失败",
        )

    async def on_resource_released(
        self, instance_id: str
    ) -> Optional[ScheduleResult]:
        """资源释放时取出队首实例分配资源启动。

        Returns:
            调度结果（有排队实例时），None（队列空时）。
        """
        # 释放运行实例资源
        running = self._running_instances.pop(instance_id, None)
        if running:
            for req in running.get("requirements", []):
                if req.allocated_pool_id:
                    await self._pool_manager.release(
                        req.allocated_pool_id, instance_id
                    )

        # 从队列取出下一个实例
        if self._queue.is_empty:
            return None

        strategy = self._strategy_registry.get_strategy(self._policy_type)
        entry = await strategy.select_next(self._queue)
        if entry is None:
            return None

        # 尝试调度队首实例
        return await self.submit(
            instance_id=entry.instance_id,
            tenant_id=entry.tenant_id,
            priority=entry.priority,
        )

    async def try_preempt(
        self,
        high_priority_instance_id: str,
        high_priority: InstancePriority,
    ) -> Optional[str]:
        """高优先级实例无法获得资源时触发抢占决策。

        Returns:
            被抢占的实例标识（如有），None（无抢占候选）。
        """
        if not self._settings.scheduler_preemption_enabled:
            return None

        # 查找最低优先级的运行实例
        priority_order = {
            InstancePriority.URGENT: 0,
            InstancePriority.HIGH: 1,
            InstancePriority.NORMAL: 2,
            InstancePriority.LOW: 3,
        }
        high_priority_value = priority_order.get(high_priority, 2)

        candidates = []
        for inst_id, info in self._running_instances.items():
            inst_priority = info.get("priority", InstancePriority.NORMAL)
            inst_priority_value = priority_order.get(inst_priority, 2)
            if inst_priority_value > high_priority_value:
                preempt_count = info.get("preempt_count", 0)
                if preempt_count < self._settings.scheduler_preemption_max_count:
                    candidates.append((inst_id, inst_priority_value))

        if not candidates:
            return None

        # 选择优先级最低的实例作为抢占候选
        candidates.sort(key=lambda x: -x[1])
        return candidates[0][0]

    def _find_pool_for_requirement(
        self, req: ResourceRequirement
    ) -> Optional[Any]:
        """为资源需求查找合适的资源池。"""
        pools = self._pool_manager.list_pools(req.pool_type)
        for pool in pools:
            if pool.available >= req.requested_amount:
                return pool
        return None

    @property
    def queue_size(self) -> int:
        """当前队列大小。"""
        return self._queue.size

    @property
    def running_count(self) -> int:
        """当前运行实例数。"""
        return len(self._running_instances)

    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态。"""
        return {
            "enabled": self._enabled,
            "queue_size": self.queue_size,
            "running_count": self.running_count,
            "policy_type": self._policy_type.value,
        }
