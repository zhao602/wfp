# =============================================================================
# 执行 Worker 单测（tests/unit/test_execution_worker.py）
#
# 覆盖（roadmap-v3 中期窗口 M2，plan worker 包）：
#   A. Worker 机械——认领→执行→complete / 重建失败→fail / 心跳续租 /
#      request_stop 退出主循环 / worker_id 形态；
#   B. 上下文重建——yaml_text 解析编译 / 工作流仓储回退 / 续跑点解析
#      （显式 resume_checkpoint_id / attempts>1 latest_for_instance / 均无）；
#   C. PersistingInstanceRegistry——注册表+事件仓储双写 / flush 收口 /
#      未知事件类型跳过 / definition_id 载荷富化；
#   D. 提交器 yaml_text 透传（worker 分支随作业携带）。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_execution_worker.py -q
# =============================================================================

import asyncio
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

import orchestration_platform.worker.execution_worker as worker_mod
from orchestration_platform.application.execution_runner import InstanceRegistry
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.enums import EventType, NodeType
from orchestration_platform.domain.models.execution_job import ExecutionJob
from orchestration_platform.domain.models.shared_state import SharedState
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
from orchestration_platform.worker.execution_worker import (
    ExecutionWorker,
    PersistingInstanceRegistry,
    WorkerJobReconstructionError,
    run_claimed_job,
)

_FOUR_NODE_YAML = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "valid_workflow_definitions"
    / "valid_sequential_four_nodes.yaml"
).read_text(encoding="utf-8")

_NOW_ISO = "2026-11-23T00:00:00+00:00"


# =============================================================================
# 测试基建：计数桩执行器 + 桩队列 + 作业构造
# =============================================================================

class _CountingStubExecutor:
    """计数桩执行器：按 node_id 统计执行次数（接管不重复执行的观测点）。"""

    def __init__(self, node_type: NodeType, block_first_node_ids=None):
        self._node_type = node_type
        self._block_first = block_first_node_ids or set()
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
            await asyncio.sleep(3600)
        return NodeExecutionResult(
            success=True,
            output_name=getattr(
                context.node_definition, "effective_output_name", None
            ),
            output_value={"stub": node_id},
        )


def _stub_executor_registry(block_first_agent: bool = False):
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())
    registry.register(_CountingStubExecutor(NodeType.DATA_SOURCE))
    registry.register(
        _CountingStubExecutor(
            NodeType.AGENT,
            block_first_node_ids={"analyze_data"} if block_first_agent else None,
        )
    )
    registry.register(_CountingStubExecutor(NodeType.TOOL))
    return registry


class _StubQueue:
    """桩队列：记录调用序列（complete/fail/heartbeat 断言观测点）。"""

    def __init__(self):
        self.completed: list = []
        self.failed: list = []
        self.heartbeats: list = []
        self.claims: list = []

    async def enqueue(self, job):
        return job

    async def claim(self, worker_id, lease_ttl_seconds=None):
        self.claims.append(worker_id)
        return None

    async def take_over_expired(self, worker_id, lease_ttl_seconds=None):
        return None

    async def heartbeat(self, job_id, worker_id):
        self.heartbeats.append((job_id, worker_id))
        return True

    async def complete(self, job_id, worker_id):
        self.completed.append((job_id, worker_id))
        return True

    async def fail(self, job_id, worker_id, reason=""):
        self.failed.append((job_id, worker_id, reason))
        return True

    async def get(self, job_id):
        return None


def _mk_job(
    instance_id="inst-w1",
    job_id="job-w1",
    yaml_text=_FOUR_NODE_YAML,
    resume_checkpoint_id=None,
    attempts=1,
):
    return ExecutionJob(
        job_id=job_id,
        instance_id=instance_id,
        definition_id="sequential_four_nodes",
        yaml_text=yaml_text,
        initial_inputs={"topic": "初始输入"},
        trigger_source="manual",
        trace_id=f"trace-{instance_id}",
        resume_checkpoint_id=resume_checkpoint_id,
        idempotency_key=instance_id,
        status="running",
        attempts=attempts,
        created_at=_NOW_ISO,
    )


def _mk_checkpoint(instance_id, round_index, checkpoint_id, next_node_id=None):
    return Checkpoint(
        checkpoint_id=checkpoint_id,
        instance_id=instance_id,
        completed_node_id=None,
        next_node_id=next_node_id,
        state_snapshot=SharedState(instance_id=instance_id),
        round_index=round_index,
    )


# =============================================================================
# A. Worker 机械
# =============================================================================

