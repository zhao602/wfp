# =============================================================================
# 审批队列服务单测（tests/unit/test_approval_queue.py）
#
# 覆盖：B16 F4 审批运营面——ApprovalQueueService 双模式聚合（design D1）
#       + suspended_at 双源兜底（D2）+ 租户过滤（D3）+ deadline 纯函数 +
#       InstanceRegistry.list_by_status 扫描原语。
# 分层：
#   A. compute_deadline 纯函数——空值/非正超时/合法计算/naive UTC/非法串；
#   B. InstanceRegistry.list_by_status——状态过滤；
#   C. 进程内行——字段全量/显式 suspended_at 优先/timestamp 兜底/
#      非等待状态排除/租户过滤（含租户缺失排除）/版本条件键/
#      无挂起事件边界（rt.suspended_checkpoint_id 兜底）；
#   D. worker 行——载荷字段/occurred_at 兜底/无挂起事件跳过/
#      挂起后已完成跳过/租户载荷提取与过滤/注册表优先去重；
#   E. 容错与排序——仓储列举失败仅注册表源/单实例读取失败跳过/
#      挂起时刻降序。
# =============================================================================

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from orchestration_platform.application.approval_queue_service import (
    ApprovalQueueService,
    compute_deadline,
)
from orchestration_platform.application.execution_runner import (
    InstanceRegistry,
    InstanceRuntime,
)
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import EventType
from orchestration_platform.domain.models.execution_event import ExecutionEvent

# 挂起时刻（带时区 ISO）与审批超时（settings 既有键的确定性注入值）
_SUSPENDED_AT = "2026-01-01T12:00:00+00:00"
_OCCURRED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_REVIEW_TIMEOUT = 3600


# =============================================================================
# 构造辅助
# =============================================================================


def _registry_event(
    instance_id: str,
    payload: Dict[str, Any],
    *,
    timestamp: str = _SUSPENDED_AT,
    event_type: str = "INSTANCE_SUSPENDED",
) -> Dict[str, Any]:
    """注册表形态事件（dict；event_type 大写）。"""
    return {
        "event_id": f"evt-{instance_id}",
        "instance_id": instance_id,
        "event_type": event_type,
        "node_id": "review_gate",
        "timestamp": timestamp,
        "payload": dict(payload),
    }


def _suspended_payload(**overrides: Any) -> Dict[str, Any]:
    payload = {
        "checkpoint_id": "cp-1",
        "next_node_id": "after_review",
        "review_instructions": "请审阅执行计划后放行",
        "timeout_action": "reject",
        "suspended_at": _SUSPENDED_AT,
    }
    payload.update(overrides)
    return payload


def _make_rt(
    instance_id: str,
    *,
    tenant_id: Optional[str] = None,
    definition_version: Optional[int] = 3,
    events: Optional[List[Dict[str, Any]]] = None,
) -> InstanceRuntime:
    """构造 waiting_review 运行时（默认带一条 INSTANCE_SUSPENDED 注册表事件）。"""
    rt = InstanceRuntime(
        instance_id,
        "flow_x",
        tenant_id=tenant_id,
        definition_version=definition_version,
    )
    rt.status = "waiting_review"
    rt.suspended_checkpoint_id = "cp-rt"
    rt.events = (
        list(events)
        if events is not None
        else [_registry_event(instance_id, _suspended_payload())]
    )
    return rt


def _repo_event(
    event_type: EventType,
    instance_id: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    occurred_at: Optional[datetime] = None,
    sequence: int = 0,
) -> ExecutionEvent:
    """仓储形态事件（ExecutionEvent 模型）。"""
    return ExecutionEvent(
        event_id=f"evt-{instance_id}-{sequence}",
        instance_id=instance_id,
        event_type=event_type,
        sequence=sequence,
        payload=dict(payload or {}),
        occurred_at=occurred_at or _OCCURRED_AT,
    )


def _worker_events(
    instance_id: str,
    *,
    suspended_payload: Optional[Dict[str, Any]] = None,
    completed: bool = False,
    started_payload: Optional[Dict[str, Any]] = None,
) -> List[ExecutionEvent]:
    """worker 形态事件序列：STARTED → SUSPENDED（可选 COMPLETED）。"""
    events = [
        _repo_event(
            EventType.INSTANCE_STARTED,
            instance_id,
            payload=started_payload
            if started_payload is not None
            else {
                "definition_id": "flow_y",
                "definition_version": 7,
            },
            sequence=1,
        )
    ]
    if suspended_payload is None:
        suspended_payload = {
            "checkpoint_id": "cp-w",
            "review_instructions": "请审",
            "timeout_action": "wait",
            "suspended_at": _SUSPENDED_AT,
            "tenant_id": "t-w",
        }
    events.append(
        _repo_event(
            EventType.INSTANCE_SUSPENDED,
            instance_id,
            payload=suspended_payload,
            sequence=2,
        )
    )
    if completed:
        events.append(
            _repo_event(
                EventType.INSTANCE_COMPLETED,
                instance_id,
                payload={},
                sequence=3,
            )
        )
    return events


