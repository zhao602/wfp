# =============================================================================
# 审计事件订阅器单测（tests/unit/test_audit_event_subscriber.py）
#
# 覆盖：arch_review_v2_tasks_wave2 任务 2.12 测试三件套之二（CHG-52；
# AC-02/AC-04 审计链证据、DC-25/DC-31/CHG-44/CHG-67）。
# 分层：
#   A. 过滤链与映射——node.*/audit_written 过滤、事件→操作映射、actor 兜底、
#      载荷脱敏（design §4.6）；
#   B. 落盘与降级——主/降级双仓储、jsonl 落盘、双双失败计数丢弃；
#   C. P10 回环通知——审计已落盘事件发布；
#   D. 续链——resume_audit_hash_chain（单条目口径，CHG-68 quirk 零改动）；
#   E. 挂接薄壳——attach 无运行循环返回 None（DC-25 规则 3）；
#   F. 消费循环——_consume_loop 有限流桩（测试不驱动真实订阅长循环）；
#   G. CHG-67——内存事件总线历史上限保护；
#   H. 端到端——发布器信封 → 消费循环 → 审计落盘（AC-04 风格）。
# =============================================================================

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


from orchestration_platform.domain.models.domain_event import DomainEvent
from orchestration_platform.domain.models.enums import (
    AuditOperationResult,
    MaskMethod,
    MessageEventType,
    OperationType,
    ResourceType,
)
from orchestration_platform.governance.audit.audit_event_subscriber import (
    AUDIT_WRITTEN_EVENT_TYPE,
    AuditEventSubscriber,
)
from orchestration_platform.governance.audit.mask_engine import MaskEngine
from orchestration_platform.governance.audit.models import MaskRule
from orchestration_platform.governance.event_bus.in_memory_event_bus import (
    InMemoryEventBus,
)
from orchestration_platform.governance.event_bus.models import EventMessage
from orchestration_platform.governance.event_bus.workflow_domain_event_publisher import (
    WorkflowDomainEventPublisher,
)
from orchestration_platform.governance.persistence.local_file.local_file_audit_log_repository import (
    LocalFileAuditLogRepository,
)


# =============================================================================
# 测试基建
# =============================================================================

def _event(
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    instance_id: str = "inst-a",
    tenant_id: str = "tenant-1",
    trace_id: str = "trace-1",
    node_id: Optional[str] = None,
) -> DomainEvent:
    """构造领域事件（handle 单测通用）。"""
    return DomainEvent(
        event_type=event_type,
        instance_id=instance_id,
        tenant_id=tenant_id,
        trace_id=trace_id,
        node_id=node_id,
        payload=payload or {},
    )


def _local_repo(tmp_path: Path) -> LocalFileAuditLogRepository:
    """临时目录本地文件审计仓储（测试隔离）。"""
    return LocalFileAuditLogRepository(storage_root=str(tmp_path))


class _FailingAuditRepository:
    """失败审计仓储桩：append 恒抛异常（模拟主/降级仓储不可用）。"""

    async def append(self, entry):
        raise RuntimeError("审计仓储写入失败（注入）")

    async def query(self, tenant_id=None, start_time=None, end_time=None,
                    operation=None, actor_id=None, limit=1000):
        return []

    async def verify_hash_chain(self, tenant_id=None, start_time=None,
                                end_time=None):
        return {
            "verified_count": 0,
            "failed_count": 0,
            "failed_entry_ids": [],
            "is_valid": True,
        }

    async def get_last_hash(self, tenant_id=None):
        return ""


class _RecordingPublisher:
    """发布记录桩：收集已发布领域事件。"""

    def __init__(self) -> None:
        self.events: List[DomainEvent] = []

    async def publish_domain_event(self, event: DomainEvent) -> None:
        self.events.append(event)


class _FiniteBus:
    """有限消息总线桩：subscribe 产出预置消息后结束（DC-25：测试不驱动长循环）。"""

    def __init__(self, messages: List[EventMessage]) -> None:
        self._messages = list(messages)

    async def subscribe(self, topic: str, group_id: str):
        for message in self._messages:
            yield message

    async def publish(self, topic: str, event: EventMessage):
        return None

    @property
    def is_degraded(self) -> bool:
        return True


