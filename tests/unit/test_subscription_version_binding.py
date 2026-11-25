# =============================================================================
# 订阅版本钉住与密钥轮换单测（tests/unit/test_subscription_version_binding.py）
#
# 覆盖：B16 F5 订阅管理面（design D5/D6）——
#   A. WebhookSubscription 模型：+4 字段默认值 / 钉版本无定义拒绝（模型级
#      独立防线）/ definition_id 空串归一 / version 下界；
#   B. EventSubscriptionRegistry.rotate_secret：secret 原子替换 +
#      secret_version 递增 + rotated_at 随行 / 未注册 KeyError / 持久化落盘；
#   C. WebhookDeliveryWorker._match_subscriptions 匹配矩阵（D5 顺序）：
#      enabled → tenant → definition 钉住 → version 钉住 → event_types；
#   D. 持久化兼容：旧文件（无新字段）恢复默认值 / 新字段往返；
#   E. 路由级：注册 422（version 无 id）/ 轮换端点一次性回显 / 404；
#   F. 发布点富化：_publish_instance_domain_event 单点 definition_id/
#      definition_version setdefault（订阅匹配的事件侧来源）。
# =============================================================================

import json
from typing import Any, Dict, List, Optional

import pytest
from pydantic import ValidationError

from orchestration_platform.domain.errors import (
    EVENT_SUBSCRIPTION_INVALID,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.domain_event import DomainEvent
from orchestration_platform.governance.event_export.models import WebhookSubscription
from orchestration_platform.governance.event_export.subscription_registry import (
    EventSubscriptionRegistry,
)
from orchestration_platform.governance.event_export.webhook_delivery_worker import (
    WebhookDeliveryWorker,
)


# =============================================================================
# 构造辅助
# =============================================================================


def _subscription(**overrides: Any) -> WebhookSubscription:
    params: Dict[str, Any] = dict(
        subscription_id="sub-1",
        tenant_id="tenant-1",
        url="http://receiver.example/hook",
        secret="s3cret",
        event_types=[],
    )
    params.update(overrides)
    return WebhookSubscription(**params)


def _event(
    payload: Optional[Dict[str, Any]] = None,
    *,
    event_type: str = "instance.succeeded",
    tenant_id: str = "tenant-1",
) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        instance_id="inst-1",
        tenant_id=tenant_id,
        trace_id="trace-1",
        payload=dict(payload or {}),
    )


# =============================================================================
# A. 模型：+4 字段与校验
# =============================================================================


class TestSubscriptionModel:
    def test_new_fields_defaults(self):
        """+4 字段缺省：未钉住 / secret_version=1 / 未轮换。"""
        subscription = _subscription()
        assert subscription.definition_id is None
        assert subscription.definition_version is None
        assert subscription.secret_version == 1
        assert subscription.rotated_at is None

    def test_version_without_definition_rejected(self):
        """钉版本必先钉定义（模型级独立防线，路由层映射 422）。"""
        with pytest.raises(ValidationError) as exc_info:
            _subscription(definition_version=3)
        assert "definition_version 需与 definition_id 同时提供" in str(
            exc_info.value
        )

    def test_definition_with_version_valid(self):
        subscription = _subscription(
            definition_id="flow_x", definition_version=3
        )
        assert subscription.definition_id == "flow_x"
        assert subscription.definition_version == 3

    def test_empty_definition_id_normalizes_to_none(self):
        """空串 definition_id 归一 None（未钉住）。"""
        subscription = _subscription(definition_id="  ")
        assert subscription.definition_id is None
        # 归一后携带 version 仍拒绝（None 语义即未钉住）
        with pytest.raises(ValidationError):
            _subscription(definition_id="", definition_version=2)

    def test_version_lower_bound(self):
        with pytest.raises(ValidationError):
            _subscription(definition_id="flow_x", definition_version=0)


# =============================================================================
# B. 注册表轮换
# =============================================================================


