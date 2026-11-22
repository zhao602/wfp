# =============================================================================
# 事件订阅持久化测试（tests/governance/test_event_export_subscription_persistence.py）
#
# 职责：验证 B7 W3——EventSubscriptionRegistry 可选持久化（persist_path）
# 变更后原子落盘、启动期 load_from_disk() 恢复、容错降级。
# 设计意图（.codeartsdoer/specs/roadmap_b7_harvest spec W3 / design §5）：
#   1. register/delete/enable/disable 变更路径原子落盘
#      （tmp + os.replace；schema_version=1；model_dump(mode="json")）；
#   2. load_from_disk round-trip 恢复（含 enabled 状态）；
#   3. JSON 损坏 / schema_version 不识别 → 告警 + 不恢复（不崩溃）；
#   4. 文件缺失 → 静默空表；
#   5. 无 persist_path → 纯内存零行为差（不触发任何落盘调用）。
# =============================================================================

import json
import logging

import pytest

from orchestration_platform.governance.event_export.models import (
    WebhookSubscription,
)
from orchestration_platform.governance.event_export import (
    subscription_registry as registry_module,
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


def _persist_path(tmp_path) -> str:
    return str(tmp_path / "event_export" / "subscriptions.json")


# =============================================================================
# A. 变更路径原子落盘（schema_version=1；mode="json" 形状）
# =============================================================================

class TestPersistOnMutation:

    def test_register_persists_schema_shape(self, tmp_path):
        path = _persist_path(tmp_path)
        registry = EventSubscriptionRegistry(persist_path=path)

        registry.register(_subscription())
        registry.register(
            _subscription(
                subscription_id="sub-2",
                tenant_id="tenant-2",
                event_types=["node."],
            )
        )

        with open(path, "r", encoding="utf-8") as file_handle:
            payload = json.load(file_handle)
        assert payload["schema_version"] == 1
        assert len(payload["subscriptions"]) == 2
        by_id = {s["subscription_id"]: s for s in payload["subscriptions"]}
        assert by_id["sub-1"]["tenant_id"] == "tenant-1"
        assert by_id["sub-1"]["enabled"] is True
        assert isinstance(by_id["sub-1"]["created_at"], str), "mode=json ISO 字符串"
        assert by_id["sub-2"]["event_types"] == ["node."]

    def test_delete_updates_disk(self, tmp_path):
        path = _persist_path(tmp_path)
        registry = EventSubscriptionRegistry(persist_path=path)
        registry.register(_subscription())
        registry.register(_subscription(subscription_id="sub-2"))

        registry.delete("sub-1")

        with open(path, "r", encoding="utf-8") as file_handle:
            payload = json.load(file_handle)
        assert [
            s["subscription_id"] for s in payload["subscriptions"]
        ] == ["sub-2"], "删除后落盘同步收敛"

    def test_disable_state_persisted(self, tmp_path):
        path = _persist_path(tmp_path)
        registry = EventSubscriptionRegistry(persist_path=path)
        registry.register(_subscription())

        registry.disable("sub-1")

        with open(path, "r", encoding="utf-8") as file_handle:
            payload = json.load(file_handle)
        assert payload["subscriptions"][0]["enabled"] is False


# =============================================================================
# B. load_from_disk round-trip 恢复
# =============================================================================

class TestLoadRoundTrip:

    def test_load_round_trip_restores(self, tmp_path):
        path = _persist_path(tmp_path)
        source = EventSubscriptionRegistry(persist_path=path)
        source.register(_subscription())
        source.register(
            _subscription(subscription_id="sub-2", tenant_id="tenant-2")
        )
        source.disable("sub-2")

        restored = EventSubscriptionRegistry(persist_path=path)
        restored.load_from_disk()

        assert [s.subscription_id for s in restored.list()] == ["sub-1", "sub-2"]
        assert restored.get("sub-1").enabled is True
        assert restored.get("sub-2").enabled is False, "停用状态随持久化保留"
        assert restored.get("sub-2").secret == "s3cret"
        assert [
            s.subscription_id for s in restored.list(tenant_id="tenant-2")
        ] == ["sub-2"], "租户过滤恢复后语义不变"


# =============================================================================
# C. 容错降级（损坏/版本不识别/文件缺失/无路径）
# =============================================================================

class TestLoadFaultTolerance:

    def test_corrupt_file_warns_and_skips_restore(self, tmp_path, caplog):
        path = _persist_path(tmp_path)
        tmp_path.joinpath("event_export").mkdir(parents=True)
        tmp_path.joinpath("event_export", "subscriptions.json").write_text(
            "{not-valid-json", encoding="utf-8"
        )
        registry = EventSubscriptionRegistry(persist_path=path)

        with caplog.at_level(logging.WARNING):
            registry.load_from_disk()

        assert registry.list() == [], "损坏文件不恢复"
        assert "损坏" in caplog.text

    def test_unknown_schema_version_skips_restore(self, tmp_path, caplog):
        path = _persist_path(tmp_path)
        tmp_path.joinpath("event_export").mkdir(parents=True)
        tmp_path.joinpath("event_export", "subscriptions.json").write_text(
            json.dumps({"schema_version": 99, "subscriptions": []}),
            encoding="utf-8",
        )
        registry = EventSubscriptionRegistry(persist_path=path)

        with caplog.at_level(logging.WARNING):
            registry.load_from_disk()

        assert registry.list() == [], "schema_version 不识别不恢复"
        assert "损坏" in caplog.text

    def test_missing_file_silent_empty(self, tmp_path):
        registry = EventSubscriptionRegistry(
            persist_path=_persist_path(tmp_path)
        )

        registry.load_from_disk()

        assert registry.list() == []

    def test_no_persist_path_pure_memory(self, monkeypatch):
        """无 persist_path：CRUD 全走内存，零落盘调用（B3 既有行为零差）。"""

        def _boom(*args, **kwargs):
            raise AssertionError("纯内存态不应触发落盘")

        monkeypatch.setattr(registry_module.os, "replace", _boom)
        registry = EventSubscriptionRegistry()

        registry.register(_subscription())
        registry.enable("sub-1")
        registry.disable("sub-1")
        assert len(registry.list()) == 1
        registry.delete("sub-1")
        registry.load_from_disk()

        assert registry.list() == []
