# =============================================================================
# M5 D9 心跳租约 TTL 单测（tests/unit/test_heartbeat_ttl.py）
#
# 覆盖（design D3，T1）：
#   A. LocalFileExecutionQueue 心跳 TTL 语义——显式 TTL 续租 / 缺省回退
#      settings 默认 / 未认领与他人租约拒绝；
#   B. worker 心跳调用点——worker 租约 TTL 随心跳传递（新签名桩断言实参）；
#      D9 前旧签名（双参）队列协议兼容（构造期签名探测，不再运行期
#      TypeError）；VAR_KEYWORD 签名按新签名对待；
#   C. 协议与实现静态接线——IExecutionQueue.heartbeat 声明可选 TTL 参；
#      local/PG 实现心跳消费 lease_ttl_seconds（无 PG 环境静态断言）。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_heartbeat_ttl.py -q
# =============================================================================

import asyncio
import inspect
import re
from datetime import datetime, timezone
from pathlib import Path


from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.execution_job import ExecutionJob
from orchestration_platform.persistence.abstractions.execution_queue import (
    IExecutionQueue,
)
from orchestration_platform.persistence.local_file.local_file_execution_queue import (
    LocalFileExecutionQueue,
)
from orchestration_platform.worker.execution_worker import ExecutionWorker

_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_QUEUE_SOURCE = (
    _ROOT
    / "src"
    / "orchestration_platform"
    / "persistence"
    / "local_file"
    / "local_file_execution_queue.py"
).read_text(encoding="utf-8")
_PG_QUEUE_SOURCE = (
    _ROOT
    / "src"
    / "orchestration_platform"
    / "persistence"
    / "postgres"
    / "postgres_execution_queue.py"
).read_text(encoding="utf-8")


def _method_body(source: str, name: str) -> str:
    """提取源码方法体（静态断言载体，test_pg_takeover_atomicity 先例）。"""
    match = re.search(
        rf"def {name}\(.*?(?=\n    (?:async )?def |\n\nclass |\Z)",
        source,
        re.S,
    )
    assert match is not None, f"未找到方法 {name}"
    return match.group(0)


def _make_job(job_id="job-hb", instance_id="inst-hb"):
    return ExecutionJob(
        job_id=job_id,
        instance_id=instance_id,
        definition_id="wf-hb",
        initial_inputs={},
        trigger_source="manual",
        trace_id="trace-hb",
        idempotency_key=instance_id,
    )


def _make_queue(tmp_path, name="q"):
    return LocalFileExecutionQueue(queue_dir=str(tmp_path / name))


def _lease_remaining_seconds(job: ExecutionJob) -> float:
    expires = datetime.fromisoformat(job.lease_expires_at)
    return (expires - datetime.now(timezone.utc)).total_seconds()


# =============================================================================
# A. LocalFileExecutionQueue 心跳 TTL 语义
# =============================================================================


class TestLocalHeartbeatTtl:

    async def test_heartbeat_with_explicit_ttl_renews_lease(self, tmp_path):
        """D9：心跳 TTL 参生效——续租到 worker 指定时长而非队列默认。"""
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())
        await queue.claim("worker-1", lease_ttl_seconds=300)
        job = await queue.get("job-hb")
        assert 280 < _lease_remaining_seconds(job) <= 300

        renewed = await queue.heartbeat(
            "job-hb", "worker-1", lease_ttl_seconds=120
        )
        assert renewed is True
        job = await queue.get("job-hb")
        assert 100 < _lease_remaining_seconds(job) <= 120, (
            "显式 TTL 续租生效（非认领 TTL、非默认）"
        )

    async def test_heartbeat_without_ttl_falls_back_settings_default(
        self, tmp_path
    ):
        """缺省 TTL → settings 默认（既有行为，零破坏）。"""
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())
        await queue.claim("worker-1", lease_ttl_seconds=300)
        default_ttl = get_settings().execution_queue_lease_seconds

        renewed = await queue.heartbeat("job-hb", "worker-1")
        assert renewed is True
        job = await queue.get("job-hb")
        assert 0 < _lease_remaining_seconds(job) <= default_ttl

    async def test_heartbeat_rejects_unclaimed_or_foreign_worker(self, tmp_path):
        """未认领作业 / 非租约持有者心跳拒绝（租约归属不变量）。"""
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())
        assert (
            await queue.heartbeat("job-hb", "worker-1", lease_ttl_seconds=60)
            is False
        )
        await queue.claim("worker-1")
        assert (
            await queue.heartbeat("job-hb", "worker-2", lease_ttl_seconds=60)
            is False
        )


# =============================================================================
# B. worker 心跳调用点（TTL 传递 + 旧签名协议兼容）
# =============================================================================


class _TtlSpyQueue:
    """新签名桩队列：记录心跳 lease_ttl_seconds 实参（D9 观测点）。"""

    def __init__(self):
        self.heartbeats: list = []
        self.completed: list = []

    async def enqueue(self, job):
        return job

    async def claim(self, worker_id, lease_ttl_seconds=None):
        return None

    async def take_over_expired(self, worker_id, lease_ttl_seconds=None):
        return None

    async def heartbeat(self, job_id, worker_id, lease_ttl_seconds=None):
        self.heartbeats.append((job_id, worker_id, lease_ttl_seconds))
        return True

    async def complete(self, job_id, worker_id):
        self.completed.append((job_id, worker_id))
        return True

    async def fail(self, job_id, worker_id, reason=""):
        return True

    async def get(self, job_id):
        return None