class _FakeRepo:
    """内存事件仓储桩（list_instance_ids + list_for_instance 可注入故障）。"""

    def __init__(
        self,
        events_by_instance: Optional[Dict[str, List[ExecutionEvent]]] = None,
        *,
        fail_list_ids: bool = False,
        fail_instances: tuple = (),
    ):
        self.events_by_instance = events_by_instance or {}
        self.fail_list_ids = fail_list_ids
        self.fail_instances = set(fail_instances)

    async def list_instance_ids(self) -> List[str]:
        if self.fail_list_ids:
            raise RuntimeError("仓储不可用（注入故障）")
        return list(self.events_by_instance)

    async def list_for_instance(
        self, instance_id: str, include_voided: bool = True
    ) -> List[ExecutionEvent]:
        if instance_id in self.fail_instances:
            raise RuntimeError("实例事件读取失败（注入故障）")
        return list(self.events_by_instance[instance_id])


@pytest.fixture
def review_timeout(monkeypatch):
    """settings 缓存实例属性注入确定性审批超时。"""
    monkeypatch.setattr(
        get_settings(), "review_timeout_seconds", _REVIEW_TIMEOUT
    )
    return _REVIEW_TIMEOUT


def _expected_deadline(suspended_at: str = _SUSPENDED_AT) -> str:
    return (
        datetime.fromisoformat(suspended_at)
        + timedelta(seconds=_REVIEW_TIMEOUT)
    ).isoformat()


# =============================================================================
# A. compute_deadline 纯函数
# =============================================================================


class TestComputeDeadline:
    def test_none_suspended_at_returns_none(self):
        assert compute_deadline(None, 3600) is None

    def test_missing_timeout_returns_none(self):
        assert compute_deadline(_SUSPENDED_AT, None) is None

    def test_nonpositive_timeout_returns_none(self):
        assert compute_deadline(_SUSPENDED_AT, 0) is None
        assert compute_deadline(_SUSPENDED_AT, -1) is None

    def test_invalid_timestamp_returns_none(self):
        assert compute_deadline("not-a-timestamp", 3600) is None

    def test_valid_computes_iso_deadline(self):
        assert compute_deadline(_SUSPENDED_AT, 3600) == _expected_deadline()

    def test_naive_timestamp_treated_as_utc(self):
        naive = _SUSPENDED_AT.replace("+00:00", "")
        assert compute_deadline(naive, 3600) == _expected_deadline()


# =============================================================================
# B. InstanceRegistry.list_by_status 扫描原语
# =============================================================================


class TestRegistryListByStatus:
    def test_filters_by_status(self):
        registry = InstanceRegistry()
        rt_waiting = _make_rt("inst-waiting")
        rt_running = _make_rt("inst-running")
        rt_running.status = "running"
        registry.register(rt_waiting)
        registry.register(rt_running)

        waiting = registry.list_by_status("waiting_review")
        assert [rt.instance_id for rt in waiting] == ["inst-waiting"]
        assert registry.list_by_status("running") == [rt_running]
        assert registry.list_by_status("pending") == []


# =============================================================================
# C. 进程内注册表行
# =============================================================================


