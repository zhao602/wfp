# =============================================================================
# 实例读穿透单测（tests/unit/test_instance_read_through.py）
#
# 覆盖（roadmap-v3 中期窗口 M2，plan 状态读穿透）：
#   A. InstanceEventReader 双实现——Registry（in_process 语义等价）/
#      Repository（事件仓储轮询 + 注册表形态归一）；
#   B. 解析器——注册表优先 / 回退事件仓储 / 均未命中 404（与注册表同文案）；
#   C. 快照推导——事件流 → GET /instances/{id} 形态快照（状态/起止时间/
#      definition_id；不依赖 payload 的 event_type 推导）；
#   D. 路由接线——get_instance / event_generator / stream 预检读穿透分支。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_instance_read_through.py -q
# =============================================================================

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import orchestration_platform.api.instance_read_through as read_through
from orchestration_platform.api.instance_read_through import (
    RegistryEventReader,
    RepositoryEventReader,
    read_instance_snapshot,
    resolve_event_reader,
)
from orchestration_platform.api.routers import execution_instance_router as inst_router
from orchestration_platform.application.execution_runner import InstanceRegistry
from orchestration_platform.domain.models.enums import EventType
from orchestration_platform.domain.models.execution_event import ExecutionEvent

_NOW = datetime(2026, 11, 23, 0, 0, 0, tzinfo=timezone.utc)
_NOW_ISO = "2026-11-23T00:00:00+00:00"


# =============================================================================
# 测试基建
# =============================================================================

def _evt(instance_id, event_type, sequence=1, node_id=None, payload=None):
    return ExecutionEvent(
        event_id=f"evt-rt-{instance_id}-{sequence}",
        instance_id=instance_id,
        event_type=event_type,
        node_id=node_id,
        sequence=sequence,
        payload=payload if payload is not None else {},
        occurred_at=_NOW,
    )


class _FakeEventRepository:
    """桩事件仓储：内存列表按 instance_id 过滤（读穿透观测点）。"""

    def __init__(self, events=None):
        self._events = list(events or [])

    async def append(self, event):
        self._events.append(event)
        return len(self._events)

    async def list_for_instance(self, instance_id, include_voided=True):
        return [
            e for e in self._events if e.instance_id == instance_id
        ]


@pytest.fixture
def reset_repo_state(monkeypatch):
    """读穿透模块仓储单例状态复位（用例隔离，防止真实仓储构造副作用）。"""
    monkeypatch.setattr(read_through, "_event_repository_instance", None)
    monkeypatch.setattr(read_through, "_event_repository_failed", False)


@pytest.fixture
def registry():
    reg = InstanceRegistry()
    yield reg
    for instance_id in list(reg._instances.keys()):
        reg.remove(instance_id)


def _register(registry, instance_id, status="running", definition_id="def-rt"):
    rt = inst_router.InstanceRuntime(
        instance_id=instance_id, definition_id=definition_id
    )
    rt.status = status
    registry.register(rt)
    return rt


# =============================================================================
# A. 双实现读语义
# =============================================================================

class TestReaders:

    async def test_registry_reader_reads_events_and_terminal(
        self, registry
    ):
        _register(registry, "inst-rt-a", status="cancelled")
        registry.append_event(
            "inst-rt-a",
            {
                "event_id": "evt-a-1",
                "instance_id": "inst-rt-a",
                "event_type": "INSTANCE_CANCELLED",
                "node_id": None,
                "timestamp": _NOW_ISO,
                "payload": {},
            },
        )
        reader = RegistryEventReader(registry)
        events = await reader.read_events("inst-rt-a")
        assert len(events) == 1
        assert events[0]["event_type"] == "INSTANCE_CANCELLED"
        assert await reader.is_terminal("inst-rt-a") is True

    async def test_registry_reader_running_not_terminal(self, registry):
        _register(registry, "inst-rt-b", status="running")
        reader = RegistryEventReader(registry)
        assert await reader.is_terminal("inst-rt-b") is False

    async def test_repository_reader_normalizes_to_registry_shape(self):
        repo = _FakeEventRepository([
            _evt("inst-rt-c", EventType.NODE_SUCCEEDED, sequence=2,
                 node_id="read_dataset", payload={"stub": True}),
        ])
        reader = RepositoryEventReader(repo)
        events = await reader.read_events("inst-rt-c")
        assert len(events) == 1
        event = events[0]
        assert event["event_type"] == "NODE_COMPLETED", (
            "NODE_SUCCEEDED 持久化载体反映射回注册表口径"
        )
        assert event["node_id"] == "read_dataset"
        assert event["timestamp"] == _NOW_ISO
        assert event["payload"] == {"stub": True}

    async def test_repository_reader_terminal_on_completed(self):
        repo = _FakeEventRepository([
            _evt("inst-rt-d", EventType.INSTANCE_STARTED, sequence=1),
            _evt("inst-rt-d", EventType.INSTANCE_COMPLETED, sequence=2),
        ])
        reader = RepositoryEventReader(repo)
        assert await reader.is_terminal("inst-rt-d") is True

    async def test_repository_reader_suspended_not_terminal(self):
        repo = _FakeEventRepository([
            _evt("inst-rt-e", EventType.INSTANCE_STARTED, sequence=1),
            _evt("inst-rt-e", EventType.INSTANCE_SUSPENDED, sequence=2),
        ])
        reader = RepositoryEventReader(repo)
        assert await reader.is_terminal("inst-rt-e") is False, (
            "waiting_review 非收尾终态（流保持开启等待恢复）"
        )

    async def test_repository_reader_node_events_not_terminal(self):
        repo = _FakeEventRepository([
            _evt("inst-rt-f", EventType.INSTANCE_STARTED, sequence=1),
            _evt("inst-rt-f", EventType.NODE_FAILED, sequence=2,
                 node_id="analyze_data"),
        ])
        reader = RepositoryEventReader(repo)
        assert await reader.is_terminal("inst-rt-f") is False


