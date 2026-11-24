# =============================================================================
# M5 队列 worker 方法 + 配额 worker 口径单测
# （tests/unit/test_queue_worker_methods.py）
#
# 覆盖（design D6，T5 + T3 配额分支）：
#   A. LocalFileExecutionQueue worker 方法语义——count_active_by_tenant
#      （租户过滤 + 终态排除）/ find_latest_by_instance（最新优先含终态 /
#      缺失 None）；
#   B. 协议与 PG 静态接线——IExecutionQueue 声明双方法；PG SQL 计数口径
#      （tenant + pending/running）与最新作业查询（ORDER BY created_at DESC
#      LIMIT 1）；
#   C. 配额闸门 worker 口径——worker 模式计数走队列活跃作业（design D6）；
#      拒绝零副作用（不入队/不注册）；in_process 计数维持注册表口径
#      （design D6 红线回归）。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_queue_worker_methods.py -q
# =============================================================================

import inspect
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestration_platform.api.routers import execution_instance_router as inst_router
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import TenantQuotaExceededError
from orchestration_platform.domain.models.execution_job import ExecutionJob
from orchestration_platform.persistence.abstractions.execution_queue import (
    IExecutionQueue,
)
from orchestration_platform.persistence.local_file.local_file_execution_queue import (
    LocalFileExecutionQueue,
)

_ROOT = Path(__file__).resolve().parents[2]
_PG_QUEUE_SOURCE = (
    _ROOT
    / "src"
    / "orchestration_platform"
    / "persistence"
    / "postgres"
    / "postgres_execution_queue.py"
).read_text(encoding="utf-8")

_YAML = """spec_version: "1.0"
definition_id: m5_quota_flow
name: M5 配额流水线
definition_version: 1
inputs:
  - name: items
    type: array
    required: true

outputs:
  - name: analysis
    type: object

nodes:
  - node_id: trigger_manual
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual

  - node_id: transform_a
    name: 数据转换
    node_type: data_transform
    configuration:
      operation: map
      input_dataset_reference: items

  - node_id: agent_b
    name: 分析节点
    node_type: agent
    output_name: analysis
    configuration:
      model_identifier: primary-model
      system_prompt: 你是一名数据分析师。

edges:
  - edge_id: e1
    source_node_id: trigger_manual
    target_node_id: transform_a
  - edge_id: e2
    source_node_id: transform_a
    target_node_id: agent_b
"""


def _method_body(source: str, name: str) -> str:
    """提取源码方法体（静态断言载体）。"""
    match = re.search(
        rf"def {name}\(.*?(?=\n    (?:async )?def |\n\nclass |\Z)",
        source,
        re.S,
    )
    assert match is not None, f"未找到方法 {name}"
    return match.group(0)


def _make_job(
    job_id,
    instance_id,
    tenant_id=None,
    created_at=None,
    status="pending",
):
    return ExecutionJob(
        job_id=job_id,
        instance_id=instance_id,
        tenant_id=tenant_id,
        definition_id="wf-m5",
        initial_inputs={},
        trigger_source="manual",
        trace_id=f"trace-{job_id}",
        idempotency_key=f"key-{job_id}",
        status=status,
        created_at=created_at
        or datetime.now(timezone.utc).isoformat(),
    )


def _make_queue(tmp_path, name="q"):
    return LocalFileExecutionQueue(queue_dir=str(tmp_path / name))


# =============================================================================
# A. LocalFileExecutionQueue worker 方法语义（T5）
# =============================================================================


class TestLocalWorkerMethods:

    async def test_count_active_by_tenant_filters_tenant_and_terminal(
        self, tmp_path
    ):
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job("j1", "i1", tenant_id="t-1"))
        await queue.enqueue(_make_job("j2", "i2", tenant_id="t-2"))
        await queue.enqueue(
            _make_job("j3", "i3", tenant_id="t-1", status="completed")
        )
        await queue.enqueue(_make_job("j4", "i4", tenant_id=None))

        assert await queue.count_active_by_tenant("t-1") == 1, "终态不计"
        assert await queue.count_active_by_tenant("t-2") == 1
        assert await queue.count_active_by_tenant(None) == 1
        assert await queue.count_active_by_tenant("t-none") == 0

    async def test_find_latest_by_instance_prefers_newest_including_terminal(
        self, tmp_path
    ):
        queue = _make_queue(tmp_path)
        t0 = "2026-01-01T00:00:00+00:00"
        t1 = "2026-01-02T00:00:00+00:00"
        await queue.enqueue(_make_job("ja", "i-l", created_at=t0))
        await queue.enqueue(
            _make_job("jb", "i-l", created_at=t1, status="completed")
        )
        await queue.enqueue(_make_job("jc", "i-other", created_at=t1))

        found = await queue.find_latest_by_instance("i-l")
        assert found is not None
        assert found.job_id == "jb", "最新作业优先（含终态，载荷补齐口径）"

    async def test_find_latest_by_instance_missing_returns_none(self, tmp_path):
        queue = _make_queue(tmp_path)
        assert await queue.find_latest_by_instance("i-none") is None


# =============================================================================
# B. 协议与 PG 静态接线
# =============================================================================