class TestWorkerMechanics:

    def test_worker_id_default_format(self):
        queue = _StubQueue()
        worker = ExecutionWorker(queue)
        assert worker.worker_id.startswith("worker-")
        assert socket.gethostname() in worker.worker_id
        assert str(os.getpid()) in worker.worker_id

    async def test_process_job_completes_on_success(self, tmp_path):
        queue = _StubQueue()
        cp_repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        worker = ExecutionWorker(
            queue,
            worker_id="worker-test",
            executor_registry=_stub_executor_registry(),
            checkpoint_repository=cp_repo,
            registry=InstanceRegistry(),
        )
        job = _mk_job()
        await worker.process_job(job)
        assert queue.completed == [(job.job_id, "worker-test")]
        assert queue.failed == []

    async def test_process_job_fails_on_bad_yaml(self, tmp_path):
        queue = _StubQueue()
        worker = ExecutionWorker(
            queue,
            worker_id="worker-test",
            executor_registry=_stub_executor_registry(),
            checkpoint_repository=LocalFileCheckpointRepository(
                storage_root=str(tmp_path)
            ),
            registry=InstanceRegistry(),
        )
        job = _mk_job(yaml_text="not: [valid yaml")
        await worker.process_job(job)
        assert queue.completed == []
        assert len(queue.failed) == 1
        job_id, worker_id, reason = queue.failed[0]
        assert job_id == job.job_id
        assert worker_id == "worker-test"
        assert reason, "失败原因非空"

    async def test_heartbeat_renews_during_run(self, tmp_path):
        queue = _StubQueue()
        executor_registry = _stub_executor_registry(block_first_agent=True)

        async def _slow_run(job, cancel_event=None):
            await asyncio.sleep(0.08)
            return True

        worker = ExecutionWorker(
            queue,
            worker_id="worker-hb",
            executor_registry=executor_registry,
            checkpoint_repository=LocalFileCheckpointRepository(
                storage_root=str(tmp_path)
            ),
            registry=InstanceRegistry(),
            heartbeat_seconds=0.01,
        )
        worker._run_job = _slow_run
        job = _mk_job()
        await worker.process_job(job)
        assert len(queue.heartbeats) >= 2, "执行期内心跳至少续租两次"
        assert queue.completed == [(job.job_id, "worker-hb")]

    async def test_run_forever_exits_on_request_stop(self):
        queue = _StubQueue()
        worker = ExecutionWorker(
            queue,
            worker_id="worker-stop",
            poll_interval_seconds=0.01,
        )

        async def _drive():
            task = asyncio.create_task(worker.run_forever())
            await asyncio.sleep(0.05)
            worker.request_stop()
            await asyncio.wait_for(task, timeout=2)

        await _drive()
        assert queue.claims, "主循环进入过认领轮询"


# =============================================================================
# B. 上下文重建与续跑点解析
# =============================================================================

