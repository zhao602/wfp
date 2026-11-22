# =============================================================================
# 事件出口集成测试（tests/integration/test_event_export_api.py）
#
# 职责：装配级验证事件出口——路由门控 / 订阅 CRUD / 投递记录 / 死信重投 /
#       SSE 过滤与清理 / 三订阅方并挂 / 0 丢失端到端（roadmap B3 三验收）。
# 设计意图（对应 design §5-§7 测试矩阵）：
#   1. 生命周期：lifespan_context 手动驱动（startup 挂接 worker / shutdown
#      停止）；非流式端点经 ASGITransport（同环调用，异常处理器正常生效）；
#   2. SSE 不经 ASGITransport（httpx ASGI 传输对无限流整包缓冲——实测会挂起）：
#      直接调用端点函数迭代 body_iterator，同环发布/收集/断连清理；
#   3. worker http 客户端注入 MockTransport（零真实网络）；退避经环境变量
#      调小（EVENT_EXPORT_WEBHOOK_BACKOFF_BASE_SECONDS=0.001）；
#   4. 审计订阅方经 monkeypatch acquire_audit_repository 注入内存假仓储
#      （不落盘），三订阅方 = 审计 + 出口 worker + SSE 同总线并挂（验收 3）；
#   5. 0 丢失端到端：好/坏双订阅，delivered ∪ dead_lettered == 全部事件，
#      无第三态（验收 1）。
# =============================================================================

import asyncio
import json

import httpx
import pytest

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.api.routers import event_export_router
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.domain_event import DomainEvent
from orchestration_platform.domain.models.enums import MessageEventType
from orchestration_platform.governance.event_bus.models import EventMessage
from orchestration_platform.governance.event_export.models import WebhookSubscription

# 事件总线 topic（settings.kafka_workflow_topic 默认值）。
_TOPIC = "workflow_events"


# =============================================================================
# 构造辅助
# =============================================================================


def _event(
    event_type: str = "instance.succeeded",
    instance_id: str = "inst-1",
    tenant_id: str = "tenant-1",
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


def _subscription(**overrides) -> WebhookSubscription:
    params = dict(
        subscription_id="sub-1",
        tenant_id="tenant-1",
        url="http://receiver.example/hook",
        secret="s3cret",
        event_types=[],
    )
    params.update(overrides)
    return WebhookSubscription(**params)


def _make_routing_client(worker, good_urls: set) -> list:
    """为 worker 注入按 URL 分流的 MockTransport 客户端（好 URL 200，其余 500）。

    返回请求记录列表（测试断言用）。
    """
    requests: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) in good_urls:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500, json={"ok": False})

    worker._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    worker._owns_client = False
    return requests


async def _request(app, method: str, path: str, json_body=None) -> httpx.Response:
    """同环 ASGI 请求（非流式端点；异常处理器在应用栈内正常生效）。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=json_body)


async def _collect_sse(response, expected: int) -> list:
    """迭代 SSE 响应 body_iterator 收集 expected 个 data 事件后关闭（断连清理）。"""
    events: list = []
    iterator = response.body_iterator
    try:
        async for chunk in iterator:
            for line in str(chunk).splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: "):]))
                    if len(events) >= expected:
                        return events
    finally:
        await iterator.aclose()
    return events


async def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.01) -> None:
    """轮询等待异步收敛（投递/落盘完成）。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)


async def _wait_subscribers(bus, *group_ids: str) -> None:
    """等待消费组在总线上完成注册。

    startup 处理器在等待任务栈内执行，create_task 不让出循环；
    lifespan 返回后立即 publish 会先于消费任务首个调度槽，
    须先轮询 _group_queues 确认注册后再发布（消除启动竞态）。
    """
    expected = set(group_ids)
    await _wait_until(
        lambda: expected <= set(bus._group_queues.get(_TOPIC, {})),
    )


# =============================================================================
# 夹具：开关关闭 / 开启 两套应用实例（get_settings 缓存前后清理）
# =============================================================================