def _envelope(event: DomainEvent) -> EventMessage:
    """按 WorkflowDomainEventPublisher 的信封格式构造总线消息。"""
    return EventMessage(
        event_id=f"evt_{event.instance_id}",
        event_type=MessageEventType.WORKFLOW_EXECUTION,
        sequence_number=1,
        payload={"domain_event": event.model_dump(mode="json")},
        instance_id=event.instance_id,
        occurred_at=event.occurred_at,
    )


def _bus_message(sequence: int) -> EventMessage:
    """构造普通总线消息（CHG-67 历史上限测试用）。"""
    return EventMessage(
        event_id=f"evt-{sequence}",
        event_type=MessageEventType.WORKFLOW_EXECUTION,
        sequence_number=sequence,
        payload={"seq": sequence},
        instance_id="inst-bus",
    )


# =============================================================================
# A. 过滤链与映射（design §4.6）
# =============================================================================

class TestHandleFilteringAndMapping:
    """handle 纯逻辑：过滤链、事件→操作映射、actor 兜底、载荷脱敏。"""

    async def test_filters_node_events(self, tmp_path):
        subscriber = AuditEventSubscriber(primary_repository=_local_repo(tmp_path))
        assert await subscriber.handle(_event("node.succeeded", node_id="node-1")) is None
        assert subscriber.total_written == 0
        assert subscriber.total_failed == 0

    async def test_filters_audit_written_loop_event(self, tmp_path):
        subscriber = AuditEventSubscriber(primary_repository=_local_repo(tmp_path))
        assert await subscriber.handle(_event(AUDIT_WRITTEN_EVENT_TYPE)) is None
        assert subscriber.total_written == 0

    async def test_maps_instance_started_to_create_success(self, tmp_path):
        repo = _local_repo(tmp_path)
        subscriber = AuditEventSubscriber(primary_repository=repo)

        entry = await subscriber.handle(_event("instance.started"))

        assert entry is not None
        assert entry.operation == OperationType.CREATE
        assert entry.operation_result == AuditOperationResult.SUCCESS
        assert entry.actor_id == "system", "无 actor 载荷时记 system"
        assert entry.actor_type == "system"
        assert entry.resource_type == ResourceType.EXECUTION_INSTANCE
        assert entry.resource_id == "inst-a"
        assert entry.tenant_id == "tenant-1"
        assert entry.trace_id == "trace-1"
        assert subscriber.total_written == 1
        persisted = await repo.query(tenant_id="tenant-1")
        assert [e.entry_id for e in persisted] == [entry.entry_id]

    async def test_maps_instance_failed_to_execute_failure(self, tmp_path):
        subscriber = AuditEventSubscriber(primary_repository=_local_repo(tmp_path))

        entry = await subscriber.handle(_event("instance.failed"))

        assert entry is not None
        assert entry.operation == OperationType.EXECUTE
        assert entry.operation_result == AuditOperationResult.FAILURE

    async def test_actor_taken_from_payload(self, tmp_path):
        subscriber = AuditEventSubscriber(primary_repository=_local_repo(tmp_path))

        entry = await subscriber.handle(
            _event("instance.started", payload={"actor": "user-1"})
        )

        assert entry is not None
        assert entry.actor_id == "user-1"
        assert entry.actor_type == "user"

    async def test_masks_sensitive_payload_fields(self, tmp_path):
        mask_engine = MaskEngine()
        mask_engine.add_rule(
            MaskRule(
                rule_id="r-secret",
                field_path="secret",
                mask_method=MaskMethod.FULL_MASK,
            )
        )
        subscriber = AuditEventSubscriber(
            primary_repository=_local_repo(tmp_path),
            mask_engine=mask_engine,
        )

        entry = await subscriber.handle(
            _event(
                "instance.started",
                payload={"secret": "hunter2", "visible": 1},
            )
        )

        assert entry is not None
        assert entry.request_payload is not None
        assert entry.request_payload["secret"] == "****"
        assert entry.request_payload["visible"] == 1

    async def test_unmapped_event_falls_back_to_default_operation(self, tmp_path):
        subscriber = AuditEventSubscriber(primary_repository=_local_repo(tmp_path))

        entry = await subscriber.handle(_event("governance.policy_updated"))

        assert entry is not None
        assert entry.operation == OperationType.EXECUTE
        assert entry.operation_result == AuditOperationResult.SUCCESS


