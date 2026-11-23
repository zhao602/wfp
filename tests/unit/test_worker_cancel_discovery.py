# =============================================================================
# M3 worker 心跳取消发现测试
# （tests/unit/test_worker_cancel_discovery.py）
#
# 职责：验证 P5 实例级取消 worker 侧全链路（roadmap design D4/D5）：
#       认领时取消标志 → 不执行直接收口（事件幂等补落）；心跳发现队列
#       取消标志 → 抢占式取消 runner → 内部取消吞掉走正常收口
#       （flush → complete，实例落 cancelled 终态与事件）；外部取消
#       维持 re-raise（M2 语义零变化）。
# 设计意图：
#   1. 真实 LocalFileExecutionQueue（标志经磁盘传播，非桩注入）；
#   2. 阻塞桩执行器保证取消窗口（analyze_data 首调阻塞 3600s）；
#   3. 取消时延上界 = 心跳间隔（0.05s 测试值；生产 10s 口径不变）。
# =============================================================================

import asyncio
from pathlib import Path

import pytest

from orchestration_platform.application.execution_runner import InstanceRegistry
from orchestration_platform.domain.models.enums import EventType, NodeType
from orchestration_platform.domain.models.execution_event import ExecutionEvent
from orchestration_platform.domain.models.execution_job import ExecutionJob
from orchestration_platform.engine.node_executor_registry import (
    NodeExecutorRegistry,
)
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.engine.nodes.trigger_node_executor import (
    TriggerNodeExecutor,
)
from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
    LocalFileCheckpointRepository,
)
from orchestration_platform.persistence.local_file.local_file_event_repository import (
    LocalFileEventRepository,
)
from orchestration_platform.persistence.local_file.local_file_execution_queue import (
    LocalFileExecutionQueue,
)
from orchestration_platform.worker.execution_worker import ExecutionWorker

_FOUR_NODE_YAML = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "valid_workflow_definitions"
    / "valid_sequential_four_nodes.yaml"
).read_text(encoding="utf-8")


class _BlockingStubExecutor:
    """阻塞桩执行器：目标节点首调阻塞 3600s（取消窗口保证）。"""

    def __init__(self, node_type: NodeType, block_node_ids=None) -> None:
        self._node_type = node_type
        self._block = block_node_ids or set()
        self.node_calls: dict = {}
        self.block_started = asyncio.Event()

    @property
    def node_type(self) -> NodeType:
        return self._node_type

    async def execute(self, context) -> NodeExecutionResult:
        node_id = context.node_definition.node_id
        self.node_calls[node_id] = self.node_calls.get(node_id, 0) + 1
        if node_id in self._block:
            self.block_started.set()
            await asyncio.sleep(3600)
        return NodeExecutionResult(
            success=True,
            output_name=getattr(
                context.node_definition, "effective_output_name", None
            ),
            output_value={"stub": node_id},
        )


def _blocking_registry() -> NodeExecutorRegistry:
    """四节点注册表：agent 首调阻塞（analyze_data）。"""
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())
    registry.register(_BlockingStubExecutor(NodeType.DATA_SOURCE))
    registry.register(
        _BlockingStubExecutor(NodeType.AGENT, block_node_ids={"analyze_data"})
    )
    registry.register(_BlockingStubExecutor(NodeType.TOOL))
    return registry


def _mk_job(instance_id: str, job_id: str) -> ExecutionJob:
    return ExecutionJob(
        job_id=job_id,
        instance_id=instance_id,
        definition_id="sequential_four_nodes",
        initial_inputs={"topic": "取消"},
        trigger_source="manual",
        trace_id=f"trace-{job_id}",
        idempotency_key=instance_id,
        yaml_text=_FOUR_NODE_YAML,
    )


def _mk_worker(queue, event_repo, cp_repo, **kwargs) -> ExecutionWorker:
    return ExecutionWorker(
        queue,
        worker_id="w-m3",
        executor_registry=_blocking_registry(),
        checkpoint_repository=cp_repo,
        event_repository=event_repo,
        heartbeat_seconds=0.05,
        **kwargs,
    )


async def _event_types(repo, instance_id: str) -> list:
    events = await repo.list_for_instance(instance_id)
    return [e.event_type for e in events]


# =============================================================================
# A. 认领时取消标志：不执行直接收口（design D5）
# =============================================================================


