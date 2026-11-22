# =============================================================================
# 事件出口核心单测（tests/unit/test_event_export_core.py）
#
# 职责：订阅注册表 / 领域模型校验 / HMAC 签名 / 事件类型匹配（roadmap B3）。
# 设计意图（对应 design §3.1-§3.3）：
#   1. registry CRUD/upsert/KeyError（路由层 404 映射依据）；
#   2. models 线级契约校验拒绝（url scheme/非空/event_types 条目）；
#   3. 签名正反例（篡改 body/secret/时间戳容差/格式）；
#   4. last_error 截断（GK-05）。
# =============================================================================

import pytest

from orchestration_platform.domain.models.domain_event import DomainEvent
from orchestration_platform.governance.event_export.models import (
    DeadLetterEntry,
    DeliveryRecord,
    WebhookSubscription,
    event_type_matches,
)
from orchestration_platform.governance.event_export.signature import (
    canonical_event_body,
    sign_webhook,
    verify_webhook,
)
from orchestration_platform.governance.event_export.subscription_registry import (
    EventSubscriptionRegistry,
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


# =============================================================================
# A. 订阅注册表
# =============================================================================


class TestEventSubscriptionRegistry:
    def test_register_upsert_and_get(self):
        registry = EventSubscriptionRegistry()
        first = registry.register(_subscription(description="v1"))
        assert registry.get("sub-1").description == "v1"
        registry.register(_subscription(description="v2"))
        assert registry.get("sub-1").description == "v2"
        assert first.description == "v1", "upsert 不改既有对象（替换注册表项）"

    def test_get_missing_raises_key_error(self):
        registry = EventSubscriptionRegistry()
        with pytest.raises(KeyError):
            registry.get("missing")

    def test_list_filters_by_tenant(self):
        registry = EventSubscriptionRegistry()
        registry.register(_subscription(subscription_id="sub-a", tenant_id="t1"))
        registry.register(_subscription(subscription_id="sub-b", tenant_id="t2"))
        assert {s.subscription_id for s in registry.list()} == {"sub-a", "sub-b"}
        assert [s.subscription_id for s in registry.list(tenant_id="t1")] == ["sub-a"]

    def test_delete(self):
        registry = EventSubscriptionRegistry()
        registry.register(_subscription())
        removed = registry.delete("sub-1")
        assert removed.subscription_id == "sub-1"
        with pytest.raises(KeyError):
            registry.delete("sub-1")

    def test_enable_disable(self):
        registry = EventSubscriptionRegistry()
        registry.register(_subscription(enabled=False))
        assert registry.enable("sub-1").enabled is True
        assert registry.get("sub-1").enabled is True
        assert registry.disable("sub-1").enabled is False
        with pytest.raises(KeyError):
            registry.enable("missing")


# =============================================================================
# B. 事件类型匹配（event_types 契约语义）
# =============================================================================


class TestEventTypeMatches:
    def test_match_semantics(self):
        assert event_type_matches([], "node.started") is True, "空=全部"
        assert event_type_matches(["node."], "node.started") is True, "前缀通配"
        assert event_type_matches(["node."], "instance.succeeded") is False
        assert event_type_matches(["instance.succeeded"], "instance.succeeded") is True
        assert event_type_matches(["instance.succeeded"], "instance.failed") is False
        assert (
            event_type_matches(["node.", "instance.failed"], "instance.failed") is True
        ), "多模式任一命中"


# =============================================================================
# C. 模型校验拒绝（线级契约）
# =============================================================================


class TestWebhookSubscriptionValidation:
    def test_url_must_be_http_or_https(self):
        with pytest.raises(ValueError, match="http/https"):
            _subscription(url="ftp://receiver.example/hook")

    def test_empty_secret_rejected(self):
        with pytest.raises(ValueError):
            _subscription(secret="   ")

    def test_empty_event_type_entry_rejected(self):
        with pytest.raises(ValueError):
            _subscription(event_types=["node.", "  "])


class TestRecordTruncation:
    def test_long_error_truncated_to_summary_limit(self):
        record = DeliveryRecord(
            subscription_id="sub-1",
            event_id="evt-1",
            event_type="instance.succeeded",
            instance_id="inst-1",
            attempts=1,
            last_status="failed",
            last_error="x" * 2000,
        )
        assert len(record.last_error) <= 519, "GK-05：截断 + 截断标记"
        assert record.last_error.endswith("...[truncated]")

    def test_dead_letter_entry_error_truncated(self):
        entry = DeadLetterEntry(
            entry_id="dlq-1",
            subscription_id="sub-1",
            event_id="evt-1",
            event_type="instance.succeeded",
            instance_id="inst-1",
            attempts=3,
            last_error="y" * 2000,
            payload_snapshot={},
        )
        assert len(entry.last_error) <= 519
        assert entry.last_error.endswith("...[truncated]")


# =============================================================================
# D. 签名（HMAC-SHA256）
# =============================================================================


def _event() -> DomainEvent:
    return DomainEvent(
        event_type="instance.succeeded",
        instance_id="inst-1",
        tenant_id="tenant-1",
        trace_id="trace-1",
        payload={"status": "succeeded"},
    )


class TestCanonicalBody:
    def test_deterministic_serialization(self):
        body = canonical_event_body(_event())
        assert body == canonical_event_body(_event()), "同事件确定性序列化"
        assert '"event_type":"instance.succeeded"' in body.replace(", ", ","), (
            "sort_keys + 紧凑分隔符口径"
        )


class TestWebhookSignature:
    def test_sign_and_verify_roundtrip(self):
        body = canonical_event_body(_event())
        header = sign_webhook("s3cret", 1700000000, body)
        assert verify_webhook("s3cret", header, body, now=1700000100.0) is True

    def test_tampered_body_rejected(self):
        body = canonical_event_body(_event())
        header = sign_webhook("s3cret", 1700000000, body)
        assert verify_webhook("s3cret", header, body + "x", now=1700000100.0) is False

    def test_wrong_secret_rejected(self):
        body = canonical_event_body(_event())
        header = sign_webhook("s3cret", 1700000000, body)
        assert verify_webhook("other", header, body, now=1700000100.0) is False

    def test_expired_timestamp_rejected(self):
        body = canonical_event_body(_event())
        header = sign_webhook("s3cret", 1700000000, body)
        assert (
            verify_webhook("s3cret", header, body, tolerance_seconds=300.0, now=1700000400.0)
            is False
        ), "超出容差窗口拒收（防重放）"

    def test_malformed_header_rejected(self):
        body = canonical_event_body(_event())
        assert verify_webhook("s3cret", "garbage", body) is False
        assert verify_webhook("s3cret", "t=abc,v1=def", body) is False
        assert verify_webhook("s3cret", "t=1700000000", body) is False