# =============================================================================
# B. 落盘与降级（DC-31 降级语义）
# =============================================================================

class TestPersistenceAndDegradation:
    """jsonl 落盘、主失败转降级、双双失败计数丢弃。"""

    async def test_persists_to_local_file_jsonl(self, tmp_path):
        repo = _local_repo(tmp_path)
        subscriber = AuditEventSubscriber(primary_repository=repo)

        entry = await subscriber.handle(_event("instance.started"))

        log_file = (
            tmp_path / "governance" / "audit_logs" / "tenant-1" / "audit_log.jsonl"
        )
        assert log_file.exists(), "按租户分目录落盘"
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["entry_id"] == entry.entry_id
        assert data["operation"] == "create"

    async def test_degrades_to_fallback_repository(self, tmp_path):
        fallback = _local_repo(tmp_path)
        subscriber = AuditEventSubscriber(
            primary_repository=_FailingAuditRepository(),
            fallback_repository=fallback,
        )

        entry = await subscriber.handle(_event("instance.started"))

        assert entry is not None
        assert subscriber.total_written == 1
        persisted = await fallback.query(tenant_id="tenant-1")
        assert [e.entry_id for e in persisted] == [entry.entry_id]

    async def test_counts_dropped_when_all_repositories_fail(self):
        subscriber = AuditEventSubscriber(
            primary_repository=_FailingAuditRepository(),
            fallback_repository=_FailingAuditRepository(),
        )

        entry = await subscriber.handle(_event("instance.started"))

        assert entry is None
        assert subscriber.total_written == 0
        assert subscriber.total_failed == 1, "双双失败计数丢弃、不抛异常"


# =============================================================================
# C. P10 回环通知
# =============================================================================

class TestAuditWrittenNotification:
    """审计已落盘通知：事件类型、载荷契约（P10）。"""

    async def test_publishes_audit_written_notification(self, tmp_path):
        recorder = _RecordingPublisher()
        subscriber = AuditEventSubscriber(
            primary_repository=_local_repo(tmp_path),
            domain_event_publisher=recorder,
        )

        entry = await subscriber.handle(_event("instance.started"))

        assert entry is not None
        assert len(recorder.events) == 1
        notified = recorder.events[0]
        assert notified.event_type == AUDIT_WRITTEN_EVENT_TYPE
        assert notified.payload["entry_id"] == entry.entry_id
        assert notified.payload["tenant_id"] == "tenant-1"
        assert notified.instance_id == "inst-a"


# =============================================================================
# D. 续链（resume_audit_hash_chain；单条目口径，CHG-68 quirk 零改动）
# =============================================================================

class TestResumeHashChain:
    """启动期哈希链恢复：新订阅器续接既有链尾。"""

    async def test_resume_links_next_entry_to_existing_chain_tail(self, tmp_path):
        repo = _local_repo(tmp_path)
        first_subscriber = AuditEventSubscriber(primary_repository=repo)
        entry1 = await first_subscriber.handle(_event("instance.started"))
        assert entry1.previous_hash == "", "首条目空链起步"

        second_subscriber = AuditEventSubscriber(primary_repository=repo)
        await second_subscriber.resume_audit_hash_chain()
        entry2 = await second_subscriber.handle(_event("instance.succeeded"))

        assert entry2.previous_hash == entry1.entry_hash, "续链：前哈希接既有链尾"


# =============================================================================
# E. 挂接薄壳（DC-25 规则 3）
# =============================================================================

class TestAttach:
    """attach：无运行事件循环返回 None（不抛异常，保证装配安全）。"""

    def test_attach_without_running_loop_returns_none(self, tmp_path):
        subscriber = AuditEventSubscriber(primary_repository=_local_repo(tmp_path))
        task = subscriber.attach(_FiniteBus([]), "workflow.events", "audit-group")
        assert task is None

    async def test_attach_starts_consume_task_and_persists(self, tmp_path):
        repo = _local_repo(tmp_path)
        subscriber = AuditEventSubscriber(primary_repository=repo)

        task = subscriber.attach(
            _FiniteBus([_envelope(_event("instance.started"))]),
            "workflow.events",
            "audit-group",
        )

        assert task is not None
        await task
        assert subscriber.total_written == 1
        persisted = await repo.query(tenant_id="tenant-1")
        assert len(persisted) == 1
        assert persisted[0].operation == OperationType.CREATE