class TestClaimTimeFlagFinalize:

    async def test_flagged_job_completes_without_execution(self, tmp_path):
        """认领前已置标志：不执行直接 complete + 事件补偿落账。"""
        queue = LocalFileExecutionQueue(queue_dir=str(tmp_path / "queue"))
        event_repo = LocalFileEventRepository(storage_root=str(tmp_path / "ev"))
        worker = _mk_worker(
            queue, event_repo, LocalFileCheckpointRepository(storage_root=str(tmp_path))
        )
        job = _mk_job("inst-m3c-claim", "job-m3c-claim")
        await queue.enqueue(job)
        await queue.request_cancel(job.job_id)
        claimed = await queue.claim("w-m3")
        assert claimed is not None and claimed.cancel_requested is True

        await worker.process_job(claimed)

        after = await queue.get(job.job_id)
        assert after is not None and after.status == "completed"
        types = await _event_types(event_repo, job.instance_id)
        assert EventType.INSTANCE_CANCELLED in types, "接管/认领场景事件补落"

        # 未执行：agent 桩零调用
        agent = worker._executor_registry.get_executor(NodeType.AGENT)
        assert agent.node_calls == {}

    async def test_finalize_event_idempotent_no_duplicate(self, tmp_path):
        """端点侧已落取消事件（盲区取消）→ 收口不双写。"""
        queue = LocalFileExecutionQueue(queue_dir=str(tmp_path / "queue"))
        event_repo = LocalFileEventRepository(storage_root=str(tmp_path / "ev"))
        worker = _mk_worker(
            queue, event_repo, LocalFileCheckpointRepository(storage_root=str(tmp_path))
        )
        job = _mk_job("inst-m3c-dup", "job-m3c-dup")
        await queue.enqueue(job)
        await queue.request_cancel(job.job_id)
        # 端点侧盲区取消事件已落账
        await event_repo.append(
            ExecutionEvent(
                event_id="evt-endpoint-cancel",
                instance_id=job.instance_id,
                event_type=EventType.INSTANCE_CANCELLED,
                node_id=None,
                sequence=0,
                payload={"definition_id": job.definition_id},
            )
        )
        claimed = await queue.claim("w-m3")

        await worker.process_job(claimed)

        events = await event_repo.list_for_instance(job.instance_id)
        cancel_events = [
            e for e in events if e.event_type == EventType.INSTANCE_CANCELLED
        ]
        assert len(cancel_events) == 1, "查重后不双写（design D5）"
        after = await queue.get(job.job_id)
        assert after is not None and after.status == "completed"


# =============================================================================
# B. 心跳发现：内部取消吞掉走正常收口（design D4）
# =============================================================================


class TestHeartbeatDiscovery:

    async def test_heartbeat_discovers_flag_and_cancels_runner(self, tmp_path):
        """心跳发现标志 → 抢占式取消 runner → 内部取消吞掉 → complete；
        实例落 cancelled 终态 + INSTANCE_STARTED/INSTANCE_CANCELLED 事件。"""
        queue = LocalFileExecutionQueue(queue_dir=str(tmp_path / "queue"))
        event_repo = LocalFileEventRepository(storage_root=str(tmp_path / "ev"))
        worker = _mk_worker(
            queue, event_repo, LocalFileCheckpointRepository(storage_root=str(tmp_path))
        )
        job = _mk_job("inst-m3c-hb", "job-m3c-hb")
        await queue.enqueue(job)
        claimed = await queue.claim("w-m3")
        assert claimed is not None
        agent = worker._executor_registry.get_executor(NodeType.AGENT)

        task = asyncio.create_task(worker.process_job(claimed))
        await asyncio.wait_for(agent.block_started.wait(), timeout=5)

        await queue.request_cancel(job.job_id)  # API 端点等价置位

        await asyncio.wait_for(task, timeout=10)  # 内部取消不抛出（吞掉）

        after = await queue.get(job.job_id)
        assert after is not None
        assert after.status == "completed", "内部取消正常收口（D4）"

        registry = worker._registry
        assert registry is not None
        rt = registry.get(job.instance_id)
        assert rt.status == "cancelled"
        types = await _event_types(event_repo, job.instance_id)
        assert EventType.INSTANCE_STARTED in types
        assert EventType.INSTANCE_CANCELLED in types, "取消事件读穿透可见"

    async def test_heartbeat_flag_respected_before_first_node(self, tmp_path):
        """标志在首节点前已置（心跳首拍即发现）：注册 cancelled 前置。"""
        queue = LocalFileExecutionQueue(queue_dir=str(tmp_path / "queue"))
        event_repo = LocalFileEventRepository(storage_root=str(tmp_path / "ev"))
        worker = _mk_worker(
            queue, event_repo, LocalFileCheckpointRepository(storage_root=str(tmp_path))
        )
        job = _mk_job("inst-m3c-fast", "job-m3c-fast")
        await queue.enqueue(job)
        claimed = await queue.claim("w-m3")
        await queue.request_cancel(job.job_id)  # 执行前置位

        await asyncio.wait_for(worker.process_job(claimed), timeout=10)

        registry = worker._registry
        assert registry is not None
        rt = registry.get(job.instance_id)
        assert rt.status == "cancelled"
        after = await queue.get(job.job_id)
        assert after is not None and after.status == "completed"


# =============================================================================
# C. 外部取消维持 re-raise（M2 语义零变化）
# =============================================================================


class TestExternalCancelReRaise:

    async def test_external_cancel_raises_and_leaves_job_running(self, tmp_path):
        """外部取消（非标志驱动）：re-raise；作业不流转（租约到期接管）。"""
        queue = LocalFileExecutionQueue(queue_dir=str(tmp_path / "queue"))
        event_repo = LocalFileEventRepository(storage_root=str(tmp_path / "ev"))
        worker = _mk_worker(
            queue, event_repo, LocalFileCheckpointRepository(storage_root=str(tmp_path))
        )
        job = _mk_job("inst-m3c-ext", "job-m3c-ext")
        await queue.enqueue(job)
        claimed = await queue.claim("w-m3")
        agent = worker._executor_registry.get_executor(NodeType.AGENT)

        task = asyncio.create_task(worker.process_job(claimed))
        await asyncio.wait_for(agent.block_started.wait(), timeout=5)

        task.cancel()  # 外部取消（worker 进程级取消等价）
        with pytest.raises(asyncio.CancelledError):
            await task

        after = await queue.get(job.job_id)
        assert after is not None
        assert after.status == "running", "外部取消不流转终态（接管续跑）"