class TestRotateSecret:
    def test_rotate_replaces_secret_and_bumps_version(self):
        registry = EventSubscriptionRegistry()
        registry.register(_subscription())
        before = registry.get("sub-1")

        rotated = registry.rotate_secret("sub-1", "new-secret-1")

        assert rotated.secret == "new-secret-1"
        assert rotated.secret_version == before.secret_version + 1 == 2
        assert rotated.rotated_at is not None
        assert registry.get("sub-1").secret == "new-secret-1"

    def test_rotate_twice_increments_progressively(self):
        registry = EventSubscriptionRegistry()
        registry.register(_subscription())
        registry.rotate_secret("sub-1", "new-secret-1")
        rotated = registry.rotate_secret("sub-1", "new-secret-2")

        assert rotated.secret == "new-secret-2"
        assert rotated.secret_version == 3

    def test_rotate_unknown_subscription_raises_key_error(self):
        registry = EventSubscriptionRegistry()
        with pytest.raises(KeyError):
            registry.rotate_secret("no-such", "whatever")

    def test_rotate_persists_to_disk(self, tmp_path):
        persist_path = tmp_path / "subscriptions.json"
        registry = EventSubscriptionRegistry(persist_path=str(persist_path))
        registry.register(_subscription())
        rotated = registry.rotate_secret("sub-1", "persisted-secret")

        payload = json.loads(persist_path.read_text(encoding="utf-8"))
        assert payload["subscriptions"][0]["secret"] == "persisted-secret"
        assert payload["subscriptions"][0]["secret_version"] == 2
        assert payload["subscriptions"][0]["rotated_at"] is not None

        restored = EventSubscriptionRegistry(persist_path=str(persist_path))
        restored.load_from_disk()
        assert restored.get("sub-1").secret_version == rotated.secret_version


# =============================================================================
# C. worker 匹配矩阵（design D5 顺序）
# =============================================================================


class TestMatchMatrix:
    def _worker(self, *subscriptions: WebhookSubscription) -> WebhookDeliveryWorker:
        registry = EventSubscriptionRegistry()
        for subscription in subscriptions:
            registry.register(subscription)
        return WebhookDeliveryWorker(registry)

    def _matched_ids(
        self, worker: WebhookDeliveryWorker, event: DomainEvent
    ) -> List[str]:
        return [
            subscription.subscription_id
            for subscription in worker._match_subscriptions(event)
        ]

    def test_unpinned_matches_with_and_without_payload(self):
        """未钉住订阅匹配全部（向后兼容：载荷有无 definition 皆可）。"""
        worker = self._worker(_subscription())
        assert self._matched_ids(worker, _event()) == ["sub-1"]
        assert (
            self._matched_ids(
                worker, _event({"definition_id": "flow_x", "definition_version": 3})
            )
            == ["sub-1"]
        )

    def test_definition_pin_requires_payload_equality(self):
        worker = self._worker(_subscription(definition_id="flow_x"))
        assert self._matched_ids(worker, _event({"definition_id": "flow_x"})) == [
            "sub-1"
        ]
        assert self._matched_ids(worker, _event({"definition_id": "flow_y"})) == []
        assert self._matched_ids(worker, _event()) == [], "载荷缺失不匹配"

    def test_version_pin_requires_payload_version_equality(self):
        worker = self._worker(
            _subscription(definition_id="flow_x", definition_version=3)
        )
        assert (
            self._matched_ids(
                worker, _event({"definition_id": "flow_x", "definition_version": 3})
            )
            == ["sub-1"]
        )
        assert (
            self._matched_ids(
                worker, _event({"definition_id": "flow_x", "definition_version": 4})
            )
            == []
        )
        assert self._matched_ids(worker, _event({"definition_id": "flow_x"})) == [], (
            "钉版本而载荷无版本不匹配"
        )

    def test_version_pin_coerces_numeric_string(self):
        """载荷版本数字字符串宽容转换（JSON 传输形态防御）。"""
        worker = self._worker(
            _subscription(definition_id="flow_x", definition_version=3)
        )
        assert (
            self._matched_ids(
                worker, _event({"definition_id": "flow_x", "definition_version": "3"})
            )
            == ["sub-1"]
        )

    def test_version_pin_rejects_garbage_payload_version(self):
        worker = self._worker(
            _subscription(definition_id="flow_x", definition_version=3)
        )
        assert (
            self._matched_ids(
                worker, _event({"definition_id": "flow_x", "definition_version": "abc"})
            )
            == []
        )

    def test_pin_combined_with_tenant_event_types_enabled(self):
        """钉住与其余四过滤叠加（disabled/租户/事件类型仍先决）。"""
        worker = self._worker(
            _subscription(
                subscription_id="sub-full",
                definition_id="flow_x",
                definition_version=3,
                event_types=["instance."],
            ),
            _subscription(subscription_id="sub-disabled", enabled=False),
            _subscription(subscription_id="sub-other-tenant", tenant_id="tenant-2"),
        )
        event = _event(
            {"definition_id": "flow_x", "definition_version": 3},
            event_type="instance.started",
        )
        assert self._matched_ids(worker, event) == ["sub-full"]

        other_type = _event(
            {"definition_id": "flow_x", "definition_version": 3},
            event_type="governance.review_decision",
        )
        assert self._matched_ids(worker, other_type) == [], "event_types 过滤仍生效"