# =============================================================================
# F. 消费循环（_consume_loop；有限流桩驱动）
# =============================================================================

class TestConsumeLoop:
    """消费循环：坏消息跳过、audit_written 过滤、单条失败不中断。"""

    async def test_skips_unparseable_messages(self, tmp_path):
        repo = _local_repo(tmp_path)
        bad = EventMessage(
            event_id="evt_bad",
            event_type=MessageEventType.WORKFLOW_EXECUTION,
            sequence_number=2,
            payload={
                "domain_event": {
                    "event_type": "not.a.valid.prefix",
                    "instance_id": "x",
                    "tenant_id": "t",
                    "trace_id": "tr",
                }
            },
            instance_id="inst-a",
        )
        good = _envelope(_event("instance.started"))
        subscriber = AuditEventSubscriber(primary_repository=repo)

        await subscriber._consume_loop(
            _FiniteBus([bad, good]), "workflow.events", "audit-group"
        )

        assert subscriber.total_written == 1
        assert subscriber.total_failed == 0
        assert len(await repo.query(tenant_id="tenant-1")) == 1

    async def test_filters_audit_written_events(self, tmp_path):
        repo = _local_repo(tmp_path)
        subscriber = AuditEventSubscriber(primary_repository=repo)

        await subscriber._consume_loop(
            _FiniteBus([_envelope(_event(AUDIT_WRITTEN_EVENT_TYPE))]),
            "workflow.events",
            "audit-group",
        )

        assert subscriber.total_written == 0, "回环事件不落审计（防自环 P10）"
        assert subscriber.total_failed == 0


# =============================================================================
# G. CHG-67：内存事件总线历史上限保护
# =============================================================================

class TestInMemoryBusHistoryMax:
    """history_max：超限丢最旧；默认不限制（基线行为不变）。"""

    async def test_history_max_drops_oldest(self):
        bus = InMemoryEventBus(history_max=2)
        for sequence in (1, 2, 3):
            await bus.publish("workflow.events", _bus_message(sequence))

        replay = await bus.replay_from_offset("workflow.events", 0, 10)

        assert [m.sequence_number for m in replay.events] == [2, 3], (
            "超出上限丢弃最旧事件，保留最新 history_max 条"
        )

    async def test_without_history_max_keeps_all(self):
        bus = InMemoryEventBus()
        for sequence in (1, 2, 3):
            await bus.publish("workflow.events", _bus_message(sequence))

        replay = await bus.replay_from_offset("workflow.events", 0, 10)

        assert [m.sequence_number for m in replay.events] == [1, 2, 3], (
            "默认不限制（CHG-67：既有行为零变化）"
        )


# =============================================================================
# H. 端到端：发布器信封 → 消费循环 → 审计落盘（AC-04 风格）
# =============================================================================

class TestPublishConsumeEndToEnd:
    """发布器产生的信封消息可被订阅器消费并落盘（信封契约闭环）。"""

    async def test_publisher_envelope_feeds_subscriber_consume_loop(self, tmp_path):
        bus = InMemoryEventBus()
        publisher = WorkflowDomainEventPublisher(bus, topic="workflow.events")
        await publisher.publish_domain_event(_event("instance.started"))

        replay = await bus.replay_from_offset("workflow.events", 0, 10)
        assert len(replay.events) == 1

        repo = _local_repo(tmp_path)
        subscriber = AuditEventSubscriber(primary_repository=repo)
        await subscriber._consume_loop(
            _FiniteBus(list(replay.events)), "workflow.events", "audit-group"
        )

        assert subscriber.total_written == 1
        persisted = await repo.query(tenant_id="tenant-1")
        assert len(persisted) == 1
        assert persisted[0].operation == OperationType.CREATE
        assert persisted[0].resource_id == "inst-a"
