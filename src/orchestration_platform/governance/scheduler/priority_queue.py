# =============================================================================
# 全局优先级队列（governance/scheduler/priority_queue.py）
#
# 职责：全局优先级队列管理跨工作流的实例调度。
# 设计意图（对应 spec 5.4、design 2.7、任务 T67.1）：
#   1. 所有实例启动请求入队，按优先级与公平策略排序；
#   2. 使用堆结构实现，调度决策开销 ≤ 100 毫秒；
#   3. 队列最大容量由 SCHEDULER_QUEUE_MAX_SIZE 控制；
#   4. 支持取消排队中的实例。
# =============================================================================

import heapq
import itertools
from datetime import datetime, timezone
from typing import Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import InstancePriority


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# 优先级数值映射（数值越小优先级越高）
_PRIORITY_ORDER = {
    InstancePriority.URGENT: 0,
    InstancePriority.HIGH: 1,
    InstancePriority.NORMAL: 2,
    InstancePriority.LOW: 3,
}


class QueueEntry:
    """优先级队列条目。"""

    __slots__ = ("instance_id", "tenant_id", "priority", "enqueued_at", "weight", "_seq")

    def __init__(
        self,
        instance_id: str,
        tenant_id: str,
        priority: InstancePriority,
        weight: int = 1,
        seq: int = 0,
    ) -> None:
        self.instance_id = instance_id
        self.tenant_id = tenant_id
        self.priority = priority
        self.enqueued_at = _utc_now()
        self.weight = weight
        self._seq = seq

    @property
    def sort_key(self) -> tuple:
        """排序键：(优先级, 公平权重, 入队序号)。"""
        return (_PRIORITY_ORDER.get(self.priority, 2), self.weight, self._seq)


class PriorityQueue:
    """全局优先级队列。

    使用堆结构实现，按优先级与公平权重排序。
    """

    def __init__(
        self,
        max_size: Optional[int] = None,
    ) -> None:
        """初始化优先级队列。

        Args:
            max_size: 队列最大容量；缺省从配置读取。
        """
        self._max_size = max_size or get_settings().scheduler_queue_max_size
        self._heap: List[tuple] = []
        self._entries: Dict[str, QueueEntry] = {}
        self._counter = itertools.count()
        self._total_enqueued = 0
        self._total_dequeued = 0
        self._total_cancelled = 0

    def enqueue(
        self,
        instance_id: str,
        tenant_id: str,
        priority: InstancePriority = InstancePriority.NORMAL,
        weight: int = 1,
    ) -> bool:
        """入队实例调度请求。

        Returns:
            True（入队成功），False（队列已满）。
        """
        if len(self._entries) >= self._max_size:
            return False

        seq = next(self._counter)
        entry = QueueEntry(
            instance_id=instance_id,
            tenant_id=tenant_id,
            priority=priority,
            weight=weight,
            seq=seq,
        )
        self._entries[instance_id] = entry
        heapq.heappush(self._heap, (entry.sort_key, instance_id))
        self._total_enqueued += 1
        return True

    def dequeue(self) -> Optional[QueueEntry]:
        """出队最高优先级实例。

        Returns:
            队列条目（非空时），None（队列空时）。
        """
        while self._heap:
            _, instance_id = heapq.heappop(self._heap)
            entry = self._entries.pop(instance_id, None)
            if entry is not None:
                self._total_dequeued += 1
                return entry
        return None

    def cancel(self, instance_id: str) -> bool:
        """取消排队中的实例。

        Returns:
            True（取消成功），False（实例不在队列中）。
        """
        if instance_id in self._entries:
            del self._entries[instance_id]
            self._total_cancelled += 1
            return True
        return False

    def peek(self) -> Optional[QueueEntry]:
        """查看队首实例（不出队）。"""
        while self._heap:
            _, instance_id = self._heap[0]
            entry = self._entries.get(instance_id)
            if entry is not None:
                return entry
            heapq.heappop(self._heap)
        return None

    @property
    def size(self) -> int:
        """当前队列大小。"""
        return len(self._entries)

    @property
    def is_empty(self) -> bool:
        """队列是否为空。"""
        return len(self._entries) == 0

    def list_entries(self) -> List[QueueEntry]:
        """列出全部排队条目（按优先级排序）。"""
        return sorted(
            self._entries.values(),
            key=lambda e: e.sort_key,
        )
