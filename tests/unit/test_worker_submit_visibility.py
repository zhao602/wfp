# =============================================================================
# M5 worker 模式提交可见窗单测（tests/unit/test_worker_submit_visibility.py）
#
# 覆盖（design D2/D5，T3+T4）：
#   1. worker 模式 POST /instances 不注册（注册表保持空，M2 design D5 的
#      404 窗口径消除基础）+ 作业入队（yaml_text/幂等键=instance_id）；
#   2. 可见窗 INSTANCE_QUEUED 落账（提交即落事件）——窗口期（提交→认领）
#      GET 推导 pending（不再 404）、/events 读穿透、/results outputs=None、
#      SSE 预检建流、cancel pending 作业盲区事件回归；
#   3. in_process 注册表路径零变化由既有用例回归（不重复覆盖）。
# 隔离：LOCAL_STORAGE_ROOT + EXECUTION_MODE=worker 环境重定向 + 提交器
#      队列注入（不落全局 ./.data）。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_worker_submit_visibility.py -q
# =============================================================================

import uuid
from types import SimpleNamespace

import pytest

from orchestration_platform.api import instance_read_through as read_through
from orchestration_platform.api.routers import execution_instance_router as inst_router
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import EventType
from orchestration_platform.persistence.local_file.local_file_event_repository import (
    LocalFileEventRepository,
)
from orchestration_platform.persistence.local_file.local_file_execution_queue import (
    LocalFileExecutionQueue,
)

_YAML = """spec_version: "1.0"
definition_id: m5_visible_flow
name: M5 可见窗流水线
definition_version: 1
inputs:
  - name: items
    type: array
    required: true
    description: 待转换数据集

outputs:
  - name: analysis
    type: object
    description: 分析结果

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


@pytest.fixture
def worker_env(tmp_path, monkeypatch):
    """worker 模式 + 存储根重定向（settings 缓存刷新收口）。"""
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    monkeypatch.setenv("EXECUTION_MODE", "worker")
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def queue(tmp_path, monkeypatch) -> LocalFileExecutionQueue:
    """独立临时目录执行队列（提交器 + 取消路径注入）。"""
    q = LocalFileExecutionQueue(queue_dir=str(tmp_path / "queue"))
    monkeypatch.setattr(inst_router._execution_submitter, "_queue", q)
    monkeypatch.setattr(inst_router, "_execution_queue_instance", q)
    monkeypatch.setattr(inst_router, "_execution_queue_failed", False)
    return q


@pytest.fixture
def event_repo(worker_env, monkeypatch) -> LocalFileEventRepository:
    """独立临时目录事件仓储（可见窗事件落账 + 读穿透注入）。"""
    repo = LocalFileEventRepository()
    monkeypatch.setattr(read_through, "_event_repository_instance", repo)
    monkeypatch.setattr(read_through, "_event_repository_failed", False)
    return repo


def _request(tenant_id=None):
    return SimpleNamespace(state=SimpleNamespace(tenant_id=tenant_id))


async def _start(tenant_id=None):
    body = inst_router.StartInstanceBody(
        definition_id="m5_visible_flow",
        yaml_text=_YAML,
        inputs={"items": [{"id": 1}, {"id": 2}]},
    )
    return await inst_router.start_instance(body, _request(tenant_id))


class TestWorkerSubmitVisibility:

    async def test_submit_does_not_register_and_enqueues_job(
        self, worker_env, queue, event_repo
    ):
        """worker 模式提交：注册表不注册 + 作业入队 + INSTANCE_QUEUED 落账。"""
        result = await _start()
        instance_id = result["instance_id"]

        assert result["status"] == "running"
        assert instance_id not in inst_router._instance_registry._instances, (
            "worker 模式 API 注册表保持空（design D2 陈旧注册表消除）"
        )

        job = await queue.find_active_by_instance(instance_id)
        assert job is not None and job.status == "pending"
        assert job.yaml_text == _YAML, "M2：yaml_text 随作业携带"
        assert job.idempotency_key == instance_id
        assert job.definition_id == "m5_visible_flow"

        events = await event_repo.list_for_instance(instance_id)
        assert [e.event_type for e in events] == [EventType.INSTANCE_QUEUED], (
            "提交即落 INSTANCE_QUEUED 可见窗事件（design D5）"
        )
        assert events[0].payload["definition_id"] == "m5_visible_flow"

    async def test_visible_window_get_derives_pending(
        self, worker_env, queue, event_repo
    ):
        """窗口期（提交→认领）GET 推导 pending（不再 404）。"""
        result = await _start()
        snapshot = await inst_router.get_instance(result["instance_id"])

        assert snapshot["status"] == "pending"
        assert snapshot["definition_id"] == "m5_visible_flow"
        assert "finished_at" not in snapshot

    async def test_visible_window_events_endpoint(
        self, worker_env, queue, event_repo
    ):
        """窗口期 /events 读穿透：仓储事件归一化形态。"""
        result = await _start()
        events = await inst_router.get_instance_events(result["instance_id"])

        assert events, "窗口期事件可见"
        assert events[0]["event_type"] == "INSTANCE_QUEUED"
        assert events[0]["payload"]["definition_id"] == "m5_visible_flow"

    async def test_visible_window_results_pending_outputs_none(
        self, worker_env, queue, event_repo
    ):
        """窗口期 /results：pending 非终态 → outputs=None（轮询友好）。"""
        result = await _start()
        results = await inst_router.get_instance_results(result["instance_id"])

        assert results["status"] == "pending"
        assert results["outputs"] is None

    async def test_visible_window_stream_precheck_builds_stream(
        self, worker_env, queue, event_repo
    ):
        """窗口期 SSE 预检建流（事件仓储有事件 → 轮询流建立）。"""
        from fastapi.responses import StreamingResponse

        result = await _start()
        response = await inst_router.stream_instance_events(
            result["instance_id"]
        )
        assert isinstance(response, StreamingResponse)

    async def test_visible_window_cancel_pending_job(
        self, worker_env, queue, event_repo
    ):
        """窗口期取消 pending 作业：标志 + 端点侧 INSTANCE_CANCELLED（M3 回归）。"""
        result = await _start()
        instance_id = result["instance_id"]

        cancelled = await inst_router.cancel_instance(instance_id)
        assert cancelled["status"] == "cancelled"

        job = await queue.find_active_by_instance(instance_id)
        assert job is not None and job.cancel_requested is True

        types = [
            e.event_type
            for e in await event_repo.list_for_instance(instance_id)
        ]
        assert EventType.INSTANCE_CANCELLED in types, "盲区取消事件落账"
