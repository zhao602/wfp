# =============================================================================
# 实例取消与取结果端点测试（tests/unit/test_instance_cancel_results.py）
#
# 职责：验证 B2 嵌入 SDK 后端两新端点契约（docs/sdk-contract.md §1-7/§1-8）：
#       cancel 四分支（404 / 409 终态 / 409 无任务 / 200 运行中→cancelled 落定）、
#       results 四分支（404 / 非终态 null / succeeded outputs / failed 空 outputs）、
#       SSE event_generator 对 cancelled 终态的收尾（STREAM_END）。
# 设计意图：
#   1. 同环 ASGI 请求（httpx.ASGITransport）：异常处理器在应用栈内正常生效
#      （409 INSTANCE_CANCEL_CONFLICT 同构错误体 {code, message, details} 可断言）；
#   2. 运行中取消 = 真实 run_and_record 协程 + _SlowScheduler 桩（execute 内
#      30s 睡眠，保证取消窗口），验证 CancelledError 分支终态落定全链路；
#   3. 注册表为路由模块级单例：用例统一 inst-b2-* 前缀 + 夹具清理（不跨用例泄漏）；
#   4. SSE 不经 ASGITransport（httpx ASGI 传输对无限流整包缓冲——实测会挂起，
#      house 先例 tests/integration/test_event_export_api.py）：直接迭代生成器。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_instance_cancel_results.py -q
# =============================================================================

import asyncio
import contextlib

import httpx
import pytest

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.api.routers import execution_instance_router as inst_router
from orchestration_platform.configuration.settings import get_settings

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
    """共享内存注册表 + 用例级清理（inst-b2-* 前缀实例不跨用例泄漏）。"""
    reg = inst_router._instance_registry
    yield reg
    for instance_id in list(reg._instances.keys()):
        if instance_id.startswith("inst-b2-"):
            reg.remove(instance_id)


