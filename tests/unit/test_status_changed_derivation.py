# =============================================================================
# M5 STATUS_CHANGED 决策可见性推导单测
# （tests/unit/test_status_changed_derivation.py）
#
# 覆盖（design D7，T4）：
#   A. read_instance_snapshot STATUS_CHANGED 推导——to_status 覆盖快照状态
#      （approve→running 无 finished_at；reject→failed/cancelled 终态带
#      finished_at；review_timeout 非终态无 finished_at）；
#   B. RepositoryEventReader.is_terminal 终态扩展——STATUS_CHANGED(
#      to_status ∈ 终态) 即 SSE 收尾；running/review_timeout 不收流；
#      INSTANCE_COMPLETED 既有终态回归；SUSPENDED 非终态；
#   C. 归一化与防御——NODE_SUCCEEDED→NODE_COMPLETED 反映射、事件形态
#      dict 化、仓储异常 → None。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_status_changed_derivation.py -q
# =============================================================================

import uuid
from datetime import datetime, timezone

from orchestration_platform.api.instance_read_through import (
    RepositoryEventReader,
    read_instance_snapshot,
)
from orchestration_platform.domain.models.enums import EventType
from orchestration_platform.domain.models.execution_event import ExecutionEvent


class _FakeEventRepository:
    """内存事件仓储桩：list_for_instance 过滤实例。"""

    def __init__(self, events=None):
        self._events = list(events or [])

    async def list_for_instance(self, instance_id):
        return [e for e in self._events if e.instance_id == instance_id]


class _FailingEventRepository:
    """异常仓储桩：查询必失败（降级路径观测）。"""

    async def list_for_instance(self, instance_id):
        raise RuntimeError("storage down")


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


def _fake(events):
    return _FakeEventRepository(events)


def _snapshot(iid, events):
    return read_instance_snapshot(iid, event_repository=_fake(events))


# =============================================================================
# A. read_instance_snapshot STATUS_CHANGED 推导
# =============================================================================


class TestSnapshotStatusChangedDerivation:

    async def test_status_changed_running_overrides_without_finished_at(self):
        """approve → running：覆盖状态、不带 finished_at（流保持开启）。"""
        iid = "inst-m5-sc-running"
        snapshot = await _snapshot(
            iid,
            [
                _event(iid, EventType.INSTANCE_STARTED, {"definition_id": "wf"}),
                _event(iid, EventType.INSTANCE_SUSPENDED),
                _event(
                    iid,
                    EventType.STATUS_CHANGED,
                    {"from_status": "waiting_review", "to_status": "running"},
                ),
            ],
        )
        assert snapshot["status"] == "running"
        assert "finished_at" not in snapshot

    async def test_status_changed_failed_sets_finished_at(self):
        """reject → failed：终态 finished_at 取留痕事件时刻。"""
        iid = "inst-m5-sc-failed"
        stamp = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        snapshot = await _snapshot(
            iid,
            [
                _event(iid, EventType.INSTANCE_STARTED, {"definition_id": "wf"}),
                _event(
                    iid,
                    EventType.STATUS_CHANGED,
                    {"from_status": "waiting_review", "to_status": "failed"},
                    occurred_at=stamp,
                ),
            ],
        )
        assert snapshot["status"] == "failed"
        assert snapshot["finished_at"] == stamp.isoformat()

    async def test_status_changed_cancelled_terminal(self):
        iid = "inst-m5-sc-cancelled"
        snapshot = await _snapshot(
            iid,
            [
                _event(
                    iid,
                    EventType.STATUS_CHANGED,
                    {"from_status": "waiting_review", "to_status": "cancelled"},
                ),
            ],
        )
        assert snapshot["status"] == "cancelled"
        assert snapshot.get("finished_at")

    async def test_status_changed_review_timeout_not_terminal_snapshot(self):
        """timeout_action → review_timeout：状态可见、非终态无 finished_at。"""
        iid = "inst-m5-sc-timeout"
        snapshot = await _snapshot(
            iid,
            [
                _event(
                    iid,
                    EventType.STATUS_CHANGED,
                    {"from_status": "waiting_review", "to_status": "review_timeout"},
                ),
            ],
        )
        assert snapshot["status"] == "review_timeout"
        assert "finished_at" not in snapshot

    async def test_queued_only_pending(self):
        iid = "inst-m5-sc-queued"
        snapshot = await _snapshot(
            iid,
            [_event(iid, EventType.INSTANCE_QUEUED, {"definition_id": "wf"})],
        )
        assert snapshot["status"] == "pending"
        assert snapshot["definition_id"] == "wf"

    async def test_suspended_waiting_review(self):
        iid = "inst-m5-sc-suspended"
        snapshot = await _snapshot(iid, [_event(iid, EventType.INSTANCE_SUSPENDED)])
        assert snapshot["status"] == "waiting_review"

    async def test_no_events_returns_none(self):
        assert await _snapshot("inst-m5-none", []) is None

    async def test_repo_failure_returns_none(self):
        """仓储查询失败 → None（调用方维持 404，不 500）。"""
        result = await read_instance_snapshot(
            "inst-m5-fail-repo", event_repository=_FailingEventRepository()
        )
        assert result is None