class _LegacyHeartbeatQueue:
    """D9 前旧签名（双参）队列：结构化协议下仍是合法实现（兼容载体）。"""

    def __init__(self):
        self.heartbeats: list = []
        self.completed: list = []

    async def enqueue(self, job):
        return job

    async def claim(self, worker_id, lease_ttl_seconds=None):
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
        return True

    async def get(self, job_id):
        return None


class _VarKeywordHeartbeatQueue(_LegacyHeartbeatQueue):
    """VAR_KEYWORD 心跳签名：探测按新签名对待（**kwargs 容纳 TTL）。"""

    async def heartbeat(self, job_id, worker_id, **kwargs):
        self.heartbeats.append((job_id, worker_id, kwargs))
        return True


def _stub_executor_registry():
    from orchestration_platform.domain.models.enums import NodeType
    from orchestration_platform.engine.node_executor_registry import (
        NodeExecutorRegistry,
    )
    from orchestration_platform.engine.nodes.trigger_node_executor import (
        TriggerNodeExecutor,
    )

    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())

    class _NoopExecutor:
        def __init__(self) -> None:
            self.node_type = NodeType.DATA_TRANSFORM

        async def execute(self, context):
            from orchestration_platform.engine.nodes.base import (
                NodeExecutionResult,
            )

            return NodeExecutionResult(success=True, output_name=None)

    registry.register(_NoopExecutor())
    return registry


def _mk_worker(queue, tmp_path, **overrides):
    from orchestration_platform.application.execution_runner import InstanceRegistry
    from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
        LocalFileCheckpointRepository,
    )

    params = dict(
        worker_id="worker-ttl",
        executor_registry=_stub_executor_registry(),
        checkpoint_repository=LocalFileCheckpointRepository(
            storage_root=str(tmp_path)
        ),
        registry=InstanceRegistry(),
        heartbeat_seconds=0.01,
        lease_ttl_seconds=77,
    )
    params.update(overrides)
    return ExecutionWorker(queue, **params)


async def _drive_slow_run(worker):
    """慢执行驱动：心跳循环至少续租两次后正常收口。"""

    async def _slow_run(job, cancel_event=None):
        await asyncio.sleep(0.08)
        return True

    worker._run_job = _slow_run
    await worker.process_job(_make_job())


class TestWorkerHeartbeatCallSite:

    async def test_worker_passes_own_lease_ttl(self, tmp_path):
        """D9：worker 租约 TTL 随心跳传递（桩断言实参 = worker 配置）。"""
        queue = _TtlSpyQueue()
        worker = _mk_worker(queue, tmp_path)

        await _drive_slow_run(worker)

        assert queue.heartbeats, "执行期内心跳已续租"
        assert all(h[2] == 77 for h in queue.heartbeats), (
            "心跳携带 worker 租约 TTL（非队列默认）"
        )
        assert queue.completed, "作业正常收口"

    async def test_worker_heartbeat_legacy_signature_compat(self, tmp_path):
        """旧签名（双参）队列心跳不再 TypeError（签名探测兼容）。"""
        queue = _LegacyHeartbeatQueue()
        worker = _mk_worker(queue, tmp_path)

        await _drive_slow_run(worker)

        assert queue.heartbeats, "旧签名队列心跳成功（双参调用）"
        assert all(len(h) == 2 for h in queue.heartbeats)
        assert queue.completed

    async def test_worker_heartbeat_var_keyword_signature_accepts_ttl(
        self, tmp_path
    ):
        """VAR_KEYWORD 心跳签名：探测按新签名对待，TTL 实参可达。"""
        queue = _VarKeywordHeartbeatQueue()
        worker = _mk_worker(queue, tmp_path)

        await _drive_slow_run(worker)

        assert queue.heartbeats
        assert all(h[2].get("lease_ttl_seconds") == 77 for h in queue.heartbeats)

    def test_probe_detects_signature_forms(self, tmp_path):
        """构造期签名探测三形态：新签名 True / 旧签名 False / VAR_KEYWORD True。"""
        assert (
            _mk_worker(_TtlSpyQueue(), tmp_path)._heartbeat_accepts_ttl is True
        )
        assert (
            _mk_worker(_LegacyHeartbeatQueue(), tmp_path)._heartbeat_accepts_ttl
            is False
        )
        assert (
            _mk_worker(
                _VarKeywordHeartbeatQueue(), tmp_path
            )._heartbeat_accepts_ttl
            is True
        )


# =============================================================================
# C. 协议与实现静态接线（无 PG 环境静态断言）
# =============================================================================


class TestHeartbeatTtlStaticWiring:

    def test_protocol_declares_optional_ttl_param(self):
        params = inspect.signature(IExecutionQueue.heartbeat).parameters
        assert "lease_ttl_seconds" in params
        assert params["lease_ttl_seconds"].default is None

    def test_local_heartbeat_consumes_ttl_param(self):
        body = _method_body(_LOCAL_QUEUE_SOURCE, "heartbeat")
        assert "_default_ttl_seconds(lease_ttl_seconds)" in body

    def test_pg_heartbeat_sql_binds_ttl_param(self):
        body = _method_body(_PG_QUEUE_SOURCE, "heartbeat")
        assert "$3::int" in body, "TTL 作为 SQL 绑定参传入"
        assert "_default_ttl_seconds(lease_ttl_seconds)" in body
