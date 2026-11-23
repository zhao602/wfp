# =============================================================================
# M3 cancel 端点 worker 模式扩展测试
# （tests/unit/test_cancel_endpoint_worker.py）
#
# 职责：验证 P5 实例级取消 worker 读穿透三态 + 盲区事件（design D5）：
#       pending 作业盲区取消（标志 + 端点侧 INSTANCE_CANCELLED 事件 +
#       GET/SSE 立即可见 cancelled）；running 作业仅置标志（终态事件由
#       worker 落）；无活跃作业三态（事件不可见 404 / 可见非可取消态
#       409 INSTANCE_CANCEL_CONFLICT）。
# 设计意图：
#   1. 同环 ASGI 请求（httpx.ASGITransport）——409 同构错误体可断言
#      （tests/unit/test_instance_cancel_results.py 先例）；
#   2. 队列/事件仓储经 monkeypatch 模块属性注入独立临时目录（不依赖
#      storage_backend 环境与全局 .data 落盘）；
#   3. in_process 注册表路径零变化由 test_instance_cancel_results.py
#      既有用例回归（design D6 红线，不重复覆盖）。
# =============================================================================

import uuid

import httpx
import pytest

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.api import instance_read_through as read_through
from orchestration_platform.api.instance_read_through import (
    RepositoryEventReader,
    resolve_event_reader,
)
from orchestration_platform.api.routers import execution_instance_router as inst_router
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import EventType
from orchestration_platform.domain.models.execution_event import ExecutionEvent
from orchestration_platform.domain.models.execution_job import ExecutionJob
from orchestration_platform.persistence.local_file.local_file_event_repository import (
    LocalFileEventRepository,
)
from orchestration_platform.persistence.local_file.local_file_execution_queue import (
    LocalFileExecutionQueue,
)

BASE = "/api/v1/instances"


# =============================================================================
# 夹具
# =============================================================================


@pytest.fixture
def app(monkeypatch):
    """本地降级 profile 应用实例（RBAC/租户隔离关闭，路由全放行）。"""
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def registry():
    """共享内存注册表 + 用例级清理（inst-m3-* 前缀实例不跨用例泄漏）。"""
    reg = inst_router._instance_registry
    yield reg
    for instance_id in list(reg._instances.keys()):
        if instance_id.startswith("inst-m3-"):
            reg.remove(instance_id)


@pytest.fixture
def queue(tmp_path, monkeypatch) -> LocalFileExecutionQueue:
    """独立临时目录执行队列（cancel worker 路径注入）。"""
    q = LocalFileExecutionQueue(queue_dir=str(tmp_path / "queue"))
    monkeypatch.setattr(inst_router, "_execution_queue_instance", q)
    monkeypatch.setattr(inst_router, "_execution_queue_failed", False)
    return q


@pytest.fixture
def event_repo(tmp_path, monkeypatch) -> LocalFileEventRepository:
    """独立临时目录事件仓储（读穿透 + 盲区事件落账注入）。"""
    repo = LocalFileEventRepository(storage_root=str(tmp_path / "data"))
    monkeypatch.setattr(read_through, "_event_repository_instance", repo)
    monkeypatch.setattr(read_through, "_event_repository_failed", False)
    return repo


