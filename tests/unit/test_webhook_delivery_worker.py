# =============================================================================
# Webhook 投递工作器单测（tests/unit/test_webhook_delivery_worker.py）
#
# 职责：匹配过滤 / 签名投递 / 断连重投 0 丢失 / 死信与重投 / p95 时延 /
#       消费循环健壮性（roadmap B3 三验收的 V 型用例层）。
# 设计意图（对应 design §3.4 / §7 测试矩阵）：
#   1. httpx 一律 MockTransport（零真实网络）；
#   2. 退避 base=0.001 注入（测试不等真实退避）；
#   3. 0 丢失：断连 N 次后成功 → delivered(attempts=N+1)；
#      永久失败 → attempts=max 后入 DLQ（无第三态）；
#   4. p95<2s：20 事件逐事件计时，宽松上界防 flaky（实际 ms 级）。
# =============================================================================

import asyncio
import time

import httpx
import pytest

from orchestration_platform.domain.models.domain_event import DomainEvent
from orchestration_platform.domain.models.enums import MessageEventType
from orchestration_platform.governance.event_bus.models import EventMessage
from orchestration_platform.governance.event_export.signature import (
    canonical_event_body,
    verify_webhook,
)
from orchestration_platform.governance.event_export.subscription_registry import (
    EventSubscriptionRegistry,
)
from orchestration_platform.governance.event_export.webhook_delivery_worker import (
    WebhookDeliveryWorker,
)


def _event(
    event_type: str = "instance.succeeded",
    tenant_id: str = "tenant-1",
    instance_id: str = "inst-1",
    trace_id: str = "trace-1",
) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        instance_id=instance_id,
        tenant_id=tenant_id,
        trace_id=trace_id,
        payload={"status": "succeeded"},
    )


def _envelope(event: DomainEvent, event_id: str, sequence: int = 1) -> EventMessage:
    return EventMessage(
        event_id=event_id,
        event_type=MessageEventType.WORKFLOW_EXECUTION,
        sequence_number=sequence,
        payload={"domain_event": event.model_dump(mode="json")},
        instance_id=event.instance_id,
    )


class _FakeReceiver:
    """MockTransport 处理器：前 failures 次抛连接异常（模拟断连），其后按 status 返回。"""

    def __init__(self, failures: int = 0, status: int = 200) -> None:
        self.requests: list = []
        self.failures = failures
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if len(self.requests) <= self.failures:
            raise httpx.ConnectError("simulated disconnect", request=request)
        return httpx.Response(self.status, json={"ok": True})


class _FiniteBus:
    """有限消息总线（消费循环测试；复用审计订阅器测试同范式）。"""

    def __init__(self, messages: list) -> None:
        self._messages = list(messages)

    async def subscribe(self, topic: str, group_id: str):
        for message in self._messages:
            yield message


def _registry_with(*subscriptions) -> EventSubscriptionRegistry:
    registry = EventSubscriptionRegistry()
    for subscription in subscriptions:
        registry.register(subscription)
    return registry


def _subscription(**overrides):
    from orchestration_platform.governance.event_export.models import WebhookSubscription

    params = dict(
        subscription_id="sub-1",
        tenant_id="tenant-1",
        url="http://receiver.example/hook",
        secret="s3cret",
        event_types=[],
    )
    params.update(overrides)
    return WebhookSubscription(**params)


def _worker(registry: EventSubscriptionRegistry, receiver: _FakeReceiver, **overrides):
    params = dict(
        max_attempts=3,
        backoff_base_seconds=0.001,
        backoff_cap_seconds=0.01,
        timeout_seconds=1.0,
        delivery_history_max=10,
        dlq_max_entries=10,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(receiver)),
    )
    params.update(overrides)
    return WebhookDeliveryWorker(registry, **params)


async def _drain(worker: WebhookDeliveryWorker) -> None:
    """等待在途投递任务全部完成（handle 异步派发后汇聚用）。"""
    while worker._delivery_tasks:
        tasks = list(worker._delivery_tasks)
        if tasks:
            await asyncio.gather(*tasks)


# =============================================================================
# A. 匹配过滤（enabled / tenant / event_types）
# =============================================================================


