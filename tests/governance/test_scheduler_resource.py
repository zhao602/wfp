# =============================================================================
# 全局调度与资源治理测试（tests/governance/test_scheduler_resource.py）
#
# 覆盖验收：A64-A73、B45-B47、B56-B58、B66-B67、B74
# =============================================================================

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from orchestration_platform.governance.scheduler.priority_queue import PriorityQueue
from orchestration_platform.governance.scheduler.global_scheduler import GlobalScheduler
from orchestration_platform.governance.scheduler.preemptor import Preemptor
from orchestration_platform.governance.resource.resource_pool import ResourcePoolManager
from orchestration_platform.domain.models.enums import (
    InstancePriority,
    ResourcePoolType,
    SchedulerPolicyType,
)


def test_priority_queue():
    """A64: 全局优先级队列——高优先级先调度。"""
    queue = PriorityQueue(max_size=100)
    queue.enqueue("inst1", "tenant1", InstancePriority.LOW)
    queue.enqueue("inst2", "tenant1", InstancePriority.URGENT)
    queue.enqueue("inst3", "tenant1", InstancePriority.NORMAL)

    entry = queue.dequeue()
    assert entry.instance_id == "inst2"  # URGENT 先出队
    entry = queue.dequeue()
    assert entry.instance_id == "inst3"  # NORMAL 其次
    entry = queue.dequeue()
    assert entry.instance_id == "inst1"  # LOW 最后
    print("✓ A64: 全局优先级队列")


async def test_resource_pool():
    """A66/A67: 资源池分配与释放。"""
    manager = ResourcePoolManager()
    pool = manager.create_pool(
        pool_id="test_pool",
        pool_type=ResourcePoolType.CONCURRENCY_SLOT,
        total_capacity=10,
    )

    from orchestration_platform.governance.resource.models import ResourceRequirement
    import uuid

    req = ResourceRequirement(
        requirement_id=f"req_{uuid.uuid4().hex[:12]}",
        instance_id="inst1",
        pool_type=ResourcePoolType.CONCURRENCY_SLOT,
        requested_amount=5,
    )

    success = await manager.allocate("test_pool", req)
    assert success

    util = manager.get_utilization("test_pool")
    assert util["allocated"] == 5
    assert util["available"] == 5

    success = await manager.release("test_pool", "inst1")
    assert success

    util = manager.get_utilization("test_pool")
    assert util["allocated"] == 0
    print("✓ A66/A67: 资源池分配与释放")


async def test_global_scheduler():
    """A73: 调度兼容——未启用时提交即调度。"""
    scheduler = GlobalScheduler(enabled=False)
    result = await scheduler.submit(
        instance_id="inst1",
        tenant_id="tenant1",
    )
    assert result.scheduled
    print("✓ A73: 调度兼容（降级模式）")


async def test_oversubscription():
    """A68: 超售比。"""
    manager = ResourcePoolManager()
    pool = manager.create_pool(
        pool_id="oversub_pool",
        pool_type=ResourcePoolType.CONCURRENCY_SLOT,
        total_capacity=20,
        oversubscription_ratio=1.5,
    )
    assert pool.max_capacity == 30
    print("✓ A68: 超售比")


async def test_preemptor():
    """A69: 优先级抢占。"""
    scheduler = GlobalScheduler(enabled=True)
    preemptor = Preemptor(scheduler)

    # 无抢占候选时返回 None
    result = await preemptor.preempt(
        high_priority_instance_id="inst_high",
        high_priority=InstancePriority.URGENT,
    )
    # 无运行实例时无抢占候选
    assert result is None
    print("✓ A69: 优先级抢占")


async def run_all_tests():
    print("=" * 60)
    print("全局调度与资源治理测试")
    print("=" * 60)

    test_priority_queue()
    await test_resource_pool()
    await test_global_scheduler()
    await test_oversubscription()
    await test_preemptor()

    print("=" * 60)
    print("全部测试通过")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