class TestRunClaimedJob:

    async def _spy_run_and_record(self, monkeypatch):
        captured = {}

        async def _spy(
            scheduler, compiled, initial_state, instance_id, trace_id,
            registry, **kwargs,
        ):
            captured["instance_id"] = instance_id
            captured["trace_id"] = trace_id
            captured["initial_inputs"] = dict(initial_state.node_outputs)
            captured["resume_checkpoint"] = kwargs.get("resume_checkpoint")
            captured["trigger_source"] = kwargs.get("trigger_source")
            registry.update_status(instance_id, "succeeded")
            return None

        monkeypatch.setattr(worker_mod, "run_and_record", _spy)
        return captured

    async def test_yaml_text_parse_compile_and_run(
        self, monkeypatch, tmp_path
    ):
        captured = await self._spy_run_and_record(monkeypatch)
        registry = InstanceRegistry()
        ok = await run_claimed_job(
            _mk_job(),
            registry=registry,
            executor_registry=_stub_executor_registry(),
            checkpoint_repository=LocalFileCheckpointRepository(
                storage_root=str(tmp_path)
            ),
        )
        assert ok is True
        assert captured["instance_id"] == "inst-w1"
        assert captured["initial_inputs"] == {"topic": "初始输入"}
        assert captured["trigger_source"] == "manual"
        assert captured["resume_checkpoint"] is None

    async def test_explicit_resume_checkpoint_resolved(
        self, monkeypatch, tmp_path
    ):
        captured = await self._spy_run_and_record(monkeypatch)
        cp_repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        await cp_repo.append(
            _mk_checkpoint("inst-w1", 2, "cp-explicit", next_node_id="read_dataset")
        )
        ok = await run_claimed_job(
            _mk_job(resume_checkpoint_id="cp-explicit"),
            registry=InstanceRegistry(),
            executor_registry=_stub_executor_registry(),
            checkpoint_repository=cp_repo,
        )
        assert ok is True
        assert captured["resume_checkpoint"] is not None
        assert captured["resume_checkpoint"].checkpoint_id == "cp-explicit"

    async def test_takeover_uses_latest_checkpoint(self, monkeypatch, tmp_path):
        """attempts>1（被接管）：续跑点 = latest_for_instance。"""
        captured = await self._spy_run_and_record(monkeypatch)
        cp_repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        await cp_repo.append(
            _mk_checkpoint("inst-w1", 1, "cp-r1", next_node_id="read_dataset")
        )
        await cp_repo.append(
            _mk_checkpoint("inst-w1", 2, "cp-r2", next_node_id="analyze_data")
        )
        ok = await run_claimed_job(
            _mk_job(attempts=2),
            registry=InstanceRegistry(),
            executor_registry=_stub_executor_registry(),
            checkpoint_repository=cp_repo,
        )
        assert ok is True
        assert captured["resume_checkpoint"] is not None
        assert captured["resume_checkpoint"].checkpoint_id == "cp-r2"

    async def test_takeover_without_checkpoint_runs_from_scratch(
        self, monkeypatch, tmp_path
    ):
        """被接管但无检查点：整段重放（at-least-once 诚实口径）。"""
        captured = await self._spy_run_and_record(monkeypatch)
        ok = await run_claimed_job(
            _mk_job(attempts=2),
            registry=InstanceRegistry(),
            executor_registry=_stub_executor_registry(),
            checkpoint_repository=LocalFileCheckpointRepository(
                storage_root=str(tmp_path)
            ),
        )
        assert ok is True
        assert captured["resume_checkpoint"] is None

    async def test_missing_definition_raises_reconstruction_error(
        self, monkeypatch, tmp_path
    ):
        class _MissingWorkflowRepo:
            async def get(self, definition_id, version=None):
                raise LookupError("wf not found")

        with pytest.raises(WorkerJobReconstructionError):
            await run_claimed_job(
                _mk_job(yaml_text=None),
                registry=InstanceRegistry(),
                executor_registry=_stub_executor_registry(),
                checkpoint_repository=LocalFileCheckpointRepository(
                    storage_root=str(tmp_path)
                ),
                workflow_repository=_MissingWorkflowRepo(),
            )

    async def test_workflow_repository_fallback(self, monkeypatch, tmp_path):
        """无 yaml_text：从工作流仓储取定义重编译（cron 等仓储定义流）。"""
        from orchestration_platform.workflow.yaml_workflow_parser import (
            YamlWorkflowParser,
        )

        captured = await self._spy_run_and_record(monkeypatch)

        class _FakeWorkflowRepo:
            async def get(self, definition_id, version=None):
                return YamlWorkflowParser().parse(_FOUR_NODE_YAML)

        ok = await run_claimed_job(
            _mk_job(yaml_text=None),
            registry=InstanceRegistry(),
            executor_registry=_stub_executor_registry(),
            checkpoint_repository=LocalFileCheckpointRepository(
                storage_root=str(tmp_path)
            ),
            workflow_repository=_FakeWorkflowRepo(),
        )
        assert ok is True
        assert captured["instance_id"] == "inst-w1"


# =============================================================================
# C. PersistingInstanceRegistry 双写
# =============================================================================