class TestProtocolAndPgStatic:

    def test_protocol_declares_worker_methods(self):
        for name in ("count_active_by_tenant", "find_latest_by_instance"):
            assert hasattr(IExecutionQueue, name), f"协议缺 {name}"
        assert (
            "tenant_id"
            in inspect.signature(
                IExecutionQueue.count_active_by_tenant
            ).parameters
        )
        assert (
            "instance_id"
            in inspect.signature(
                IExecutionQueue.find_latest_by_instance
            ).parameters
        )

    def test_pg_count_active_sql_filters_tenant_and_active_statuses(self):
        body = _method_body(_PG_QUEUE_SOURCE, "count_active_by_tenant")
        assert "tenant_id = $1" in body
        assert "status IN ('pending', 'running')" in body, (
            "计数口径 = 租户 + 活跃（pending/running）作业"
        )

    def test_pg_find_latest_sql_orders_newest_first(self):
        body = _method_body(_PG_QUEUE_SOURCE, "find_latest_by_instance")
        assert "ORDER BY created_at DESC" in body
        assert "LIMIT 1" in body


# =============================================================================
# C. 配额闸门 worker 口径（design D6）
# =============================================================================


class _RecordingQuotaGate:
    """记录型配额闸门桩：记录 (tenant_id, active_count) 调用序列。"""

    def __init__(self, limit=None):
        self.calls: list = []
        self._limit = limit

    async def ensure_capacity(self, tenant_id, active_count):
        self.calls.append((tenant_id, active_count))
        if self._limit is not None and active_count >= self._limit:
            raise TenantQuotaExceededError(tenant_id, active_count, self._limit)


@pytest.fixture
def worker_env(tmp_path, monkeypatch, request):
    """执行模式可参数化（默认 worker）+ 存储根重定向。"""
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    monkeypatch.setenv("EXECUTION_MODE", getattr(request, "param", "worker"))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


async def _start_with_tenant(tenant_id):
    body = inst_router.StartInstanceBody(
        definition_id="m5_quota_flow",
        yaml_text=_YAML,
        inputs={"items": [{"id": 1}]},
    )
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=tenant_id))
    return await inst_router.start_instance(body, request)


class TestQuotaWorkerCounting:

    async def test_worker_mode_counts_queue_active_jobs(
        self, worker_env, tmp_path, monkeypatch
    ):
        """worker 模式配额计数走队列活跃作业口径（design D6）。"""
        queue = _make_queue(tmp_path, "quota-q")
        monkeypatch.setattr(inst_router._execution_submitter, "_queue", queue)
        monkeypatch.setattr(inst_router, "_execution_queue_instance", queue)
        monkeypatch.setattr(inst_router, "_execution_queue_failed", False)
        await queue.enqueue(_make_job("jq1", "iq1", tenant_id="t-quota"))

        gate = _RecordingQuotaGate()
        monkeypatch.setattr(inst_router, "get_quota_gate", lambda: gate)

        await _start_with_tenant("t-quota")

        assert gate.calls == [("t-quota", 1)], (
            "计数 = 该租户队列 pending/running 作业数"
        )

    async def test_worker_mode_quota_rejection_zero_side_effects(
        self, worker_env, tmp_path, monkeypatch
    ):
        """超配额拒绝零副作用：不入队、不注册。"""
        queue = _make_queue(tmp_path, "quota-q")
        monkeypatch.setattr(inst_router._execution_submitter, "_queue", queue)
        monkeypatch.setattr(inst_router, "_execution_queue_instance", queue)
        monkeypatch.setattr(inst_router, "_execution_queue_failed", False)
        gate = _RecordingQuotaGate(limit=1)
        monkeypatch.setattr(inst_router, "get_quota_gate", lambda: gate)
        await queue.enqueue(_make_job("jq0", "iq0", tenant_id="t-quota"))

        with pytest.raises(TenantQuotaExceededError):
            await _start_with_tenant("t-quota")

        assert gate.calls == [("t-quota", 1)]
        assert [j.job_id for j in queue._list_jobs()] == ["jq0"], "不入队"
        assert inst_router._instance_registry.count_active_by_tenant(
            "t-quota"
        ) == 0

    @pytest.mark.parametrize("worker_env", ["in_process"], indirect=True)
    async def test_in_process_mode_counts_registry(
        self, worker_env, monkeypatch
    ):
        """in_process 计数维持注册表口径（design D6 红线零变化）。"""
        from orchestration_platform.application.execution_runner import (
            InstanceRuntime,
        )

        class _NoopSubmitter:
            async def submit(self, *args, **kwargs):
                return None

        gate = _RecordingQuotaGate()
        monkeypatch.setattr(inst_router, "get_quota_gate", lambda: gate)
        monkeypatch.setattr(inst_router, "_execution_submitter", _NoopSubmitter())

        rt = InstanceRuntime(
            instance_id="iq-inproc",
            definition_id="wf-m5",
            tenant_id="t-inproc",
        )
        rt.status = "running"
        inst_router._instance_registry.register(rt)
        try:
            await _start_with_tenant("t-inproc")
            assert gate.calls == [("t-inproc", 1)], (
                "in_process 计数 = 注册表活跃实例数（红线回归）"
            )
        finally:
            inst_router._instance_registry.remove("iq-inproc")
