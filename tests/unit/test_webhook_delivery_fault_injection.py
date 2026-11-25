# =============================================================================
# Webhook 投递故障注入套件（tests/unit/test_webhook_delivery_fault_injection.py）
#
# 职责（roadmap B14 T4「webhook 5xx」，design D4-webhook）：MockTransport
# 注入接收方 5xx，直注 WebhookDeliveryWorker 构造参数（不经 create_app/
# 环境变量），固定投递故障契约（既有 tests/integration/test_event_export_api.py
# 为装配级路由/端点测试，max_attempts=2；本套件差异化：单测级满参
# max_attempts=5 + 每次尝试 HMAC 签名复算断言 + 全链路总线场景；既有文件
# 零修改）：
#   A. 恒 500：5 次尝试耗尽 → DeliveryRecord failed（attempts=5，
#      last_error="HTTP 500"）+ DLQ（payload_snapshot 完整事件快照）；
#   B. 500→500→200：期限内 delivered 无 DLQ（退避期内自愈路径）；
#   C. redrive：好客户端重投 → 移出 DLQ + delivered + attempts 累加；
#   D. 全链路：内存总线 + attach 消费循环 → DomainEvent 发布 → 5xx →
#      DLQ → 换好客户端 redrive → delivered；
#   E. 每次尝试 SIGNATURE_HEADER 在场且签名可复算（verify_webhook 全绿，
#      接收方视角契约）。
# =============================================================================

import asyncio

import httpx

from orchestration_platform.domain.models.domain_event import DomainEvent
from orchestration_platform.domain.models.enums import MessageEventType
from orchestration_platform.governance.event_bus.in_memory_event_bus import (
    InMemoryEventBus,
)
from orchestration_platform.governance.event_bus.models import EventMessage
from orchestration_platform.governance.event_export.models import WebhookSubscription
from orchestration_platform.governance.event_export.signature import (
    SIGNATURE_HEADER,
    verify_webhook,
)
from orchestration_platform.governance.event_export.subscription_registry import (
    EventSubscriptionRegistry,
)
from orchestration_platform.governance.event_export.webhook_delivery_worker import (
    WebhookDeliveryWorker,
)

_TOPIC = "workflow_events"
_SECRET = "s3cret"


def _event() -> DomainEvent:
    return DomainEvent(
        event_type="instance.succeeded",
        instance_id="inst-1",
        tenant_id="tenant-1",
        trace_id="trace-1",
        payload={"status": "succeeded"},
    )


def _subscription(**overrides) -> WebhookSubscription:
    params = dict(
        subscription_id="sub-1",
        tenant_id="tenant-1",
        url="http://receiver.example/hook",
        secret=_SECRET,
        event_types=[],
    )
    params.update(overrides)
    return WebhookSubscription(**params)


def _recording_client(requests: list, handler) -> httpx.AsyncClient:
    """记录请求的 MockTransport 客户端（零真实网络）。"""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _make_worker(registry: EventSubscriptionRegistry, client: httpx.AsyncClient):
    return WebhookDeliveryWorker(
        registry,
        max_attempts=5,
        backoff_base_seconds=0.001,
        backoff_cap_seconds=0.01,
        http_client=client,
    )


def _assert_signed(requests: list) -> None:
    """每次尝试 SIGNATURE_HEADER 在场且签名可复算（接收方视角契约）。"""
    for request in requests:
        header_value = request.headers[SIGNATURE_HEADER]
        body = request.content.decode("utf-8")
        assert verify_webhook(_SECRET, header_value, body) is True


async def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.01):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)


# =============================================================================
# A. 恒 500：尝试耗尽 → failed 记录 + DLQ 完整快照
# =============================================================================


class TestConstant500Exhaustion:
    async def test_five_attempts_then_failed_record_and_dlq_snapshot(self):
        registry = EventSubscriptionRegistry()
        registry.register(_subscription())
        requests: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(500, json={"ok": False})

        client = _recording_client(requests, handler)
        worker = _make_worker(registry, client)
        event = _event()

        tasks = worker.handle(event)
        await asyncio.gather(*tasks)

        # 恰 max_attempts=5 次 HTTP 尝试，每次签名在场且可复算
        assert len(requests) == 5
        _assert_signed(requests)

        records = worker.list_deliveries("sub-1")
        assert len(records) == 1
        assert records[0].last_status == "failed"
        assert records[0].attempts == 5
        assert records[0].last_error == "HTTP 500"

        dead = worker.list_dead_letters()
        assert len(dead) == 1
        assert dead[0].subscription_id == "sub-1"
        assert dead[0].attempts == 5
        assert dead[0].last_error == "HTTP 500"
        assert dead[0].payload_snapshot == event.model_dump(mode="json"), (
            "死信载荷快照完整（redrive 重建事件的唯一依据）"
        )