async def _request(app, method: str, path: str, json_body=None) -> httpx.Response:
    """同环 ASGI 请求（非流式端点）。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=json_body)


def _enqueue(queue: LocalFileExecutionQueue, instance_id: str) -> ExecutionJob:
    """构造并入队待执行作业（worker 模式实例：不在 API 注册表）。"""
    job = ExecutionJob(
        job_id=f"job-{instance_id}",
        instance_id=instance_id,
        definition_id="wf-m3-cancel",
        initial_inputs={},
        trigger_source="manual",
        trace_id="trace-m3",
        idempotency_key=instance_id,
    )
    return job


async def _append_repo_event(
    repo: LocalFileEventRepository, instance_id: str, event_type: EventType
) -> None:
    """直接向事件仓储落一条实例级事件（模拟 worker 侧历史落账）。"""
    await repo.append(
        ExecutionEvent(
            event_id=str(uuid.uuid4()),
            instance_id=instance_id,
            event_type=event_type,
            node_id=None,
            sequence=0,
            payload={"definition_id": "wf-m3-cancel"},
        )
    )


# =============================================================================
# A. pending 作业盲区取消（design D5：标志 + 端点侧事件 + 立即可见）
# =============================================================================


class TestPendingJobBlindZoneCancel:

    async def test_cancel_pending_job_sets_flag_and_returns_cancelled(
        self, app, queue, event_repo
    ):
        instance_id = "inst-m3-blind"
        await queue.enqueue(_enqueue(queue, instance_id))

        response = await _request(app, "POST", f"{BASE}/{instance_id}/cancel")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "cancelled", "盲区取消即时终态（design D5）"
        assert body["definition_id"] == "wf-m3-cancel"

        job = await queue.get(f"job-{instance_id}")
        assert job is not None and job.cancel_requested is True

        events = await event_repo.list_for_instance(instance_id)
        types = [e.event_type for e in events]
        assert EventType.INSTANCE_CANCELLED in types, "端点侧盲区取消事件落账"

    async def test_cancelled_visible_via_get_snapshot(
        self, app, queue, event_repo
    ):
        """GET 读穿透立即可见 cancelled（盲区取消的可见性验收）。"""
        instance_id = "inst-m3-blind-get"
        await queue.enqueue(_enqueue(queue, instance_id))

        cancel = await _request(app, "POST", f"{BASE}/{instance_id}/cancel")
        assert cancel.status_code == 200

        got = await _request(app, "GET", f"{BASE}/{instance_id}")
        assert got.status_code == 200
        assert got.json()["status"] == "cancelled"

    async def test_cancelled_terminates_sse_reader(self, app, queue, event_repo):
        """SSE 读穿透终态判定：盲区取消事件即收尾（STREAM_END 前置）。"""
        instance_id = "inst-m3-blind-sse"
        await queue.enqueue(_enqueue(queue, instance_id))

        cancel = await _request(app, "POST", f"{BASE}/{instance_id}/cancel")
        assert cancel.status_code == 200

        reader = await resolve_event_reader(
            instance_id, registry=inst_router._instance_registry
        )
        assert isinstance(reader, RepositoryEventReader), "事件仓储路径"
        assert await reader.is_terminal(instance_id) is True

    async def test_repeat_cancel_idempotent(self, app, queue, event_repo):
        """重复取消幂等：仍 200 cancelled，标志恒 True。"""
        instance_id = "inst-m3-blind-repeat"
        await queue.enqueue(_enqueue(queue, instance_id))

        first = await _request(app, "POST", f"{BASE}/{instance_id}/cancel")
        assert first.status_code == 200
        second = await _request(app, "POST", f"{BASE}/{instance_id}/cancel")
        assert second.status_code == 200
        assert second.json()["status"] == "cancelled"

        job = await queue.get(f"job-{instance_id}")
        assert job is not None and job.cancel_requested is True


# =============================================================================
# B. running 作业取消（design D5：仅置标志，终态事件由 worker 落）
# =============================================================================


class TestRunningJobCancel:

    async def test_cancel_running_job_sets_flag_only(
        self, app, queue, event_repo
    ):
        instance_id = "inst-m3-run"
        await queue.enqueue(_enqueue(queue, instance_id))
        claimed = await queue.claim("worker-m3")
        assert claimed is not None and claimed.status == "running"

        response = await _request(app, "POST", f"{BASE}/{instance_id}/cancel")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "running", "取消在途（心跳发现后落定）"

        job = await queue.get(f"job-{instance_id}")
        assert job is not None and job.cancel_requested is True

        events = await event_repo.list_for_instance(instance_id)
        assert events == [], "running 取消不落端点侧事件（避免与 worker 双写）"

    async def test_cancel_running_with_started_event_returns_snapshot(
        self, app, queue, event_repo
    ):
        """running 且事件可见（worker 已落 INSTANCE_STARTED）→ 事件推导快照。"""
        instance_id = "inst-m3-run-visible"
        await queue.enqueue(_enqueue(queue, instance_id))
        await queue.claim("worker-m3")
        await _append_repo_event(
            event_repo, instance_id, EventType.INSTANCE_STARTED
        )

        response = await _request(app, "POST", f"{BASE}/{instance_id}/cancel")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "running"
        assert body["definition_id"] == "wf-m3-cancel"
        assert body["started_at"] is not None


# =============================================================================
# C. 无活跃作业三态（404 / 409 镜像 in_process 语义）
# =============================================================================


class TestNoActiveJobStates:

    async def test_unknown_instance_404(self, app, queue, event_repo):
        response = await _request(app, "POST", f"{BASE}/inst-m3-none/cancel")
        assert response.status_code == 404

    async def test_suspended_visible_409_conflict(self, app, queue, event_repo):
        """waiting_review 挂起可见（事件推导）→ 409 无可取消任务。"""
        instance_id = "inst-m3-suspended"
        await _append_repo_event(
            event_repo, instance_id, EventType.INSTANCE_SUSPENDED
        )

        response = await _request(app, "POST", f"{BASE}/{instance_id}/cancel")
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "INSTANCE_CANCEL_CONFLICT"

    async def test_terminal_visible_409_conflict(self, app, queue, event_repo):
        """终态可见（事件推导 succeeded）→ 409 已终态。"""
        instance_id = "inst-m3-done"
        await _append_repo_event(
            event_repo, instance_id, EventType.INSTANCE_STARTED
        )
        await _append_repo_event(
            event_repo, instance_id, EventType.INSTANCE_COMPLETED
        )

        response = await _request(app, "POST", f"{BASE}/{instance_id}/cancel")
        assert response.status_code == 409
        assert response.json()["code"] == "INSTANCE_CANCEL_CONFLICT"

    async def test_terminal_job_without_events_404(
        self, app, queue, event_repo
    ):
        """终态作业且无事件（M2 可见窗口径外）→ 404（诚实口径维持）。"""
        instance_id = "inst-m3-terminal-quiet"
        job = _enqueue(queue, instance_id)
        await queue.enqueue(job)
        await queue.claim("worker-m3")
        await queue.complete(job.job_id, "worker-m3")

        response = await _request(app, "POST", f"{BASE}/{instance_id}/cancel")
        assert response.status_code == 404


# =============================================================================
# D. 队列不可用降级（构造失败 → 404 兜底，不 500）
# =============================================================================


class TestQueueUnavailableDegradation:

    async def test_queue_unavailable_falls_back_404(self, app, monkeypatch):
        monkeypatch.setattr(inst_router, "_execution_queue_instance", None)
        monkeypatch.setattr(inst_router, "_execution_queue_failed", True)

        response = await _request(app, "POST", f"{BASE}/inst-m3-noq/cancel")
        assert response.status_code == 404, "队列不可达按未知实例兜底（不 500）"