# =============================================================================
# B. 解析器：注册表优先 → 事件仓储 → 404
# =============================================================================

class TestResolve:

    async def test_registry_takes_priority(
        self, registry, reset_repo_state
    ):
        _register(registry, "inst-rt-g")
        reader = await resolve_event_reader(
            "inst-rt-g", registry=registry
        )
        assert isinstance(reader, RegistryEventReader)

    async def test_fallback_to_repository_when_events_exist(
        self, registry, reset_repo_state, monkeypatch
    ):
        repo = _FakeEventRepository([
            _evt("inst-rt-h", EventType.INSTANCE_STARTED, sequence=1),
        ])
        monkeypatch.setattr(
            read_through, "_event_repository_instance", repo
        )
        reader = await resolve_event_reader(
            "inst-rt-h", registry=registry
        )
        assert isinstance(reader, RepositoryEventReader)

    async def test_404_when_neither_registry_nor_events(
        self, registry, reset_repo_state, monkeypatch
    ):
        monkeypatch.setattr(read_through, "_event_repository_failed", True)
        with pytest.raises(HTTPException) as exc_info:
            await resolve_event_reader("inst-rt-missing", registry=registry)
        assert exc_info.value.status_code == 404
        assert "不存在" in exc_info.value.detail, "与注册表 404 同文案"

    async def test_404_when_repository_empty(
        self, registry, reset_repo_state, monkeypatch
    ):
        monkeypatch.setattr(
            read_through, "_event_repository_instance", _FakeEventRepository()
        )
        with pytest.raises(HTTPException) as exc_info:
            await resolve_event_reader("inst-rt-missing", registry=registry)
        assert exc_info.value.status_code == 404


# =============================================================================
# C. 快照推导
# =============================================================================

class TestSnapshot:

    async def test_running_snapshot_from_started_event(
        self, reset_repo_state, monkeypatch
    ):
        repo = _FakeEventRepository([
            _evt(
                "inst-rt-i",
                EventType.INSTANCE_STARTED,
                sequence=1,
                payload={"definition_id": "def-i"},
            ),
        ])
        snapshot = await read_instance_snapshot(
            "inst-rt-i", event_repository=repo
        )
        assert snapshot == {
            "instance_id": "inst-rt-i",
            "definition_id": "def-i",
            "status": "running",
            "started_at": _NOW_ISO,
        }

    async def test_succeeded_snapshot_with_finished_at(
        self, reset_repo_state
    ):
        repo = _FakeEventRepository([
            _evt("inst-rt-j", EventType.INSTANCE_STARTED, sequence=1),
            _evt("inst-rt-j", EventType.NODE_SUCCEEDED, sequence=2,
                 node_id="read_dataset"),
            _evt("inst-rt-j", EventType.INSTANCE_COMPLETED, sequence=3),
        ])
        snapshot = await read_instance_snapshot(
            "inst-rt-j", event_repository=repo
        )
        assert snapshot["status"] == "succeeded"
        assert snapshot["finished_at"] == _NOW_ISO

    async def test_suspended_snapshot(self, reset_repo_state):
        repo = _FakeEventRepository([
            _evt("inst-rt-k", EventType.INSTANCE_STARTED, sequence=1),
            _evt("inst-rt-k", EventType.INSTANCE_SUSPENDED, sequence=2),
        ])
        snapshot = await read_instance_snapshot(
            "inst-rt-k", event_repository=repo
        )
        assert snapshot["status"] == "waiting_review"
        assert "finished_at" not in snapshot

    async def test_failed_snapshot(self, reset_repo_state):
        repo = _FakeEventRepository([
            _evt("inst-rt-l", EventType.INSTANCE_STARTED, sequence=1),
            _evt("inst-rt-l", EventType.INSTANCE_FAILED, sequence=2),
        ])
        snapshot = await read_instance_snapshot(
            "inst-rt-l", event_repository=repo
        )
        assert snapshot["status"] == "failed"

    async def test_none_when_no_events(self, reset_repo_state):
        repo = _FakeEventRepository()
        snapshot = await read_instance_snapshot(
            "inst-rt-none", event_repository=repo
        )
        assert snapshot is None

    async def test_none_when_no_repository(self, reset_repo_state, monkeypatch):
        monkeypatch.setattr(read_through, "_event_repository_failed", True)
        snapshot = await read_instance_snapshot("inst-rt-none")
        assert snapshot is None