class TestMatchingFilters:
    async def test_no_matching_subscription_returns_no_tasks(self):
        worker = _worker(
            _registry_with(_subscription(tenant_id="other-tenant")), _FakeReceiver()
        )
        assert worker.handle(_event(), event_id="evt-1") == []

    async def test_filters_tenant_event_types_and_enabled(self):
        registry = _registry_with(
            _subscription(subscription_id="sub-tenant", tenant_id="other"),
            _subscription(subscription_id="sub-type", event_types=["node.started"]),
            _subscription(subscription_id="sub-off", enabled=False),
            _subscription(subscription_id="sub-hit"),
        )
        worker = _worker(registry, _FakeReceiver())
        tasks = worker.handle(_event(), event_id="evt-1")
        assert len(tasks) == 1, "仅 tenant+type+enabled 全匹配的订阅命中"
        await asyncio.gather(*tasks)


# =============================================================================
# B. 成功投递（线级契约：签名头 + canonical body）
# =============================================================================


class TestSuccessfulDelivery:
    async def test_delivers_signed_canonical_body(self):
        subscription = _subscription()
        registry = _registry_with(subscription)
        receiver = _FakeReceiver()
        worker = _worker(registry, receiver)
        event = _event()

        tasks = worker.handle(event, event_id="evt-1")
        await asyncio.gather(*tasks)

        assert len(receiver.requests) == 1
        request = receiver.requests[0]
        body = request.content.decode("utf-8")
        assert body == canonical_event_body(event), "canonical body 口径"
        signature_header = request.headers["x-orch-signature"]
        assert verify_webhook(subscription.secret, signature_header, body) is True, (
            "签名头可验签"
        )
        assert request.headers["x-orch-event-type"] == "instance.succeeded"
        assert request.headers["x-orch-instance-id"] == "inst-1"

        records = worker.list_deliveries("sub-1")
        assert len(records) == 1
        assert records[0].last_status == "delivered"
        assert records[0].attempts == 1
        assert records[0].event_id == "evt-1"
        assert records[0].delivered_at is not None
        assert worker.list_dead_letters() == []


# =============================================================================
# C. 断连重投 0 丢失 + 永久失败入死信（roadmap 验收 1）
# =============================================================================


class TestRetryAndDeadLetter:
    async def test_disconnect_retry_delivers_with_attempt_count(self):
        receiver = _FakeReceiver(failures=2)
        worker = _worker(_registry_with(_subscription()), receiver)

        tasks = worker.handle(_event(), event_id="evt-1")
        await asyncio.gather(*tasks)

        assert len(receiver.requests) == 3, "2 次断连后第 3 次成功"
        records = worker.list_deliveries("sub-1")
        assert records[0].last_status == "delivered"
        assert records[0].attempts == 3, "attempts 反映重投次数（0 丢失实证）"
        assert worker.list_dead_letters() == []

    async def test_permanent_failure_lands_in_dlq(self):
        receiver = _FakeReceiver(status=500)
        worker = _worker(_registry_with(_subscription()), receiver, max_attempts=2)

        event = _event()
        tasks = worker.handle(event, event_id="evt-1")
        await asyncio.gather(*tasks)

        records = worker.list_deliveries("sub-1")
        assert records[0].last_status == "failed"
        assert records[0].attempts == 2
        assert "HTTP 500" in records[0].last_error

        dead_letters = worker.list_dead_letters()
        assert len(dead_letters) == 1, "重试耗尽入死信（终态二选一，无第三态）"
        entry = dead_letters[0]
        assert entry.event_id == "evt-1"
        assert entry.attempts == 2
        assert entry.payload_snapshot == event.model_dump(mode="json"), (
            "死信载荷快照可重建事件（redrive 依据）"
        )

    async def test_redrive_success_removes_from_dlq(self):
        receiver = _FakeReceiver(status=500)
        worker = _worker(_registry_with(_subscription()), receiver, max_attempts=2)

        tasks = worker.handle(_event(), event_id="evt-1")
        await asyncio.gather(*tasks)
        entry_id = worker.list_dead_letters()[0].entry_id

        receiver.status = 200
        receiver.failures = 0
        entry = await worker.redrive(entry_id)

        assert worker.list_dead_letters() == [], "重投成功移出 DLQ"
        statuses = [r.last_status for r in worker.list_deliveries("sub-1")]
        assert statuses == ["failed", "delivered"], "重投周期投递记录完整"
        assert entry.attempts == 3, "累计尝试 = 原 2 + 重投周期 1"

        with pytest.raises(KeyError):
            await worker.redrive("missing-entry")

    async def test_dlq_cap_evicts_oldest(self):
        receiver = _FakeReceiver(status=500)
        worker = _worker(
            _registry_with(_subscription()),
            receiver,
            max_attempts=1,
            dlq_max_entries=2,
        )
        for index in range(3):
            tasks = worker.handle(
                _event(trace_id=f"trace-{index}"), event_id=f"evt-{index}"
            )
            await asyncio.gather(*tasks)

        remaining = worker.list_dead_letters()
        assert len(remaining) == 2, "超限丢最旧"
        assert [e.event_id for e in remaining] == ["evt-1", "evt-2"]

    async def test_delivery_history_ring_buffer(self):
        worker = _worker(
            _registry_with(_subscription()), _FakeReceiver(), delivery_history_max=2
        )
        for index in range(3):
            tasks = worker.handle(
                _event(trace_id=f"trace-{index}"), event_id=f"evt-{index}"
            )
            await asyncio.gather(*tasks)

        records = worker.list_deliveries("sub-1")
        assert [r.event_id for r in records] == ["evt-1", "evt-2"], "环形上限丢最旧"


