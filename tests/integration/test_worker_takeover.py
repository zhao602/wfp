# =============================================================================
# Worker kill -9 模拟接管集成测试（tests/integration/test_worker_takeover.py）
#
# 覆盖（roadmap-v3 中期窗口 M2 核心门禁，plan/F1 出口判据）：
#   kill -9 worker 后实例被接管续跑；已完成节点不重复执行（at-least-once
#   诚实口径：重放范围仅被杀时刻执行中单节点）。
#
# 场景驱动（四节点顺序流水线：trigger → read_dataset → analyze_data →
# build_report；Agent 节点首次执行挂起模拟被杀时刻执行中节点）：
#   1. 入队 → worker-1 认领（租约 TTL=1s）→ 执行至 analyze_data 挂起
#      （trigger/read_dataset 已完成并逐节点落检查点）；
#   2. 模拟死亡：任务弃置（kill -9 等价——无 complete/fail/心跳清理，
#      仅留下 running 作业 + 检查点 + 事件仓储轨迹）；
#   3. 租约有效期内接管探测被拒（租约语义）；
#   4. 短 TTL 真实推进租约过期（1.2s）→ worker-2 take_over_expired 接管
#      → 从最新检查点续跑（analyze_data 单节点重放）→ build_report 完成；
#   5. 断言：作业 completed、终态 succeeded、已完成节点执行次数均为 1
#      （不重复执行）、被杀时刻执行中节点恰重放一次、事件仓储轨迹
#      支撑 API 读穿透快照（succeeded）。
# 执行命令：PYTHONPATH=src python -m pytest tests/integration/test_worker_takeover.py -q
# =============================================================================

import asyncio
import contextlib
from pathlib import Path

import pytest

from orchestration_platform.api.instance_read_through import (
    read_instance_snapshot,
)
from orchestration_platform.domain.models.enums import EventType, NodeType
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
from orchestration_platform.worker.execution_worker import (
    ExecutionWorker,
    PersistingInstanceRegistry,
    run_claimed_job,
)

_FOUR_NODE_YAML = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "valid_workflow_definitions"
    / "valid_sequential_four_nodes.yaml"
).read_text(encoding="utf-8")

_INSTANCE_ID = "inst-takeover-1"
_JOB_ID = "job-takeover-1"


class _CountingBlockStubExecutor:
    """计数桩：按 node_id 计数；指定节点首次执行挂起（被杀时刻执行中节点）。"""

    def __init__(self, node_type: NodeType, block_first_node_ids=None):
        self._node_type = node_type
        self._block_first = set(block_first_node_ids or [])
        self.node_calls: dict = {}
        self.block_started = asyncio.Event()

    @property
    def node_type(self) -> NodeType:
        return self._node_type

    async def execute(self, context) -> NodeExecutionResult:
        node_id = context.node_definition.node_id
        self.node_calls[node_id] = self.node_calls.get(node_id, 0) + 1
        if node_id in self._block_first and self.node_calls[node_id] == 1:
            self.block_started.set()
            await asyncio.sleep(3600)  # 模拟执行中被 kill -9（永不返回）
        return NodeExecutionResult(
            success=True,
            output_name=getattr(
                context.node_definition, "effective_output_name", None
            ),
            output_value={"stub": node_id},
        )


def _shared_executor_registry():
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())
    registry.register(_CountingBlockStubExecutor(NodeType.DATA_SOURCE))
    registry.register(
        _CountingBlockStubExecutor(
            NodeType.AGENT, block_first_node_ids={"analyze_data"}
        )
    )
    registry.register(_CountingBlockStubExecutor(NodeType.TOOL))
    return registry


def _executor_of(registry: NodeExecutorRegistry, node_type: NodeType):
    for executor in registry._executors.values():
        if executor.node_type == node_type:
            return executor
    raise LookupError(f"executor not found: {node_type}")