class TestPersistingInstanceRegistry:

    async def test_dual_write_and_flush(self, tmp_path):
        repo = LocalFileEventRepository(storage_root=str(tmp_path))
        registry = PersistingInstanceRegistry(repo)
        rt = worker_mod.InstanceRuntime(
            instance_id="inst-p1", definition_id="def-p1"
        )
        registry.register(rt)
        registry.append_event(
            "inst-p1",
            {
                "event_id": "evt-p1-1",
                "instance_id": "inst-p1",
                "event_type": "INSTANCE_STARTED",
                "node_id": None,
                "timestamp": _NOW_ISO,
                "payload": {},
            },
        )
        registry.append_event(
            "inst-p1",
            {
                "event_id": "evt-p1-2",
                "instance_id": "inst-p1",
                "event_type": "NODE_STARTED",
                "node_id": "read_dataset",
                "timestamp": _NOW_ISO,
                "payload": {},
            },
        )
        await registry.flush()
        events = await repo.list_for_instance("inst-p1")
        assert len(events) == 2
        assert events[0].event_type == EventType.INSTANCE_STARTED
        assert events[0].payload.get("definition_id") == "def-p1"
        assert events[1].event_type == EventType.NODE_STARTED
        assert events[1].node_id == "read_dataset"

    async def test_node_completed_maps_to_node_succeeded(self, tmp_path):
        """注册表 NODE_COMPLETED 口径 → 持久化 NODE_SUCCEEDED（读侧反映射）。"""
        repo = LocalFileEventRepository(storage_root=str(tmp_path))
        registry = PersistingInstanceRegistry(repo)
        registry.register(
            worker_mod.InstanceRuntime(
                instance_id="inst-p2", definition_id="def-p2"
            )
        )
        registry.append_event(
            "inst-p2",
            {
                "event_id": "evt-p2-1",
                "instance_id": "inst-p2",
                "event_type": "NODE_COMPLETED",
                "node_id": "read_dataset",
                "timestamp": _NOW_ISO,
                "payload": {"stub": "read_dataset"},
            },
        )
        await registry.flush()
        events = await repo.list_for_instance("inst-p2")
        assert events[0].event_type == EventType.NODE_SUCCEEDED

    async def test_unknown_event_type_not_persisted(self, tmp_path):
        repo = LocalFileEventRepository(storage_root=str(tmp_path))
        registry = PersistingInstanceRegistry(repo)
        registry.register(
            worker_mod.InstanceRuntime(
                instance_id="inst-p3", definition_id="def-p3"
            )
        )
        registry.append_event(
            "inst-p3",
            {
                "event_id": "evt-p3-1",
                "instance_id": "inst-p3",
                "event_type": "SOME_FUTURE_TYPE",
                "node_id": None,
                "timestamp": _NOW_ISO,
                "payload": {},
            },
        )
        await registry.flush()
        events = await repo.list_for_instance("inst-p3")
        assert events == [], "未映射事件类型不持久化（前向兼容口径维持）"

    async def test_instance_cancelled_persisted(self, tmp_path):
        """M3（design D5）：runner 取消分支的 INSTANCE_CANCELLED 注册表
        事件持久化（running 作业取消读穿透可见性）。"""
        repo = LocalFileEventRepository(storage_root=str(tmp_path))
        registry = PersistingInstanceRegistry(repo)
        registry.register(
            worker_mod.InstanceRuntime(
                instance_id="inst-p3c", definition_id="def-p3c"
            )
        )
        registry.append_event(
            "inst-p3c",
            {
                "event_id": "evt-p3c-1",
                "instance_id": "inst-p3c",
                "event_type": "INSTANCE_CANCELLED",
                "node_id": None,
                "timestamp": _NOW_ISO,
                "payload": {},
            },
        )
        await registry.flush()
        events = await repo.list_for_instance("inst-p3c")
        assert len(events) == 1
        assert events[0].event_type == EventType.INSTANCE_CANCELLED
        assert events[0].payload.get("definition_id") == "def-p3c"

    async def test_persist_failure_does_not_block_registry(self, tmp_path):
        class _FailingRepo:
            async def append(self, event):
                raise RuntimeError("storage down")

        registry = PersistingInstanceRegistry(_FailingRepo())
        registry.register(
            worker_mod.InstanceRuntime(
                instance_id="inst-p4", definition_id="def-p4"
            )
        )
        registry.append_event(
            "inst-p4",
            {
                "event_id": "evt-p4-1",
                "instance_id": "inst-p4",
                "event_type": "INSTANCE_STARTED",
                "node_id": None,
                "timestamp": _NOW_ISO,
                "payload": {},
            },
        )
        await registry.flush()
        events = registry.get_events("inst-p4")
        assert len(events) == 1, "持久化失败不阻断注册表主链（INV-01）"


# =============================================================================
# D. 提交器 yaml_text 透传（worker 分支）
# =============================================================================

class TestSubmitterYamlText:

    async def test_worker_branch_carries_yaml_text(self, monkeypatch):
        import orchestration_platform.application.execution_runner as runner_mod

        monkeypatch.setattr(
            runner_mod,
            "get_settings",
            lambda: SimpleNamespace(execution_mode="worker"),
        )

        enqueued = []

        class _FakeQueue:
            async def enqueue(self, job):
                enqueued.append(job)
                return job

        rt = runner_mod.InstanceRuntime(
            instance_id="inst-yaml", definition_id="def-yaml"
        )
        submitter = runner_mod.ExecutionSubmitter(queue=_FakeQueue())
        returned = await submitter.submit(
            SimpleNamespace(),
            SimpleNamespace(definition_version=7),
            SimpleNamespace(node_outputs={}),
            "inst-yaml",
            "trace-yaml",
            runner_mod.InstanceRegistry(),
            rt=rt,
            yaml_text="spec_version: '1.0'",
        )
        assert returned is None
        job = enqueued[0]
        assert job.yaml_text == "spec_version: '1.0'"
        assert job.definition_version == 7
