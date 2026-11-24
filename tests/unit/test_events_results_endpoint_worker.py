# =============================================================================
# M5 /events 与 /results 端点 worker 读穿透单测
# （tests/unit/test_events_results_endpoint_worker.py）
#
# 覆盖（design D7，T6）：
#   A. /events worker 读穿透——未知 404；注册表未命中回退事件仓储
#      （归一化形态：NODE_SUCCEEDED→NODE_COMPLETED、payload dict）；
#      注册表命中优先（双源同时有事件时）；
#   B. /results worker 读穿透——未知 404；非终态 outputs=None（轮询友好）；
#      succeeded 输出取检查点快照 node_outputs（INV-08 唯一权威输出存储）；
#      无检查点/读失败回退 {}；failed → {}；pending 窗口 → outputs=None。
# 隔离：LOCAL_STORAGE_ROOT 重定向 + 事件仓储/检查点仓储注入。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_events_results_endpoint_worker.py -q
# =============================================================================

import uuid
from datetime import datetime, timezone

import pytest

from orchestration_platform.api import instance_read_through as read_through
from orchestration_platform.api.routers import execution_instance_router as inst_router
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.enums import EventType
from orchestration_platform.domain.models.execution_event import ExecutionEvent
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
    LocalFileCheckpointRepository,
)
from orchestration_platform.persistence.local_file.local_file_event_repository import (
    LocalFileEventRepository,
)
from fastapi import HTTPException


@pytest.fixture
def routed_env(tmp_path, monkeypatch):
    """存储根重定向 + settings 缓存刷新收口。"""
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def event_repo(routed_env, monkeypatch) -> LocalFileEventRepository:
    """独立临时目录事件仓储（读穿透注入）。"""
    repo = LocalFileEventRepository()
    monkeypatch.setattr(read_through, "_event_repository_instance", repo)
    monkeypatch.setattr(read_through, "_event_repository_failed", False)
    return repo


@pytest.fixture
def checkpoint_repo(routed_env, monkeypatch) -> LocalFileCheckpointRepository:
    """独立临时目录检查点仓储（_acquire_checkpoint_repository 注入）。"""
    repo = LocalFileCheckpointRepository()
    monkeypatch.setattr(inst_router, "_acquire_checkpoint_repository", lambda: repo)
    return repo


def _event(instance_id, event_type, payload=None, occurred_at=None):
    return ExecutionEvent(
        event_id=str(uuid.uuid4()),
        instance_id=instance_id,
        event_type=event_type,
        node_id=None,
        sequence=0,
        payload=payload or {},
        occurred_at=occurred_at or datetime.now(timezone.utc),
    )


async def _append(repo, instance_id, event_type, payload=None):
    await repo.append(_event(instance_id, event_type, payload))


def _checkpoint(instance_id, checkpoint_id, completed_node_id=None, round_index=1):
    return Checkpoint(
        checkpoint_id=checkpoint_id,
        instance_id=instance_id,
        completed_node_id=completed_node_id,
        next_node_id=None,
        state_snapshot=SharedState(
            instance_id=instance_id,
            node_outputs={completed_node_id or "n": {"stub": True}},
        ),
        round_index=round_index,
    )


# =============================================================================
# A. /events worker 读穿透
# =============================================================================


class TestEventsEndpointReadThrough:

    async def test_unknown_instance_404(self, routed_env, event_repo):
        with pytest.raises(HTTPException) as exc_info:
            await inst_router.get_instance_events("inst-none-m5")
        assert exc_info.value.status_code == 404

    async def test_registry_miss_reads_repository_normalized(
        self, routed_env, event_repo
    ):
        """注册表未命中 → 事件仓储读穿透（归一化：类型反映射 + payload dict）。"""
        iid = "inst-m5-events"
        await _append(
            event_repo, iid, EventType.INSTANCE_STARTED, {"definition_id": "wf-m5"}
        )
        await _append(event_repo, iid, EventType.NODE_SUCCEEDED, {"node_id": "n1"})

        events = await inst_router.get_instance_events(iid)

        assert [e["event_type"] for e in events] == [
            "INSTANCE_STARTED",
            "NODE_COMPLETED",
        ], "NODE_SUCCEEDED 归一化为注册表口径 NODE_COMPLETED"
        assert events[0]["payload"]["definition_id"] == "wf-m5"

    async def test_registry_hit_takes_priority(self, routed_env, event_repo):
        """注册表命中优先：双源同时有事件时走注册表（in_process 语义）。"""
        from orchestration_platform.application.execution_runner import (
            InstanceRuntime,
        )

        iid = "inst-m5-registry-priority"
        rt = InstanceRuntime(
            instance_id=iid, definition_id="wf-m5", tenant_id=None
        )
        inst_router._instance_registry.register(rt)
        inst_router._instance_registry.append_event(
            iid,
            {
                "event_id": "evt-reg-1",
                "instance_id": iid,
                "event_type": "INSTANCE_STARTED",
                "payload": {"marker": "registry"},
            },
        )
        await _append(event_repo, iid, EventType.INSTANCE_COMPLETED, {"marker": "repo"})
        try:
            events = await inst_router.get_instance_events(iid)
            assert len(events) == 1
            assert events[0]["payload"]["marker"] == "registry"
        finally:
            inst_router._instance_registry.remove(iid)