# =============================================================================
# D. 持久化兼容（旧文件恢复 / 新字段往返）
# =============================================================================


class TestPersistenceCompatibility:
    def test_legacy_file_restores_with_defaults(self, tmp_path):
        """旧持久化文件（无新字段）恢复：未钉住 / secret_version=1 / 未轮换。"""
        persist_path = tmp_path / "subscriptions.json"
        legacy_payload = {
            "schema_version": 1,
            "subscriptions": [
                {
                    "subscription_id": "sub-legacy",
                    "tenant_id": "tenant-1",
                    "url": "http://receiver.example/hook",
                    "secret": "legacy-secret",
                    "event_types": ["node."],
                    "enabled": True,
                    "description": "旧条目",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
        }
        persist_path.write_text(
            json.dumps(legacy_payload, ensure_ascii=False), encoding="utf-8"
        )

        registry = EventSubscriptionRegistry(persist_path=str(persist_path))
        registry.load_from_disk()

        restored = registry.get("sub-legacy")
        assert restored.definition_id is None
        assert restored.definition_version is None
        assert restored.secret_version == 1
        assert restored.rotated_at is None

    def test_new_fields_round_trip(self, tmp_path):
        persist_path = tmp_path / "subscriptions.json"
        registry = EventSubscriptionRegistry(persist_path=str(persist_path))
        registry.register(
            _subscription(
                subscription_id="sub-pinned",
                definition_id="flow_x",
                definition_version=3,
            )
        )
        registry.rotate_secret("sub-pinned", "rotated-secret")

        restored = EventSubscriptionRegistry(persist_path=str(persist_path))
        restored.load_from_disk()

        subscription = restored.get("sub-pinned")
        assert subscription.definition_id == "flow_x"
        assert subscription.definition_version == 3
        assert subscription.secret_version == 2
        assert subscription.rotated_at is not None


# =============================================================================
# E. 路由级（直接函数调用 + 注册表注入）
# =============================================================================


@pytest.fixture
def router_registry(monkeypatch):
    """路由模块注册表注入（隔离模块级上下文）。"""
    from orchestration_platform.api.routers import event_export_router

    registry = EventSubscriptionRegistry()
    monkeypatch.setattr(event_export_router, "_registry", registry)
    return registry


class TestRouterLevel:
    async def test_register_with_pin_fields_round_trip(self, router_registry):
        from orchestration_platform.api.routers.event_export_router import (
            SubscriptionBody,
            register_subscription,
        )

        body = SubscriptionBody(
            subscription_id="sub-pin",
            tenant_id="tenant-1",
            url="http://receiver.example/hook",
            secret="s3cret",
            definition_id="flow_x",
            definition_version=3,
        )
        result = await register_subscription(body)

        assert result["definition_id"] == "flow_x"
        assert result["definition_version"] == 3
        assert result["secret_version"] == 1
        assert "secret" not in result
        assert result["secret_set"] is True

    async def test_register_version_without_definition_422(self, router_registry):
        """路由层 422 映射：EVENT_SUBSCRIPTION_INVALID。"""
        from orchestration_platform.api.routers.event_export_router import (
            SubscriptionBody,
            register_subscription,
        )

        body = SubscriptionBody(
            subscription_id="sub-bad",
            tenant_id="tenant-1",
            url="http://receiver.example/hook",
            secret="s3cret",
            definition_version=3,
        )
        with pytest.raises(WorkflowValidationError) as exc_info:
            await register_subscription(body)
        assert exc_info.value.code == EVENT_SUBSCRIPTION_INVALID

    async def test_rotate_endpoint_one_time_secret_reveal(self, router_registry):
        from orchestration_platform.api.routers.event_export_router import (
            rotate_subscription_secret,
        )

        router_registry.register(_subscription())
        result = await rotate_subscription_secret("sub-1")

        assert result["secret_version"] == 2
        assert result["rotated_at"] is not None
        assert isinstance(result["secret"], str) and result["secret"]
        assert result["secret"] != "s3cret", "新密钥服务端生成（非回显旧值）"
        assert router_registry.get("sub-1").secret == result["secret"]

    async def test_rotate_unknown_subscription_404(self, router_registry):
        from orchestration_platform.api.routers.event_export_router import (
            rotate_subscription_secret,
        )

        with pytest.raises(Exception) as exc_info:
            await rotate_subscription_secret("no-such")
        assert "订阅不存在" in str(exc_info.value)


# =============================================================================
# F. 发布点富化（_publish_instance_domain_event，design D5 事件侧来源）
# =============================================================================


class _RecordingPublisher:
    def __init__(self) -> None:
        self.events: List[DomainEvent] = []

    async def publish_domain_event(self, event: DomainEvent) -> None:
        self.events.append(event)


class TestPublishEnrichment:
    async def test_payload_enriched_from_runtime(self, monkeypatch):
        import orchestration_platform.application.execution_runner as runner_mod

        publisher = _RecordingPublisher()
        monkeypatch.setattr(
            runner_mod, "_current_domain_event_publisher", lambda: publisher
        )
        rt = runner_mod.InstanceRuntime(
            "inst-1", "flow_x", definition_version=3
        )

        await runner_mod._publish_instance_domain_event(
            rt, "trace-1", "instance.succeeded", {"node_count_summary": 2}
        )

        assert len(publisher.events) == 1
        payload = publisher.events[0].payload
        assert payload["definition_id"] == "flow_x"
        assert payload["definition_version"] == 3
        assert payload["node_count_summary"] == 2

    async def test_explicit_payload_keys_not_clobbered(self, monkeypatch):
        """setdefault 语义：调用方显式键优先。"""
        import orchestration_platform.application.execution_runner as runner_mod

        publisher = _RecordingPublisher()
        monkeypatch.setattr(
            runner_mod, "_current_domain_event_publisher", lambda: publisher
        )
        rt = runner_mod.InstanceRuntime(
            "inst-1", "flow_x", definition_version=3
        )

        await runner_mod._publish_instance_domain_event(
            rt,
            "trace-1",
            "instance.started",
            {"definition_id": "explicit-flow", "definition_version": 9},
        )

        payload = publisher.events[0].payload
        assert payload["definition_id"] == "explicit-flow"
        assert payload["definition_version"] == 9

    async def test_version_omitted_when_runtime_has_none(self, monkeypatch):
        import orchestration_platform.application.execution_runner as runner_mod

        publisher = _RecordingPublisher()
        monkeypatch.setattr(
            runner_mod, "_current_domain_event_publisher", lambda: publisher
        )
        rt = runner_mod.InstanceRuntime("inst-1", "flow_x", definition_version=None)

        await runner_mod._publish_instance_domain_event(
            rt, "trace-1", "instance.failed", {}
        )

        payload = publisher.events[0].payload
        assert payload["definition_id"] == "flow_x"
        assert "definition_version" not in payload

    async def test_no_publisher_noop(self, monkeypatch):
        import orchestration_platform.application.execution_runner as runner_mod

        monkeypatch.setattr(
            runner_mod, "_current_domain_event_publisher", lambda: None
        )
        rt = runner_mod.InstanceRuntime("inst-1", "flow_x")
        await runner_mod._publish_instance_domain_event(
            rt, "trace-1", "instance.succeeded", {}
        )
