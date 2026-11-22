# =============================================================================
# 资源池管理（governance/resource/resource_pool.py）
#
# 职责：池化管理 CPU/内存/并发槽位资源。
# 设计意图（对应 spec 5.4、design 2.7、任务 T68.1）：
#   1. 三类资源池：CPU、内存、并发槽位；
#   2. 配额上限：分配总量不得超过配额上限 × 超售比；
#   3. 分配与释放：实例执行前分配，完成后释放；
#   4. 状态查询：各资源池占用率、可用槽位、排队实例数。
# =============================================================================

from datetime import datetime, timezone
from typing import Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import ResourcePoolType
from orchestration_platform.governance.resource.models import (
    ResourcePool,
    ResourceRequirement,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ResourcePoolManager:
    """资源池管理器。

    管理三类资源池的分配、释放与状态查询。
    """

    def __init__(self) -> None:
        """初始化资源池管理器。"""
        self._settings = get_settings()
        self._pools: Dict[str, ResourcePool] = {}
        self._allocations: Dict[str, Dict[str, ResourceRequirement]] = {}

    def create_pool(
        self,
        pool_id: str,
        pool_type: ResourcePoolType,
        total_capacity: int,
        oversubscription_ratio: Optional[float] = None,
        tenant_id: Optional[str] = None,
    ) -> ResourcePool:
        """创建资源池。

        后置条件：max_capacity = total_capacity × oversubscription_ratio。
        """
        ratio = oversubscription_ratio or self._settings.resource_pool_oversubscription_ratio
        max_capacity = int(total_capacity * ratio)

        pool = ResourcePool(
            pool_id=pool_id,
            tenant_id=tenant_id,
            pool_type=pool_type,
            total_capacity=total_capacity,
            allocated=0,
            available=max_capacity,
            oversubscription_ratio=ratio,
            max_capacity=max_capacity,
        )
        self._pools[pool_id] = pool
        self._allocations[pool_id] = {}
        return pool

    async def allocate(
        self,
        pool_id: str,
        requirement: ResourceRequirement,
    ) -> bool:
        """从资源池分配资源。

        Returns:
            True（分配成功），False（资源不足）。
        """
        pool = self._pools.get(pool_id)
        if pool is None:
            return False

        if pool.available < requirement.requested_amount:
            return False

        pool.allocated += requirement.requested_amount
        pool.available -= requirement.requested_amount
        pool.updated_at = _utc_now()

        requirement.is_fulfilled = True
        requirement.allocated_pool_id = pool_id
        self._allocations[pool_id][requirement.instance_id] = requirement
        return True

    async def release(
        self,
        pool_id: str,
        instance_id: str,
    ) -> bool:
        """释放实例占用的资源。

        Returns:
            True（释放成功），False（实例未分配资源）。
        """
        pool = self._pools.get(pool_id)
        if pool is None:
            return False

        allocations = self._allocations.get(pool_id, {})
        requirement = allocations.pop(instance_id, None)
        if requirement is None:
            return False

        pool.allocated -= requirement.requested_amount
        pool.available += requirement.requested_amount
        pool.updated_at = _utc_now()
        return True

    def get_pool(self, pool_id: str) -> Optional[ResourcePool]:
        """查询资源池状态。"""
        return self._pools.get(pool_id)

    def list_pools(
        self,
        pool_type: Optional[ResourcePoolType] = None,
    ) -> List[ResourcePool]:
        """列出全部资源池，可按类型过滤。"""
        pools = list(self._pools.values())
        if pool_type is not None:
            pools = [p for p in pools if p.pool_type == pool_type]
        return pools

    def get_utilization(self, pool_id: str) -> Optional[Dict[str, any]]:
        """查询资源池占用率。

        Returns:
            {pool_id, pool_type, total_capacity, allocated, available,
             utilization_ratio, max_capacity}。
        """
        pool = self._pools.get(pool_id)
        if pool is None:
            return None
        utilization = (
            pool.allocated / pool.max_capacity
            if pool.max_capacity > 0
            else 0
        )
        return {
            "pool_id": pool.pool_id,
            "pool_type": pool.pool_type.value,
            "total_capacity": pool.total_capacity,
            "allocated": pool.allocated,
            "available": pool.available,
            "utilization_ratio": round(utilization, 4),
            "max_capacity": pool.max_capacity,
        }

    def init_default_pools(self) -> None:
        """初始化默认资源池。"""
        slot_quota = self._settings.resource_pool_concurrency_slot_quota
        self.create_pool(
            pool_id="default_concurrency_slots",
            pool_type=ResourcePoolType.CONCURRENCY_SLOT,
            total_capacity=slot_quota,
        )
        self.create_pool(
            pool_id="default_cpu",
            pool_type=ResourcePoolType.CPU,
            total_capacity=100,
        )
        self.create_pool(
            pool_id="default_memory",
            pool_type=ResourcePoolType.MEMORY,
            total_capacity=102400,
        )
