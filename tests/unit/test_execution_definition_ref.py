# =============================================================================
# 执行入口双轨与版本追溯测试
# （tests/unit/test_execution_definition_ref.py）
#
# 职责：B11 定义版本与发布态——执行入口双轨（design D9）与版本追溯
#       三通道（InstanceRuntime/QUEUED 载荷/读穿透快照）验收：
#   1. yaml_text 轨回归（前端画布路径零变化）+ GET 版本随行；
#   2. 引用轨：缺省解析最新已发布版本 / 显式 definition_version 精确取
#      （不校验发布态）/ 无已发布版本 422 / 显式版本不存在 404；
#   3. 双轨入口守卫（既无 yaml_text 又无 definition_id → 422）；
#   4. 版本追溯通道：InstanceRuntime 构造契约（新参数 + 既有构造零破坏）
#      + read_instance_snapshot 从 QUEUED/STARTED 载荷提取 definition_version
#      （worker 读穿透路径；缺省 None 键恒存在）。
# 设计意图：
#   1. 同环 ASGI 请求（httpx.ASGITransport——test_cancel_endpoint_worker.py
#      先例）；本地降级 profile + 独立 LOCAL_STORAGE_ROOT 临时目录；
#   2. 端到端最小可执行定义 = 单触发器节点夹具（真实解析/编译/调度链，
#      无 LLM/工具/数据源依赖）；实例收尾轮询至终态（后台任务不悬挂）；
#   3. 既有 yaml_text 主路径行为由全仓既有测试回归（本文件仅锚定版本
#      随行增量），不重复覆盖。
# =============================================================================

import asyncio
from pathlib import Path
from typing import List, Optional

import httpx
import pytest

from orchestration_platform.api import instance_read_through
from orchestration_platform.api.application_factory import create_app
from orchestration_platform.api.routers import execution_instance_router as inst_router
from orchestration_platform.application.execution_runner import InstanceRuntime
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import EventType
from orchestration_platform.domain.models.execution_event import ExecutionEvent

WF_BASE = "/api/v1/workflows"
INST_BASE = "/api/v1/instances"

_TRIGGER_YAML = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "valid_workflow_definitions"
    / "valid_single_trigger_node.yaml"
).read_text(encoding="utf-8")


# =============================================================================
# 夹具与助手
# =============================================================================


@pytest.fixture
def app(tmp_path, monkeypatch):
    """本地降级 profile 应用 + 独立存储根目录（引用轨仓储隔离）。"""
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("STORAGE_BACKEND", "local_file")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def registry_cleanup():
    """用例级注册表清理（本文件产生的实例不跨用例泄漏）。"""
    reg = inst_router._instance_registry
    before = set(reg._instances.keys())
    yield reg
    for instance_id in set(reg._instances.keys()) - before:
        reg.remove(instance_id)