# =============================================================================
# B. 瞬态 5xx：退避期内自愈 → delivered 无 DLQ
# =============================================================================


class TestTransient500Recovery:
    async def test_500_500_200_delivers_within_attempts(self):
        registry = EventSubscriptionRegistry()
        registry.register(_subscription())
        requests: list = []
        counter = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            counter["n"] += 1
            if counter["n"] <= 2:
                return httpx.Response(500, json={"ok": False})
            return httpx.Response(200, json={"ok": True})

        client = _recording_client(requests, handler)
        worker = _make_worker(registry, client)

        tasks = worker.handle(_event())
        await asyncio.gather(*tasks)

        assert len(requests) == 3
        _assert_signed(requests)

        records = worker.list_deliveries("sub-1")
        assert len(records) == 1
        assert records[0].last_status == "delivered"
        assert records[0].attempts == 3, "第 3 次尝试（期限内）成功"
        assert worker.list_dead_letters() == [], "自愈路径零死信"


# =============================================================================
# C. redrive：好客户端重投 → 移出 DLQ + delivered + attempts 累加
# =============================================================================


class TestRedriveRecovery:
    async def test_redrive_removes_entry_and_delivers(self):
        registry = EventSubscriptionRegistry()
        registry.register(_subscription())
        fail_requests: list = []

        def fail_handler(request: httpx.Request) -> httpx.Response:
            fail_requests.append(request)
            return httpx.Response(500, json={"ok": False})

        worker = _make_worker(registry, _recording_client(fail_requests, fail_handler))
        await asyncio.gather(*worker.handle(_event()))
        assert len(worker.list_dead_letters()) == 1

        # 换好客户端后 redrive：单次成功
        ok_requests: list = []

        def ok_handler(request: httpx.Request) -> httpx.Response:
            ok_requests.append(request)
            return httpx.Response(200, json={"ok": True})

        worker._client = _recording_client(ok_requests, ok_handler)
        worker._owns_client = False

        entry = worker.list_dead_letters()[0]
        await worker.redrive(entry.entry_id)

        assert worker.list_dead_letters() == [], "redrive 成功后移出 DLQ"
        assert len(ok_requests) == 1
        _assert_signed(ok_requests)
        assert entry.attempts == 6, "attempts 累加：5 次失败 + 1 次 redrive 成功"

        records = worker.list_deliveries("sub-1")
        assert records[-1].last_status == "delivered"
        assert records[-1].attempts == 1


# =============================================================================
# D. 全链路：内存总线 + attach → DomainEvent → 5xx → DLQ → redrive → delivered
# =============================================================================


class TestFullChainBusToDelivery:
    async def test_bus_attach_dlq_then_redrive_delivered(self):
        registry = EventSubscriptionRegistry()
        registry.register(_subscription())
        fail_requests: list = []

        def fail_handler(request: httpx.Request) -> httpx.Response:
            fail_requests.append(request)
            return httpx.Response(500, json={"ok": False})

        worker = _make_worker(
            registry, _recording_client(fail_requests, fail_handler)
        )
        bus = InMemoryEventBus()
        worker.attach(bus, _TOPIC)

        # 等消费组在总线注册（消除启动竞态）
        await _wait_until(
            lambda: "event_export_worker"
            in bus._group_queues.get(_TOPIC, {})
        )

        event = _event()
        message = EventMessage(
            event_id="evt-b14",
            event_type=MessageEventType.WORKFLOW_EXECUTION,
            sequence_number=1,
            payload={"domain_event": event.model_dump(mode="json")},
            instance_id=event.instance_id,
        )
        await bus.publish(_TOPIC, message)

        # 投递周期收敛：5 次尝试后入 DLQ
        await _wait_until(lambda: len(worker.list_dead_letters()) == 1)
        assert len(fail_requests) == 5
        _assert_signed(fail_requests)

        dead = worker.list_dead_letters()[0]
        assert dead.event_id == "evt-b14", "事件信封标识溯源"

        # 换好客户端 redrive → delivered
        ok_requests: list = []

        def ok_handler(request: httpx.Request) -> httpx.Response:
            ok_requests.append(request)
            return httpx.Response(200, json={"ok": True})

        worker._client = _recording_client(ok_requests, ok_handler)
        worker._owns_client = False
        await worker.redrive(dead.entry_id)

        assert worker.list_dead_letters() == []
        assert len(ok_requests) == 1
        _assert_signed(ok_requests)
        records = worker.list_deliveries("sub-1")
        assert records[-1].last_status == "delivered"
        assert records[-1].event_id == "evt-b14"

        worker.stop()