class TestRegistryRows:
    async def test_row_fields_full(self, review_timeout):
        registry = InstanceRegistry()
        registry.register(
            _make_rt("inst-1", tenant_id="t-1", definition_version=3)
        )

        rows = await ApprovalQueueService(
            registry, event_repository=_FakeRepo()
        ).list_waiting_review()

        assert len(rows) == 1
        row = rows[0]
        assert row["instance_id"] == "inst-1"
        assert row["definition_id"] == "flow_x"
        assert row["definition_version"] == 3
        assert row["tenant_id"] == "t-1"
        assert row["status"] == "waiting_review"
        assert row["checkpoint_id"] == "cp-1"
        assert row["suspended_at"] == _SUSPENDED_AT
        assert row["review_timeout_seconds"] == _REVIEW_TIMEOUT
        assert row["deadline"] == _expected_deadline()
        assert row["timeout_action"] == "reject"
        assert row["review_instructions"] == "请审阅执行计划后放行"

    async def test_suspended_at_prefers_payload_over_timestamp(
        self, review_timeout
    ):
        registry = InstanceRegistry()
        event = _registry_event(
            "inst-2",
            _suspended_payload(suspended_at="2026-02-02T08:00:00+00:00"),
            timestamp="2026-01-01T00:00:00+00:00",
        )
        registry.register(_make_rt("inst-2", events=[event]))

        rows = await ApprovalQueueService(
            registry, event_repository=_FakeRepo()
        ).list_waiting_review()

        assert rows[0]["suspended_at"] == "2026-02-02T08:00:00+00:00"

    async def test_suspended_at_falls_back_to_event_timestamp(
        self, review_timeout
    ):
        """存量事件无 suspended_at 载荷键 → 事件 timestamp 兜底（D2）。"""
        registry = InstanceRegistry()
        event = _registry_event(
            "inst-3", {"checkpoint_id": "cp-x"}, timestamp="2026-03-03T09:00:00+00:00"
        )
        registry.register(_make_rt("inst-3", events=[event]))

        rows = await ApprovalQueueService(
            registry, event_repository=_FakeRepo()
        ).list_waiting_review()

        assert rows[0]["suspended_at"] == "2026-03-03T09:00:00+00:00"

    async def test_non_waiting_status_excluded(self, review_timeout):
        registry = InstanceRegistry()
        rt = _make_rt("inst-running")
        rt.status = "running"
        registry.register(rt)

        assert (
            await ApprovalQueueService(
                registry, event_repository=_FakeRepo()
            ).list_waiting_review()
            == []
        )

    async def test_tenant_filter_keeps_matching_only(self, review_timeout):
        registry = InstanceRegistry()
        registry.register(_make_rt("inst-a", tenant_id="t-a"))
        registry.register(_make_rt("inst-b", tenant_id="t-b"))
        service = ApprovalQueueService(registry, event_repository=_FakeRepo())

        rows = await service.list_waiting_review(tenant_id="t-a")

        assert [r["instance_id"] for r in rows] == ["inst-a"]

    async def test_null_tenant_excluded_when_filter_active(self, review_timeout):
        """租户缺失行在过滤时不入结果（归属不可证不外泄，D3）。"""
        registry = InstanceRegistry()
        registry.register(_make_rt("inst-null", tenant_id=None))
        service = ApprovalQueueService(registry, event_repository=_FakeRepo())

        assert await service.list_waiting_review(tenant_id="t-a") == []
        assert len(await service.list_waiting_review()) == 1

    async def test_definition_version_omitted_when_none(self, review_timeout):
        registry = InstanceRegistry()
        registry.register(
            _make_rt("inst-nv", definition_version=None)
        )

        rows = await ApprovalQueueService(
            registry, event_repository=_FakeRepo()
        ).list_waiting_review()

        assert "definition_version" not in rows[0]

    async def test_row_without_suspended_event_uses_rt_fallbacks(
        self, review_timeout
    ):
        """边界：rt 已 waiting_review 但事件缺失（内存丢失）——行仍产出，
        挂起元数据取 rt.suspended_checkpoint_id 兜底、时刻/deadline 置 None。"""
        registry = InstanceRegistry()
        registry.register(_make_rt("inst-bare", events=[]))

        rows = await ApprovalQueueService(
            registry, event_repository=_FakeRepo()
        ).list_waiting_review()

        row = rows[0]
        assert row["instance_id"] == "inst-bare"
        assert row["checkpoint_id"] == "cp-rt"
        assert row["suspended_at"] is None
        assert row["deadline"] is None
        assert row["review_instructions"] == ""
        assert row["timeout_action"] == ""


# =============================================================================
# D. worker 行（事件仓储读穿透）
# =============================================================================