# =============================================================================
# D. 多订阅并发 + p95 时延（roadmap 验收 2/3 单元层）
# =============================================================================


class TestConcurrencyAndLatency:
    async def test_multiple_subscriptions_each_delivered(self):
        registry = _registry_with(
            _subscription(subscription_id="sub-a"),
            _subscription(subscription_id="sub-b"),
        )
        receiver = _FakeReceiver()
        worker = _worker(registry, receiver)

        tasks = worker.handle(_event(), event_id="evt-1")
        assert len(tasks) == 2
        await asyncio.gather(*tasks)

        assert len(receiver.requests) == 2, "每订阅各达一份"
        assert len(worker.list_deliveries("sub-a")) == 1
        assert len(worker.list_deliveries("sub-b")) == 1

    async def test_p95_delivery_latency_under_2_seconds(self):
        worker = _worker(_registry_with(_subscription()), _FakeReceiver())
        latencies = []
        for index in range(20):
            started = time.perf_counter()
            tasks = worker.handle(
                _event(trace_id=f"trace-{index}"), event_id=f"evt-{index}"
            )
            await asyncio.gather(*tasks)
            latencies.append(time.perf_counter() - started)
        assert max(latencies) < 2.0, (
            f"20 事件逐事件投递时延 max={max(latencies):.4f}s < 2s（≥ p95 口径）"
        )


# =============================================================================
# E. 消费循环与生命周期
# =============================================================================


class TestConsumeLoopAndStop:
    async def test_consume_loop_skips_invalid_and_delivers_valid(self):
        registry = _registry_with(_subscription())
        receiver = _FakeReceiver()
        worker = _worker(registry, receiver)
        messages = [
            EventMessage(
                event_id="evt-bad",
                event_type=MessageEventType.WORKFLOW_EXECUTION,
                sequence_number=1,
                payload={"not_a_domain_event": True},
                instance_id="inst-1",
            ),
            _envelope(_event(), "evt-1", sequence=2),
        ]

        await worker._consume_loop(
            _FiniteBus(messages), "workflow_events", "event_export_worker"
        )
        await _drain(worker)

        assert len(receiver.requests) == 1, "坏消息跳过、好消息照投（单条失败不中断）"
        assert worker.list_deliveries("sub-1")[0].event_id == "evt-1"

    async def test_stop_is_idempotent(self):
        worker = _worker(_registry_with(_subscription()), _FakeReceiver())
        worker.stop()
        worker.stop()  # 幂等：重复停止无副作用
