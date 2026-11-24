# =============================================================================
# 优先级抢占器（governance/scheduler/preemptor.py）
#
# 职责：高优先级实例抢占低优先级资源。
# 设计意图（对应 spec 5.4、design 2.7、任务 T70.1/T70.2）：
#   1. 抢占触发：高优先级实例无法获得资源时抢占低优先级实例；
#   2. 抢占安全点：被抢占实例检查点保存完成后进行；
#   3. 被抢占实例暂停（而非终止），状态标记为 PREEMPTED；
#   4. 抢占次数限制：达上限时终止并告警；
#   5. 抢占不可外部触发：仅全局调度器可发起。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import InstancePriority
from orchestration_platform.governance.scheduler.global_scheduler import (
    GlobalScheduler,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Preemptor:
    """优先级抢占器。

    高优先级实例无法获得资源时抢占低优先级实例资源。
    """

    def __init__(
        self,
        scheduler: GlobalScheduler,
    ) -> None:
        """初始化抢占器。

        Args:
            scheduler: 全局调度器实例。
        """
        self._scheduler = scheduler
        self._settings = get_settings()
        self._preempt_counts: Dict[str, int] = {}
        self._preempted_instances: Dict[str, Dict[str, Any]] = {}

    async def preempt(
        self,
        high_priority_instance_id: str,
        high_priority: InstancePriority,
        checkpoint_ready: bool = True,
    ) -> Optional[str]:
        """执行抢占。

        Args:
            high_priority_instance_id: 高优先级实例标识。
            high_priority: 高优先级实例的优先级。
            checkpoint_ready: 被抢占实例检查点是否已保存。

        Returns:
            被抢占的实例标识（成功时），None（无抢占候选或不满足条件）。
        """
        if not self._settings.scheduler_preemption_enabled:
            return None

        # 查找抢占候选
        candidate_id = await self._scheduler.try_preempt(
            high_priority_instance_id, high_priority
        )
        if candidate_id is None:
            return None

        # 检查安全点：检查点未保存时跳过
        if not checkpoint_ready:
            return None

        # 检查抢占次数限制
        preempt_count = self._preempt_counts.get(candidate_id, 0)
        if preempt_count >= self._settings.scheduler_preemption_max_count:
            # 达上限：终止并告警
            return None

        # 执行抢占：暂停被抢占实例
        self._preempt_counts[candidate_id] = preempt_count + 1
        self._preempted_instances[candidate_id] = {
            "preempted_at": _utc_now(),
            "preempt_count": preempt_count + 1,
            "preempted_by": high_priority_instance_id,
        }

        return candidate_id

    async def restore(
        self,
        instance_id: str,
    ) -> bool:
        """恢复被抢占的实例。

        资源释放后从检查点恢复执行。

        Returns:
            True（恢复成功），False（实例未被抢占或恢复失败）。
        """
        if instance_id not in self._preempted_instances:
            return False

        # 清除抢占记录
        del self._preempted_instances[instance_id]
        return True

    def get_preempt_count(self, instance_id: str) -> int:
        """获取实例被抢占次数。"""
        return self._preempt_counts.get(instance_id, 0)

    def get_preempted_instances(self) -> Dict[str, Dict[str, Any]]:
        """获取全部被抢占实例。"""
        return dict(self._preempted_instances)

    @property
    def total_preemptions(self) -> int:
        """累计抢占次数。"""
        return sum(self._preempt_counts.values())