# =============================================================================
# D. 路由接线：get_instance / event_generator 读穿透分支
# =============================================================================

@pytest.fixture
def router_registry(monkeypatch, registry):
    """router 命名空间注册表替换（调用期求值，monkeypatch 语义验证点）。"""
    monkeypatch.setattr(inst_router, "_instance_registry", registry)
    return registry


class TestRouterReadThrough:

    async def test_get_instance_registry_path_unchanged(
        self, router_registry, reset_repo_state
    ):
        _register(router_registry, "inst-rt-reg", status="running")
        result = await inst_router.get_instance("inst-rt-reg")
        assert result["status"] == "running"
        assert result["definition_id"] == "def-rt"

    async def test_get_instance_read_through_from_repository(
        self, router_registry, reset_repo_state, monkeypatch
    ):
        repo = _FakeEventRepository([
            _evt(
                "inst-rt-worker",
                EventType.INSTANCE_STARTED,
                sequence=1,
                payload={"definition_id": "def-worker"},
            ),
            _evt("inst-rt-worker", EventType.INSTANCE_COMPLETED,
                 sequence=2),
        ])
        monkeypatch.setattr(
            read_through, "_event_repository_instance", repo
        )
        result = await inst_router.get_instance("inst-rt-worker")
        assert result["instance_id"] == "inst-rt-worker"
        assert result["definition_id"] == "def-worker"
        assert result["status"] == "succeeded"
        assert result["finished_at"] == _NOW_ISO

    async def test_get_instance_404_when_unknown(
        self, router_registry, reset_repo_state, monkeypatch
    ):
        monkeypatch.setattr(
            read_through, "_event_repository_instance", _FakeEventRepository()
        )
        with pytest.raises(HTTPException) as exc_info:
            await inst_router.get_instance("inst-rt-unknown")
        assert exc_info.value.status_code == 404

    async def test_event_generator_repository_fallback_stream_end(
        self, router_registry, reset_repo_state, monkeypatch
    ):
        """worker 模式 SSE：注册表未命中 → 事件仓储轮询 → 终态 STREAM_END。"""
        repo = _FakeEventRepository([
            _evt("inst-rt-sse", EventType.INSTANCE_STARTED, sequence=1),
            _evt("inst-rt-sse", EventType.NODE_SUCCEEDED, sequence=2,
                 node_id="read_dataset"),
            _evt("inst-rt-sse", EventType.INSTANCE_COMPLETED, sequence=3),
        ])
        monkeypatch.setattr(
            read_through, "_event_repository_instance", repo
        )
        chunks: list = []
        async for chunk in inst_router.event_generator("inst-rt-sse"):
            chunks.append(chunk)
        assert len(chunks) == 4, "3 事件帧 + STREAM_END 收尾帧"
        assert "INSTANCE_STARTED" in chunks[0]
        assert "NODE_COMPLETED" in chunks[1], "反映射回注册表事件口径"
        assert "INSTANCE_COMPLETED" in chunks[2]
        assert "STREAM_END" in chunks[3]

    async def test_stream_precheck_resolves_worker_instance(
        self, router_registry, reset_repo_state, monkeypatch
    ):
        """stream 端点预检：注册表未命中但事件仓储有事件 → 不 404。"""
        repo = _FakeEventRepository([
            _evt("inst-rt-stream", EventType.INSTANCE_STARTED, sequence=1),
        ])
        monkeypatch.setattr(
            read_through, "_event_repository_instance", repo
        )
        response = await inst_router.stream_instance_events("inst-rt-stream")
        assert response.media_type == "text/event-stream"

    async def test_stream_precheck_404_unknown(
        self, router_registry, reset_repo_state, monkeypatch
    ):
        monkeypatch.setattr(
            read_through, "_event_repository_instance", _FakeEventRepository()
        )
        with pytest.raises(HTTPException) as exc_info:
            await inst_router.stream_instance_events("inst-rt-unknown")
        assert exc_info.value.status_code == 404