# =============================================================================
# B. RepositoryEventReader.is_terminal 终态扩展（SSE 收尾判定）
# =============================================================================


class TestIsTerminalStatusChanged:

    async def test_status_changed_failed_true(self):
        iid = "inst-m5-term-failed"
        reader = RepositoryEventReader(
            _fake(
                [
                    _event(
                        iid,
                        EventType.STATUS_CHANGED,
                        {"to_status": "failed"},
                    )
                ]
            )
        )
        assert await reader.is_terminal(iid) is True, "reject→failed 即收尾"

    async def test_status_changed_cancelled_true(self):
        iid = "inst-m5-term-cancelled"
        reader = RepositoryEventReader(
            _fake(
                [
                    _event(
                        iid,
                        EventType.STATUS_CHANGED,
                        {"to_status": "cancelled"},
                    )
                ]
            )
        )
        assert await reader.is_terminal(iid) is True

    async def test_status_changed_running_false(self):
        """approve→running 不收流（续跑终态由后续 INSTANCE_COMPLETED 收口）。"""
        iid = "inst-m5-term-running"
        reader = RepositoryEventReader(
            _fake(
                [
                    _event(
                        iid,
                        EventType.STATUS_CHANGED,
                        {"to_status": "running"},
                    )
                ]
            )
        )
        assert await reader.is_terminal(iid) is False

    async def test_status_changed_review_timeout_false(self):
        """review_timeout 非终态（流保持开启等待恢复）。"""
        iid = "inst-m5-term-timeout"
        reader = RepositoryEventReader(
            _fake(
                [
                    _event(
                        iid,
                        EventType.STATUS_CHANGED,
                        {"to_status": "review_timeout"},
                    )
                ]
            )
        )
        assert await reader.is_terminal(iid) is False

    async def test_instance_completed_true_regression(self):
        iid = "inst-m5-term-completed"
        reader = RepositoryEventReader(
            _fake([_event(iid, EventType.INSTANCE_COMPLETED)])
        )
        assert await reader.is_terminal(iid) is True

    async def test_suspended_false(self):
        """waiting_review 非终态（等待审核决策）。"""
        iid = "inst-m5-term-suspended"
        reader = RepositoryEventReader(
            _fake([_event(iid, EventType.INSTANCE_SUSPENDED)])
        )
        assert await reader.is_terminal(iid) is False

    async def test_queued_pending_false(self):
        iid = "inst-m5-term-queued"
        reader = RepositoryEventReader(
            _fake([_event(iid, EventType.INSTANCE_QUEUED)])
        )
        assert await reader.is_terminal(iid) is False


# =============================================================================
# C. 事件归一化（读取形态）
# =============================================================================


class TestReadEventsNormalization:

    async def test_node_succeeded_reverse_mapped_and_payload_dict(self):
        """NODE_SUCCEEDED → NODE_COMPLETED 反映射 + 事件形态 dict 化。"""
        iid = "inst-m5-norm"
        reader = RepositoryEventReader(
            _fake(
                [
                    _event(iid, EventType.NODE_SUCCEEDED, {"node_id": "n1"}),
                    _event(iid, EventType.INSTANCE_QUEUED, {"definition_id": "wf"}),
                ]
            )
        )
        events = await reader.read_events(iid)
        assert [e["event_type"] for e in events] == [
            "NODE_COMPLETED",
            "INSTANCE_QUEUED",
        ]
        assert events[0]["payload"] == {"node_id": "n1"}
        assert events[0]["timestamp"] is not None

    async def test_empty_instance_returns_empty_list(self):
        reader = RepositoryEventReader(_FakeEventRepository())
        assert await reader.read_events("inst-m5-empty") == []