class TestWorkerRows:
    async def test_worker_row_fields(self, review_timeout):
        repo = _FakeRepo(
            {"inst-w1": _worker_events("inst-w1")}
        )

        rows = await ApprovalQueueService(
            InstanceRegistry(), event_repository=repo
        ).list_waiting_review()

        assert len(rows) == 1
        row = rows[0]
        assert row["instance_id"] == "inst-w1"
        assert row["definition_id"] == "flow_y"
        assert row["definition_version"] == 7
        assert row["tenant_id"] == "t-w"
        assert row["status"] == "waiting_review"
        assert row["checkpoint_id"] == "cp-w"
        assert row["suspended_at"] == _SUSPENDED_AT
        assert row["deadline"] == _expected_deadline()
        assert row["timeout_action"] == "wait"
        assert row["review_instructions"] == "请审"

    async def test_worker_suspended_at_falls_back_to_occurred_at(
        self, review_timeout
    ):
        """存量持久化事件无 suspended_at → occurred_at 兜底（D2）。"""
        repo = _FakeRepo(
            {
                "inst-w2": _worker_events(
                    "inst-w2",
                    suspended_payload={
                        "checkpoint_id": "cp-w2",
                        "suspended_at": None,
                    },
                )
            }
        )

        rows = await ApprovalQueueService(
            InstanceRegistry(), event_repository=repo
        ).list_waiting_review()

        assert rows[0]["suspended_at"] == _OCCURRED_AT.isoformat()

    async def test_worker_row_without_tenant_payload_yields_none(
        self, review_timeout
    ):
        """降级持久化事件（无租户富化）→ 行租户 None（诚实缺失）。"""
        repo = _FakeRepo(
            {
                "inst-w3": _worker_events(
                    "inst-w3",
                    suspended_payload={"checkpoint_id": "cp-w3"},
                )
            }
        )

        rows = await ApprovalQueueService(
            InstanceRegistry(), event_repository=repo
        ).list_waiting_review()

        assert rows[0]["tenant_id"] is None

    async def test_worker_tenant_filter(self, review_timeout):
        repo = _FakeRepo(
            {
                "inst-match": _worker_events("inst-match"),
                "inst-miss": _worker_events(
                    "inst-miss",
                    suspended_payload={
                        "checkpoint_id": "cp-miss",
                        "tenant_id": "t-other",
                    },
                ),
                "inst-none": _worker_events(
                    "inst-none",
                    suspended_payload={"checkpoint_id": "cp-none"},
                ),
            }
        )
        service = ApprovalQueueService(
            InstanceRegistry(), event_repository=repo
        )

        rows = await service.list_waiting_review(tenant_id="t-w")

        assert [r["instance_id"] for r in rows] == ["inst-match"]

    async def test_worker_instance_without_suspended_event_skipped(
        self, review_timeout
    ):
        repo = _FakeRepo(
            {
                "inst-started": [
                    _repo_event(
                        EventType.INSTANCE_STARTED,
                        "inst-started",
                        payload={"definition_id": "flow_z"},
                        sequence=1,
                    )
                ]
            }
        )

        assert (
            await ApprovalQueueService(
                InstanceRegistry(), event_repository=repo
            ).list_waiting_review()
            == []
        )

    async def test_worker_instance_completed_after_suspend_skipped(
        self, review_timeout
    ):
        """挂起后已完成的实例（快照 succeeded）不入队列。"""
        repo = _FakeRepo(
            {"inst-done": _worker_events("inst-done", completed=True)}
        )

        assert (
            await ApprovalQueueService(
                InstanceRegistry(), event_repository=repo
            ).list_waiting_review()
            == []
        )

    async def test_registry_row_takes_precedence_over_repo(self, review_timeout):
        """同一实例双源命中 → 注册表行权威（D1 去重）。"""
        registry = InstanceRegistry()
        registry.register(_make_rt("inst-dup", tenant_id="t-reg"))
        repo = _FakeRepo({"inst-dup": _worker_events("inst-dup")})

        rows = await ApprovalQueueService(
            registry, event_repository=repo
        ).list_waiting_review()

        assert len(rows) == 1
        assert rows[0]["checkpoint_id"] == "cp-1"
        assert rows[0]["tenant_id"] == "t-reg"
        assert rows[0]["timeout_action"] == "reject"


# =============================================================================
# E. 容错与排序
# =============================================================================


class TestResilienceAndOrdering:
    async def test_repo_list_ids_failure_returns_registry_rows(
        self, review_timeout
    ):
        """仓储列举失败 → 降级仅注册表源（不空转、不抛错）。"""
        registry = InstanceRegistry()
        registry.register(_make_rt("inst-live"))
        repo = _FakeRepo({"inst-dead": _worker_events("inst-dead")})
        repo.fail_list_ids = True

        rows = await ApprovalQueueService(
            registry, event_repository=repo
        ).list_waiting_review()

        assert [r["instance_id"] for r in rows] == ["inst-live"]

    async def test_repo_instance_read_failure_skips_instance(
        self, review_timeout
    ):
        """单实例事件读取失败 → 跳过该实例，其余行照常返回。"""
        repo = _FakeRepo(
            {
                "inst-good": _worker_events("inst-good"),
                "inst-bad": _worker_events("inst-bad"),
            },
            fail_instances=("inst-bad",),
        )

        rows = await ApprovalQueueService(
            InstanceRegistry(), event_repository=repo
        ).list_waiting_review()

        assert [r["instance_id"] for r in rows] == ["inst-good"]

    async def test_rows_sorted_by_suspended_at_desc(self, review_timeout):
        """挂起时刻降序（新挂起优先）。"""
        registry = InstanceRegistry()
        registry.register(
            _make_rt(
                "inst-old",
                events=[
                    _registry_event(
                        "inst-old",
                        _suspended_payload(suspended_at="2026-01-01T10:00:00+00:00"),
                    )
                ],
            )
        )
        registry.register(
            _make_rt(
                "inst-new",
                events=[
                    _registry_event(
                        "inst-new",
                        _suspended_payload(suspended_at="2026-02-01T10:00:00+00:00"),
                    )
                ],
            )
        )

        rows = await ApprovalQueueService(
            registry, event_repository=_FakeRepo()
        ).list_waiting_review()

        assert [r["instance_id"] for r in rows] == ["inst-new", "inst-old"]