async def _request(app, method: str, path: str, json_body=None) -> httpx.Response:
    """同环 ASGI 请求（非流式端点）。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=json_body)


async def _start(app, body: dict) -> httpx.Response:
    """启动实例（POST /instances）。"""
    return await _request(app, "POST", INST_BASE, body)


async def _get_until_terminal(
    app, instance_id: str, timeout_seconds: float = 5.0
) -> dict:
    """轮询实例直至终态（后台任务收尾，不悬挂用例）。"""
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    snapshot: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await _request(app, "GET", f"{INST_BASE}/{instance_id}")
        assert resp.status_code == 200
        snapshot = resp.json()
        if snapshot.get("status") in ("succeeded", "failed", "cancelled"):
            return snapshot
        await asyncio.sleep(0.05)
    return snapshot


class _FakeEventRepo:
    """事件仓储替身：固定事件列表（read_instance_snapshot 直测注入）。"""

    def __init__(self, events: List[ExecutionEvent]) -> None:
        self._events = events

    async def list_for_instance(self, instance_id: str) -> List[ExecutionEvent]:
        return list(self._events)


def _event(
    instance_id: str,
    event_type: EventType,
    payload: Optional[dict] = None,
) -> ExecutionEvent:
    """构造持久化形态执行事件。"""
    return ExecutionEvent(
        event_id=f"evt-{instance_id}-{event_type.value}",
        instance_id=instance_id,
        event_type=event_type,
        node_id=None,
        sequence=0,
        payload=payload or {},
    )


# =============================================================================
# 执行入口双轨
# =============================================================================


class TestStartInstanceDualTrack:
    async def test_yaml_text_track_regression_carries_version(self, app, registry_cleanup):
        """yaml_text 轨回归：启动成功 + GET 注册表路径随行 definition_version。"""
        resp = await _start(
            app,
            {"definition_id": "single_trigger_node", "yaml_text": _TRIGGER_YAML},
        )
        assert resp.status_code == 200
        instance_id = resp.json()["instance_id"]
        snapshot = await _get_until_terminal(app, instance_id)
        assert snapshot["definition_id"] == "single_trigger_node"
        assert snapshot["definition_version"] == 1  # YAML definition_version 随行

    async def test_reference_track_defaults_to_published_version(
        self, app, registry_cleanup
    ):
        """引用轨缺省：解析最新已发布版本（draft 不可上线）。"""
        saved = await _request(
            app, "POST", WF_BASE, {"yaml_text": _TRIGGER_YAML}
        )
        assert saved.json()["status"] == "draft"
        published = await _request(
            app, "POST", f"{WF_BASE}/single_trigger_node/publish", {}
        )
        assert published.status_code == 200

        resp = await _start(app, {"definition_id": "single_trigger_node"})
        assert resp.status_code == 200
        instance_id = resp.json()["instance_id"]
        snapshot = await _get_until_terminal(app, instance_id)
        assert snapshot["definition_version"] == 1  # published 版本

    async def test_reference_track_explicit_version_takes_exact_version(
        self, app, registry_cleanup
    ):
        """引用轨显式版本：精确取该版本（不校验发布态——显式即意图）。"""
        await _request(app, "POST", WF_BASE, {"yaml_text": _TRIGGER_YAML})  # v1
        await _request(app, "POST", WF_BASE, {"yaml_text": _TRIGGER_YAML})  # v2 draft
        await _request(
            app, "POST", f"{WF_BASE}/single_trigger_node/publish", {"version": 2}
        )

        resp = await _start(
            app,
            {"definition_id": "single_trigger_node", "definition_version": 1},
        )
        assert resp.status_code == 200
        instance_id = resp.json()["instance_id"]
        snapshot = await _get_until_terminal(app, instance_id)
        assert snapshot["definition_version"] == 1  # 显式 draft 版本精确取

    async def test_reference_track_without_published_returns_422(
        self, app, registry_cleanup
    ):
        """引用轨缺省但仅有 draft：422 尚无已发布版本。"""
        await _request(app, "POST", WF_BASE, {"yaml_text": _TRIGGER_YAML})
        resp = await _start(app, {"definition_id": "single_trigger_node"})
        assert resp.status_code == 422
        assert "尚无已发布版本" in resp.json()["detail"]

    async def test_reference_track_unknown_definition_with_version_returns_404(
        self, app, registry_cleanup
    ):
        """引用轨显式版本但定义不存在：404（与解析失败 422 严格区分）。"""
        resp = await _start(
            app, {"definition_id": "wf-b11-none", "definition_version": 3}
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "WF_NOT_FOUND"

    async def test_missing_both_tracks_returns_422(self, app, registry_cleanup):
        """双轨入口守卫：既无 yaml_text 又无 definition_id → 422 双轨提示。"""
        resp = await _start(app, {"definition_id": ""})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "yaml_text" in detail and "definition_id" in detail


# =============================================================================
# 版本追溯通道
# =============================================================================


class TestInstanceRuntimeVersionChannel:
    def test_constructor_carries_definition_version(self):
        """构造契约：新参数随行；既有构造（无版本）零破坏（None）。"""
        rt = InstanceRuntime(
            instance_id="inst-b11-rt",
            definition_id="wf-b11-rt",
            definition_version=3,
        )
        assert rt.definition_version == 3
        legacy = InstanceRuntime(instance_id="inst-b11-legacy", definition_id="wf-b11")
        assert legacy.definition_version is None


class TestSnapshotVersionExtraction:
    async def test_snapshot_extracts_version_from_queued_payload(self):
        """worker 读穿透：QUEUED 载荷提取版本（提交可见窗口径）。"""
        repo = _FakeEventRepo(
            [
                _event(
                    "inst-b11-q",
                    EventType.INSTANCE_QUEUED,
                    {"definition_id": "wf-b11-q", "definition_version": 5},
                )
            ]
        )
        snapshot = await instance_read_through.read_instance_snapshot(
            "inst-b11-q", event_repository=repo
        )
        assert snapshot is not None
        assert snapshot["status"] == "pending"
        assert snapshot["definition_id"] == "wf-b11-q"
        assert snapshot["definition_version"] == 5

    async def test_snapshot_extracts_version_from_started_payload(self):
        """worker 读穿透：QUEUED 缺版本时 STARTED 载荷兜底提取。"""
        repo = _FakeEventRepo(
            [
                _event(
                    "inst-b11-s",
                    EventType.INSTANCE_QUEUED,
                    {"definition_id": "wf-b11-s"},  # 旧格式无版本
                ),
                _event(
                    "inst-b11-s",
                    EventType.INSTANCE_STARTED,
                    {"definition_id": "wf-b11-s", "definition_version": 7},
                ),
            ]
        )
        snapshot = await instance_read_through.read_instance_snapshot(
            "inst-b11-s", event_repository=repo
        )
        assert snapshot is not None
        assert snapshot["status"] == "running"
        assert snapshot["definition_version"] == 7

    async def test_snapshot_version_key_conditional_when_unknown(self):
        """快照契约：definition_version 非 None 才随行（未知 = 键缺席，
        与注册表路径条件键同构——既有快照精确等价契约零破坏）。"""
        repo = _FakeEventRepo(
            [
                _event(
                    "inst-b11-n",
                    EventType.INSTANCE_QUEUED,
                    {"definition_id": "wf-b11-n"},
                )
            ]
        )
        snapshot = await instance_read_through.read_instance_snapshot(
            "inst-b11-n", event_repository=repo
        )
        assert snapshot is not None
        assert "definition_version" not in snapshot