class TestWorkerTakeover:
    """kill -9 模拟接管续跑（M2 核心门禁）。"""

    async def test_kill9_takeover_resumes_from_checkpoint_without_duplicate(
        self, tmp_path
    ):
        # ---- 共享存储：队列 / 检查点 / 事件仓储（两 worker 跨进程等价物） ----
        queue = LocalFileExecutionQueue(
            queue_dir=str(tmp_path / "queue")
        )
        cp_repo = LocalFileCheckpointRepository(
            storage_root=str(tmp_path / "checkpoints")
        )
        event_repo = LocalFileEventRepository(
            storage_root=str(tmp_path / "events")
        )
        executor_registry = _shared_executor_registry()
        data_stub = _executor_of(executor_registry, NodeType.DATA_SOURCE)
        agent_stub = _executor_of(executor_registry, NodeType.AGENT)
        tool_stub = _executor_of(executor_registry, NodeType.TOOL)

        # ---- 1. 入队 + worker-1 认领（短租约 TTL=1s）----
        await queue.enqueue(
            ExecutionJob(
                job_id=_JOB_ID,
                instance_id=_INSTANCE_ID,
                definition_id="sequential_four_nodes",
                yaml_text=_FOUR_NODE_YAML,
                initial_inputs={"topic": "接管演练"},
                trigger_source="manual",
                trace_id="trace-takeover",
                idempotency_key=_INSTANCE_ID,
                status="pending",
                created_at="2026-11-23T00:00:00+00:00",
            )
        )
        claimed = await queue.claim(
            "worker-1-doomed", lease_ttl_seconds=1
        )
        assert claimed is not None and claimed.job_id == _JOB_ID

        # ---- 2. worker-1 执行至 analyze_data 挂起（已完成节点落检查点）----
        registry1 = PersistingInstanceRegistry(event_repo)
        run_task = asyncio.create_task(
            run_claimed_job(
                claimed,
                registry=registry1,
                executor_registry=executor_registry,
                checkpoint_repository=cp_repo,
            )
        )
        await asyncio.wait_for(agent_stub.block_started.wait(), timeout=10)
        # 模拟 kill -9：任务弃置（无 complete/fail/心跳清理）
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
        await registry1.flush()

        # 已完成节点计数（被杀时刻快照）
        assert data_stub.node_calls.get("read_dataset") == 1
        assert agent_stub.node_calls.get("analyze_data") == 1, (
            "被杀时刻执行中节点（首次执行未返回）"
        )

        # ---- 3. 租约有效期内接管探测被拒（租约语义）----
        early = await queue.take_over_expired(
            "worker-probe", lease_ttl_seconds=60
        )
        assert early is None, "租约未过期不可接管"

        # ---- 4. 短 TTL 真实推进租约过期 → worker-2 接管续跑 ----
        await asyncio.sleep(1.2)
        registry2 = PersistingInstanceRegistry(event_repo)
        worker2 = ExecutionWorker(
            queue,
            worker_id="worker-2-takeover",
            executor_registry=executor_registry,
            checkpoint_repository=cp_repo,
            event_repository=event_repo,
            registry=registry2,
            lease_ttl_seconds=60,
        )
        taken = await queue.take_over_expired(
            "worker-2-takeover", lease_ttl_seconds=60
        )
        assert taken is not None, "租约过期后被接管"
        assert taken.job_id == _JOB_ID
        assert taken.attempts == 2, "接管计数递增"
        await worker2.process_job(taken)

        # ---- 5. 断言：完成 + 无重复执行 + 读穿透 ----
        final_job = await queue.get(_JOB_ID)
        assert final_job is not None
        assert final_job.status == "completed", "接管后续跑至作业完成"
        assert final_job.lease_owner is None, "完成释放租约（M1 语义）"
        assert final_job.attempts == 2

        rt = registry2.get(_INSTANCE_ID)
        assert rt.status == "succeeded", "接管续跑终态 succeeded"

        # 已完成节点不重复执行（检查点续跑契约）；被杀时刻执行中节点
        # 恰重放一次（at-least-once 诚实口径，plan D3）
        assert data_stub.node_calls["read_dataset"] == 1, (
            "read_dataset 不重复执行"
        )
        assert agent_stub.node_calls["analyze_data"] == 2, (
            "被杀时刻执行中单节点恰重放一次"
        )
        assert tool_stub.node_calls["build_report"] == 1, (
            "未执行节点接管后执行一次"
        )

        # 事件仓储轨迹（两 worker 双写累积）支撑 API 读穿透快照
        await registry2.flush()
        snapshot = await read_instance_snapshot(
            _INSTANCE_ID, event_repository=event_repo
        )
        assert snapshot is not None
        assert snapshot["status"] == "succeeded"
        assert snapshot["finished_at"], "终态快照含 finished_at"

        # 事件序完整：worker-1 轨迹（INSTANCE_STARTED…analyze NODE_STARTED）
        # + worker-2 续跑轨迹（…INSTANCE_COMPLETED），sequence 严格递增
        events = await event_repo.list_for_instance(_INSTANCE_ID)
        sequences = [e.sequence for e in events]
        assert sequences == sorted(sequences) and len(set(sequences)) == len(
            sequences
        ), "事件序号严格递增（跨接管不回绕）"
        assert events[-1].event_type == EventType.INSTANCE_COMPLETED