@pytest.fixture
def app_export_off(monkeypatch):
    """出口开关全关的应用实例（路由不注册，端点 404）。"""
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("EVENT_EXPORT_ENABLED", "false")
    monkeypatch.setenv("EVENT_EXPORT_API_ENABLED", "false")
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def app_export_on(monkeypatch, tmp_path):
    """出口开关开启的应用实例（退避调小、尝试次数 2：测试不等真实退避）。"""
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("EVENT_EXPORT_ENABLED", "true")
    monkeypatch.setenv("EVENT_EXPORT_API_ENABLED", "true")
    monkeypatch.setenv("EVENT_EXPORT_WEBHOOK_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("EVENT_EXPORT_WEBHOOK_BACKOFF_BASE_SECONDS", "0.001")
    monkeypatch.setenv("EVENT_EXPORT_WEBHOOK_BACKOFF_CAP_SECONDS", "0.01")
    # 2026-11-22 B7 W3：订阅持久化指向 tmp，防默认路径 .data/ 交叉泄漏
    monkeypatch.setenv(
        "EVENT_EXPORT_SUBSCRIPTIONS_PERSIST_PATH",
        str(tmp_path / "subscriptions.json"),
    )
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


class _FakeAuditRepository:
    """内存审计仓储（三订阅方测试：不落盘；append/get_last_hash 最小面）。"""

    def __init__(self) -> None:
        self.entries: list = []

    async def append(self, entry) -> None:
        self.entries.append(entry)

    async def get_last_hash(self):
        return ""


@pytest.fixture
def fake_audit_repository() -> _FakeAuditRepository:
    return _FakeAuditRepository()


@pytest.fixture
def app_export_audit_on(monkeypatch, tmp_path, fake_audit_repository):
    """出口 + 审计订阅双开启（审计仓储注入内存假仓储，P10 回环保留）。"""
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("EVENT_EXPORT_ENABLED", "true")
    monkeypatch.setenv("EVENT_EXPORT_API_ENABLED", "true")
    monkeypatch.setenv("AUDIT_EVENT_SUBSCRIPTION_ENABLED", "true")
    monkeypatch.setenv("EVENT_EXPORT_WEBHOOK_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("EVENT_EXPORT_WEBHOOK_BACKOFF_BASE_SECONDS", "0.001")
    monkeypatch.setenv("EVENT_EXPORT_WEBHOOK_BACKOFF_CAP_SECONDS", "0.01")
    # 2026-11-22 B7 W3：订阅持久化指向 tmp，防默认路径 .data/ 交叉泄漏
    monkeypatch.setenv(
        "EVENT_EXPORT_SUBSCRIPTIONS_PERSIST_PATH",
        str(tmp_path / "subscriptions.json"),
    )
    from orchestration_platform.governance.audit import audit_event_subscriber

    monkeypatch.setattr(
        audit_event_subscriber,
        "acquire_audit_repository",
        lambda: (fake_audit_repository, None),
    )
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


# =============================================================================
# A. 开关门控：关闭 404（INV 范式）
# =============================================================================


class TestExportFlagOff:
    async def test_flag_off_endpoints_404(self, app_export_off):
        """开关关闭：全部出口端点不注册（404），基线零变化。"""
        for path in (
            "/api/v1/event-export/subscriptions",
            "/api/v1/event-export/dead-letters",
            "/api/v1/event-export/stream",
        ):
            response = await _request(app_export_off, "GET", path)
            assert response.status_code == 404, path


# =============================================================================
# B. 订阅 CRUD（线级契约：响应不回显 secret）
# =============================================================================


class TestSubscriptionCrud:
    async def test_crud_full_cycle(self, app_export_on):
        body = {
            "subscription_id": "sub-crud",
            "tenant_id": "tenant-1",
            "url": "http://receiver.example/hook",
            "secret": "s3cret",
            "event_types": ["instance."],
        }
        response = await _request(
            app_export_on, "POST", "/api/v1/event-export/subscriptions", body
        )
        assert response.status_code == 200
        created = response.json()
        assert created["subscription_id"] == "sub-crud"
        assert created["secret_set"] is True, "响应不回显 secret（契约）"
        assert "secret" not in created

        response = await _request(
            app_export_on, "GET", "/api/v1/event-export/subscriptions"
        )
        assert [s["subscription_id"] for s in response.json()["subscriptions"]] == [
            "sub-crud"
        ]

        response = await _request(
            app_export_on, "GET", "/api/v1/event-export/subscriptions/sub-crud"
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is True

        response = await _request(
            app_export_on, "DELETE", "/api/v1/event-export/subscriptions/sub-crud"
        )
        assert response.json() == {"deleted": "sub-crud"}

        response = await _request(
            app_export_on, "GET", "/api/v1/event-export/subscriptions/sub-crud"
        )
        assert response.status_code == 404
        assert response.json()["code"] == "EVENT_SUBSCRIPTION_NOT_FOUND"

    async def test_validation_422(self, app_export_on):
        response = await _request(
            app_export_on,
            "POST",
            "/api/v1/event-export/subscriptions",
            {
                "subscription_id": "sub-bad",
                "tenant_id": "tenant-1",
                "url": "ftp://receiver.example/hook",
                "secret": "s3cret",
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "EVENT_SUBSCRIPTION_INVALID"

        response = await _request(
            app_export_on,
            "POST",
            "/api/v1/event-export/subscriptions",
            {"subscription_id": "sub-missing"},
        )
        assert response.status_code == 422, "缺必填字段 422"

    async def test_enable_disable_colon_paths(self, app_export_on):
        body = {
            "subscription_id": "sub-toggle",
            "tenant_id": "tenant-1",
            "url": "http://receiver.example/hook",
            "secret": "s3cret",
            "enabled": False,
        }
        await _request(
            app_export_on, "POST", "/api/v1/event-export/subscriptions", body
        )

        response = await _request(
            app_export_on,
            "POST",
            "/api/v1/event-export/subscriptions/sub-toggle:enable",
        )
        assert response.json()["enabled"] is True

        response = await _request(
            app_export_on,
            "POST",
            "/api/v1/event-export/subscriptions/sub-toggle:disable",
        )
        assert response.json()["enabled"] is False

        response = await _request(
            app_export_on, "POST", "/api/v1/event-export/subscriptions/missing:enable"
        )
        assert response.status_code == 404
        assert response.json()["code"] == "EVENT_SUBSCRIPTION_NOT_FOUND"


# =============================================================================
# C. 投递记录与死信端点（含 :redrive 冒号动作路径）
# =============================================================================


class TestDeliveriesAndDeadLetters:
    async def test_deliveries_endpoint(self, app_export_on):
        worker = event_export_router._worker
        registry = event_export_router._registry
        _make_routing_client(worker, {"http://receiver.example/hook"})
        registry.register(_subscription())
        await asyncio.gather(*worker.handle(_event(), event_id="evt-1"))

        async with app_export_on.router.lifespan_context(app_export_on):
            response = await _request(
                app_export_on,
                "GET",
                "/api/v1/event-export/subscriptions/sub-1/deliveries",
            )
            assert response.status_code == 200
            records = response.json()["deliveries"]
            assert len(records) == 1
            assert records[0]["last_status"] == "delivered"
            assert records[0]["event_id"] == "evt-1"

            response = await _request(
                app_export_on,
                "GET",
                "/api/v1/event-export/subscriptions/missing/deliveries",
            )
            assert response.status_code == 404

    async def test_dlq_endpoints_and_redrive(self, app_export_on):
        worker = event_export_router._worker
        registry = event_export_router._registry
        good_urls = set()
        _make_routing_client(worker, good_urls)
        registry.register(_subscription(url="http://receiver.example/hook"))
        bus = app_export_on.state.event_bus

        async with app_export_on.router.lifespan_context(app_export_on):
            await _wait_subscribers(bus, "event_export_worker")
            await bus.publish(_TOPIC, _envelope(_event(), "evt-1"))
            await _wait_until(lambda: worker.list_dead_letters())
            entry_id = worker.list_dead_letters()[0].entry_id

            response = await _request(
                app_export_on, "GET", "/api/v1/event-export/dead-letters"
            )
            dead_letters = response.json()["dead_letters"]
            assert len(dead_letters) == 1
            assert dead_letters[0]["event_id"] == "evt-1"
            assert dead_letters[0]["attempts"] == 2, "settings 注入 max_attempts=2"

            good_urls.add("http://receiver.example/hook")
            response = await _request(
                app_export_on,
                "POST",
                f"/api/v1/event-export/dead-letters/{entry_id}:redrive",
            )
            assert response.status_code == 200
            assert response.json()["redelivered"] is True

            response = await _request(
                app_export_on, "GET", "/api/v1/event-export/dead-letters"
            )
            assert response.json()["dead_letters"] == [], "重投成功移出 DLQ"

            response = await _request(
                app_export_on,
                "POST",
                "/api/v1/event-export/dead-letters/nonexistent:redrive",
            )
            assert response.status_code == 404
            assert response.json()["code"] == "EVENT_DEAD_LETTER_NOT_FOUND"


# =============================================================================
# D. SSE 流：过滤参数 + 断连清理
# =============================================================================


class TestSseStream:
    async def test_stream_filters_and_cleanup(self, app_export_on):
        bus = app_export_on.state.event_bus

        async with app_export_on.router.lifespan_context(app_export_on):
            response = await event_export_router.stream_events(
                instance_id="inst-1", event_types="instance."
            )
            assert response.media_type == "text/event-stream"
            collector = asyncio.create_task(_collect_sse(response, expected=2))
            await asyncio.sleep(0.05)  # 让 SSE 生成器完成订阅注册

            await bus.publish(
                _TOPIC, _envelope(_event(instance_id="inst-1"), "evt-1", 1)
            )
            await bus.publish(
                _TOPIC, _envelope(_event(instance_id="inst-2"), "evt-2", 2)
            )
            await bus.publish(
                _TOPIC,
                _envelope(
                    _event(instance_id="inst-1", event_type="governance.review_decision"),
                    "evt-3",
                    3,
                ),
            )
            await bus.publish(
                _TOPIC, _envelope(_event(instance_id="inst-1"), "evt-4", 4)
            )

            events = await asyncio.wait_for(collector, timeout=5.0)
            assert [e["event_id"] if "event_id" in e else e["instance_id"] for e in events] == [
                "inst-1",
                "inst-1",
            ], "instance_id 精确过滤 + event_types 前缀过滤"
            assert all(e["event_type"] == "instance.succeeded" for e in events)

            await asyncio.sleep(0.05)
            groups = bus._group_queues[_TOPIC].keys()
            assert set(groups) == {"event_export_worker"}, (
                "SSE 断连清理：per-connection 消费组已移除，worker 组保留"
            )


# =============================================================================
# E. 三订阅方并挂（roadmap 验收 3：订阅方 ≥2）
# =============================================================================


class TestThreeSubscribersFanout:
    async def test_audit_worker_sse_each_receive_all(
        self, app_export_audit_on, fake_audit_repository
    ):
        """审计订阅方 + 出口 worker + SSE 同总线并挂：publish N 事件三方各收 N。"""
        app = app_export_audit_on
        bus = app.state.event_bus
        worker = event_export_router._worker
        registry = event_export_router._registry
        requests = _make_routing_client(worker, {"http://receiver.example/hook"})
        registry.register(_subscription(event_types=["instance."]))

        async with app.router.lifespan_context(app):
            await _wait_subscribers(
                bus, "event_export_worker", "audit_event_subscriber"
            )
            sse_response = await event_export_router.stream_events(
                event_types="instance."
            )
            collector = asyncio.create_task(_collect_sse(sse_response, expected=3))
            await asyncio.sleep(0.05)

            for index in range(3):
                await bus.publish(
                    _TOPIC,
                    _envelope(
                        _event(instance_id=f"inst-{index}", trace_id=f"trace-{index}"),
                        f"evt-{index}",
                        index + 1,
                    ),
                )

            sse_events = await asyncio.wait_for(collector, timeout=5.0)
            await _wait_until(
                lambda: len(worker.list_deliveries("sub-1")) >= 3
                and len(fake_audit_repository.entries) >= 3
            )

            assert len(sse_events) == 3, "SSE 收全量（扇出实证）"
            assert {e["instance_id"] for e in sse_events} == {
                "inst-0",
                "inst-1",
                "inst-2",
            }

            records = worker.list_deliveries("sub-1")
            assert len(records) == 3, "出口 worker 收全量（audit_written 回环被过滤）"
            assert all(r.last_status == "delivered" for r in records)
            assert len(requests) == 3

            assert len(fake_audit_repository.entries) == 3, "审计订阅方收全量"


# =============================================================================
# F. 0 丢失端到端（roadmap 验收 1：断连重投 0 丢失，进程生命周期内）
# =============================================================================


class TestZeroLossEndToEnd:
    async def test_delivered_union_dead_lettered_covers_all(self, app_export_on):
        """好/坏双订阅：delivered ∪ dead_lettered == 全部事件，无第三态。"""
        app = app_export_on
        bus = app.state.event_bus
        worker = event_export_router._worker
        registry = event_export_router._registry
        good_urls = {"http://good.example/hook"}
        _make_routing_client(worker, good_urls)
        registry.register(
            _subscription(subscription_id="sub-good", url="http://good.example/hook")
        )
        registry.register(
            _subscription(subscription_id="sub-bad", url="http://bad.example/hook")
        )

        async with app.router.lifespan_context(app):
            await _wait_subscribers(bus, "event_export_worker")
            for index in range(3):
                await bus.publish(
                    _TOPIC,
                    _envelope(
                        _event(instance_id=f"inst-{index}", trace_id=f"trace-{index}"),
                        f"evt-{index}",
                        index + 1,
                    ),
                )
            await _wait_until(
                lambda: len(worker.list_deliveries("sub-good")) >= 3
                and len(worker.list_dead_letters()) >= 3
            )

            delivered_ids = {
                r.event_id for r in worker.list_deliveries("sub-good")
            }
            dead_letter_ids = {
                e.event_id for e in worker.list_dead_letters()
            }
            assert delivered_ids == {"evt-0", "evt-1", "evt-2"}, "好订阅全量 delivered"
            assert dead_letter_ids == {"evt-0", "evt-1", "evt-2"}, (
                "坏订阅重试耗尽全量 dead_lettered（attempts=2）"
            )
            bad_records = worker.list_deliveries("sub-bad")
            assert all(r.attempts == 2 for r in bad_records), (
                "退避重投：attempts 与 settings 注入一致（0 丢失重投实证）"
            )
            assert len(bad_records) + len(worker.list_dead_letters()) == 6, (
                "坏订阅每事件一个终态记录 + 一个死信条目"
            )