async def _request(app, method: str, path: str, json_body=None) -> httpx.Response:
    """同环 ASGI 请求（非流式端点；异常处理器在应用栈内正常生效）。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=json_body)


# =============================================================================
# 构造桩
# =============================================================================


class _SlowScheduler:
    """执行期长睡眠调度器桩：保证取消窗口内 execute 不自然完成。"""

    async def execute(
        self, compiled, initial_state, instance_id, trace_id, resume_checkpoint=None,
        tenant_id=None,  # 2026-11-22 B7 W2a：run_and_record 新增租户穿参实参
    ):
        await asyncio.sleep(30)
        return initial_state


class _FinalStateStub:
    """终态共享状态桩：results 端点经 getattr(node_outputs) 读取。"""

    def __init__(self, node_outputs: dict) -> None:
        self.node_outputs = node_outputs


def _register(
    registry, instance_id: str, status: str = "running", task=None, final_state=None
) -> inst_router.InstanceRuntime:
    """注册带预设状态的实例运行时（不经过 start_instance 全链路）。"""
    rt = inst_router.InstanceRuntime(
        instance_id=instance_id, definition_id="wf-b2-test"
    )
    rt.status = status
    rt.task = task
    rt.final_state = final_state
    registry.register(rt)
    return rt


# =============================================================================
# A. cancel 端点（契约四分支）
# =============================================================================


class TestCancelEndpoint:
    async def test_cancel_unknown_instance_404(self, app):
        response = await _request(app, "POST", f"{BASE}/inst-b2-missing/cancel")
        assert response.status_code == 404
        assert "inst-b2-missing" in str(response.json().get("detail", ""))

    async def test_cancel_terminal_instance_409_conflict_code(self, app, registry):
        _register(registry, "inst-b2-done", status="succeeded")
        response = await _request(app, "POST", f"{BASE}/inst-b2-done/cancel")
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "INSTANCE_CANCEL_CONFLICT", "同构错误体顶层 code"
        assert "details" in body
        assert "inst-b2-done" in body["message"]

    async def test_cancel_pending_without_task_409(self, app, registry):
        _register(registry, "inst-b2-pending", status="pending", task=None)
        response = await _request(app, "POST", f"{BASE}/inst-b2-pending/cancel")
        assert response.status_code == 409
        assert response.json()["code"] == "INSTANCE_CANCEL_CONFLICT"

    async def test_cancel_running_instance_200_then_cancelled(self, app, registry):
        """取消全链路：路由 200 快照（取消已投递、终态异步落定）→
        run_and_record CancelledError 分支置 cancelled 终态 +
        INSTANCE_CANCELLED 事件 + finished_at + results 端点空 outputs 回读。"""
        rt = _register(registry, "inst-b2-run")
        task = asyncio.create_task(
            inst_router.run_and_record(
                _SlowScheduler(),
                None,
                {"node_outputs": {}},
                "inst-b2-run",
                "trace-b2",
                registry,
            )
        )
        rt.task = task
        await asyncio.sleep(0.05)  # 让协程进入 execute()

        try:
            response = await _request(app, "POST", f"{BASE}/inst-b2-run/cancel")
            assert response.status_code == 200
            snapshot = response.json()
            assert snapshot["instance_id"] == "inst-b2-run"
            assert snapshot["definition_id"] == "wf-b2-test"
            assert snapshot["status"] == "running", "返回时刻取消尚未落定（快照语义）"

            with pytest.raises(asyncio.CancelledError):
                await task
            rt_after = registry.get("inst-b2-run")
            assert rt_after.status == "cancelled"
            assert rt_after.finished_at is not None
            event_types = [e["event_type"] for e in rt_after.events]
            assert "INSTANCE_CANCELLED" in event_types
            assert "INSTANCE_STARTED" in event_types, "取消前执行链正常启动"

            results = await _request(app, "GET", f"{BASE}/inst-b2-run/results")
            assert results.status_code == 200
            body = results.json()
            assert body["status"] == "cancelled"
            assert body["outputs"] == {}
            assert body["finished_at"] == rt_after.finished_at
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


# =============================================================================
# B. results 端点（契约四分支）
# =============================================================================


class TestResultsEndpoint:
    async def test_results_unknown_instance_404(self, app):
        response = await _request(app, "GET", f"{BASE}/inst-b2-missing/results")
        assert response.status_code == 404

    async def test_results_running_returns_null_outputs(self, app, registry):
        _register(registry, "inst-b2-live", status="running")
        response = await _request(app, "GET", f"{BASE}/inst-b2-live/results")
        assert response.status_code == 200
        body = response.json()
        assert body["instance_id"] == "inst-b2-live"
        assert body["status"] == "running"
        assert body["outputs"] is None, "非终态轮询友好（契约 §1-8）"
        assert "finished_at" not in body

    async def test_results_succeeded_returns_node_outputs(self, app, registry):
        rt = _register(
            registry,
            "inst-b2-ok",
            status="succeeded",
            final_state=_FinalStateStub({"node_a": {"answer": 42}}),
        )
        rt.finished_at = "2026-11-21T00:00:00+00:00"
        response = await _request(app, "GET", f"{BASE}/inst-b2-ok/results")
        assert response.status_code == 200
        body = response.json()
        assert body["outputs"] == {"node_a": {"answer": 42}}
        assert body["status"] == "succeeded"
        assert body["finished_at"] == "2026-11-21T00:00:00+00:00"

    async def test_results_failed_returns_empty_outputs(self, app, registry):
        _register(registry, "inst-b2-fail", status="failed", final_state=None)
        response = await _request(app, "GET", f"{BASE}/inst-b2-fail/results")
        assert response.status_code == 200
        assert response.json()["outputs"] == {}, "非 succeeded 终态 outputs 恒空"


# =============================================================================
# C. SSE 收尾：cancelled 终态使 event_generator 正常结束（STREAM_END）
# =============================================================================


class TestSseTerminalOnCancelled:
    async def test_event_generator_ends_on_cancelled(self, registry):
        _register(registry, "inst-b2-sse", status="cancelled")
        registry.append_event(
            "inst-b2-sse",
            {
                "event_id": "evt-b2-1",
                "instance_id": "inst-b2-sse",
                "event_type": "INSTANCE_CANCELLED",
                "node_id": None,
                "timestamp": "2026-11-21T00:00:00+00:00",
                "payload": {},
            },
        )
        chunks: list = []
        async for chunk in inst_router.event_generator("inst-b2-sse"):
            chunks.append(chunk)
        assert chunks, "cancelled 终态下生成器须产出事件帧后收尾"
        assert "INSTANCE_CANCELLED" in chunks[0]
        assert any("STREAM_END" in c for c in chunks), "终态收尾帧（B2 补丁点）"
