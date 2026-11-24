# =============================================================================
# M3 drill 子进程 runner（scripts/drill/_m3_drill_worker.py）
#
# 职责：以真实 ExecutionWorker 机械（认领/租约/心跳/接管/收口）运行
#       drill 作业；执行器为记录型阻塞桩（零外部调用，节点执行落
#       {calls_dir}/calls-{pid}.jsonl，父子进程合并断言）。
# 用法（由 drill 父脚本拉起，不直接运行）：
#   python _m3_drill_worker.py <queue_dir> <calls_dir> <cp_root> <ev_root>
#                               <yaml_path> <block_seconds> <heartbeat>
#                               <lease_ttl> [poll_interval]
# =============================================================================

import asyncio
import os
import sys
import time
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _phase(label: str) -> None:
    """相位打印（stdout 落 drill 日志，供接管时延分析）。"""
    print(f"[phase] {time.time():.3f} pid={os.getpid()} {label}", flush=True)

from orchestration_platform.domain.models.enums import NodeType  # noqa: E402
from orchestration_platform.engine.node_executor_registry import (  # noqa: E402
    NodeExecutorRegistry,
)
from orchestration_platform.engine.nodes.base import (  # noqa: E402
    NodeExecutionResult,
)
from orchestration_platform.engine.nodes.trigger_node_executor import (  # noqa: E402
    TriggerNodeExecutor,
)
from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (  # noqa: E402
    LocalFileCheckpointRepository,
)
from orchestration_platform.persistence.local_file.local_file_event_repository import (  # noqa: E402
    LocalFileEventRepository,
)
from orchestration_platform.persistence.local_file.local_file_execution_queue import (  # noqa: E402
    LocalFileExecutionQueue,
)
from orchestration_platform.worker.execution_worker import (  # noqa: E402
    ExecutionWorker,
)


class _RecordingBlockingStub:
    """记录型阻塞桩：节点执行落 jsonl（instance|node|pid|ts）+ 可选阻塞。"""

    def __init__(self, node_type: NodeType, calls_path: str, block_seconds: float):
        self._node_type = node_type
        self._calls_path = calls_path
        self._block = block_seconds

    @property
    def node_type(self) -> NodeType:
        return self._node_type

    async def execute(self, context) -> NodeExecutionResult:
        import time

        node_id = context.node_definition.node_id
        line = (
            f"{context.instance_id}|{node_id}|{os.getpid()}"
            f"|{time.time()}\n"
        )
        with open(self._calls_path, "a", encoding="utf-8") as fh:
            fh.write(line)
        if self._block > 0:
            await asyncio.sleep(self._block)
        return NodeExecutionResult(
            success=True,
            output_name=getattr(
                context.node_definition, "effective_output_name", None
            ),
            output_value={"stub": node_id},
        )


def main() -> None:
    (
        queue_dir,
        calls_dir,
        cp_root,
        ev_root,
        yaml_path,
        block_seconds,
        heartbeat,
        lease_ttl,
    ) = sys.argv[1:9]
    poll_interval = float(sys.argv[9]) if len(sys.argv) > 9 else 0.2
    _phase("runner-start")

    import orchestration_platform.worker.execution_worker as worker_mod

    _orig_process_job = worker_mod.ExecutionWorker.process_job
    _orig_run_claimed_job = worker_mod.run_claimed_job
    _orig_run_and_record = worker_mod.run_and_record

    async def _timed_process_job(self, job):
        _phase(f"process_job-enter {job.job_id} attempts={job.attempts}")
        started = time.time()
        try:
            return await _orig_process_job(self, job)
        finally:
            _phase(
                f"process_job-exit {job.job_id} "
                f"elapsed={time.time() - started:.3f}"
            )

    async def _timed_run_claimed_job(job, **kwargs):
        _phase(f"run_claimed_job-enter {job.job_id} attempts={job.attempts}")
        started = time.time()
        try:
            return await _orig_run_claimed_job(job, **kwargs)
        finally:
            _phase(
                f"run_claimed_job-exit {job.job_id} "
                f"elapsed={time.time() - started:.3f}"
            )

    async def _timed_run_and_record(*args, **kwargs):
        _phase("run_and_record-enter")
        started = time.time()
        try:
            return await _orig_run_and_record(*args, **kwargs)
        finally:
            _phase(
                f"run_and_record-exit elapsed={time.time() - started:.3f}"
            )

    worker_mod.ExecutionWorker.process_job = _timed_process_job
    worker_mod.run_claimed_job = _timed_run_claimed_job
    worker_mod.run_and_record = _timed_run_and_record

    # 接管扫描诊断：claim/take_over 每次扫描记录 running 作业的租约态
    from orchestration_platform.persistence.local_file import (
        local_file_execution_queue as lfq_mod,
    )

    _orig_to = lfq_mod.LocalFileExecutionQueue.take_over_expired

    async def _diag_take_over(self, worker_id, lease_ttl_seconds=None):
        for candidate in self._list_jobs():
            if candidate.status == "running":
                _phase(
                    f"take_over-scan job={candidate.job_id} "
                    f"owner={candidate.lease_owner} "
                    f"expires_at={candidate.lease_expires_at} "
                    f"now={self._now().isoformat()}"
                )
        result = await _orig_to(self, worker_id, lease_ttl_seconds)
        _phase(f"take_over-result {result.job_id if result else None}")
        return result

    lfq_mod.LocalFileExecutionQueue.take_over_expired = _diag_take_over

    calls_path = str(Path(calls_dir) / f"calls-{os.getpid()}.jsonl")
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())
    for node_type in (
        NodeType.DATA_SOURCE,
        NodeType.AGENT,
        NodeType.TOOL,
    ):
        registry.register(
            _RecordingBlockingStub(
                node_type, calls_path, float(block_seconds)
            )
        )

    worker = ExecutionWorker(
        LocalFileExecutionQueue(queue_dir=queue_dir),
        worker_id=f"drill-{os.getpid()}",
        executor_registry=registry,
        checkpoint_repository=LocalFileCheckpointRepository(
            storage_root=cp_root
        ),
        event_repository=LocalFileEventRepository(storage_root=ev_root),
        heartbeat_seconds=float(heartbeat),
        lease_ttl_seconds=int(lease_ttl),
        poll_interval_seconds=poll_interval,
    )
    asyncio.run(worker.run_forever())


if __name__ == "__main__":
    main()