# =============================================================================
# B. /results worker 读穿透
# =============================================================================


class TestResultsEndpointReadThrough:

    async def test_unknown_instance_404(
        self, routed_env, event_repo, checkpoint_repo
    ):
        with pytest.raises(HTTPException) as exc_info:
            await inst_router.get_instance_results("inst-none-m5")
        assert exc_info.value.status_code == 404

    async def test_running_outputs_none(
        self, routed_env, event_repo, checkpoint_repo
    ):
        """非终态 → 200 {outputs: null}（轮询友好，不设 409）。"""
        iid = "inst-m5-run"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)

        result = await inst_router.get_instance_results(iid)

        assert result == {
            "instance_id": iid,
            "status": "running",
            "outputs": None,
        }

    async def test_succeeded_outputs_from_latest_checkpoint(
        self, routed_env, event_repo, checkpoint_repo
    ):
        """succeeded 输出 = 检查点快照 node_outputs（INV-08 权威输出存储）。"""
        iid = "inst-m5-ok"
        started = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        finished = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
        await event_repo.append(
            _event(iid, EventType.INSTANCE_STARTED, occurred_at=started)
        )
        await event_repo.append(
            _event(iid, EventType.INSTANCE_COMPLETED, occurred_at=finished)
        )
        await checkpoint_repo.append(
            Checkpoint(
                checkpoint_id="cp-m5-1",
                instance_id=iid,
                completed_node_id="transform_a",
                next_node_id=None,
                state_snapshot=SharedState(
                    instance_id=iid,
                    node_outputs={
                        "trigger_manual": {"v": 1},
                        "transform_a": {"v": 2},
                    },
                ),
                round_index=2,
            )
        )

        result = await inst_router.get_instance_results(iid)

        assert result["status"] == "succeeded"
        assert result["outputs"] == {
            "trigger_manual": {"v": 1},
            "transform_a": {"v": 2},
        }
        assert result["finished_at"] == finished.isoformat()

    async def test_succeeded_without_checkpoint_empty_outputs(
        self, routed_env, event_repo, checkpoint_repo
    ):
        """succeeded 但无检查点快照 → outputs={}（可用性优先回退）。"""
        iid = "inst-m5-ok-no-cp"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)
        await _append(event_repo, iid, EventType.INSTANCE_COMPLETED)

        result = await inst_router.get_instance_results(iid)

        assert result["status"] == "succeeded"
        assert result["outputs"] == {}
        assert result["finished_at"] is not None

    async def test_failed_empty_outputs(
        self, routed_env, event_repo, checkpoint_repo
    ):
        """failed 终态 → outputs={}（镜像 in_process 仅 succeeded 携带输出）。"""
        iid = "inst-m5-fail"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)
        await _append(event_repo, iid, EventType.INSTANCE_FAILED)

        result = await inst_router.get_instance_results(iid)

        assert result["status"] == "failed"
        assert result["outputs"] == {}

    async def test_pending_window_outputs_none(
        self, routed_env, event_repo, checkpoint_repo
    ):
        """可见窗（仅 INSTANCE_QUEUED）→ pending 非终态 outputs=None。"""
        iid = "inst-m5-pending"
        await _append(event_repo, iid, EventType.INSTANCE_QUEUED)

        result = await inst_router.get_instance_results(iid)

        assert result["status"] == "pending"
        assert result["outputs"] is None
