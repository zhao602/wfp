# =============================================================================
# 实时数据订阅单元测试（tests/unit/test_realtime_subscription.py）
# =============================================================================

import asyncio
import pytest

from orchestration_platform.domain.models.enums import DataSourceType
from orchestration_platform.engine.realtime_subscription.models import (
    EventFilterRule,
    SubscriptionCreateRequest,
    SubscriptionStatus,
)
from orchestration_platform.engine.realtime_subscription.subscription_manager import (
    RealtimeSubscriptionManager,
)


def run_coro(coro):
    """在同步测试中驱动协程。"""
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------- 过滤规则测试 ----------

class TestEventFilterRule:
    """事件过滤规则测试。"""

    def test_eq_match(self):
        assert RealtimeSubscriptionManager._match_filter(
            {"status": "ready"}, [EventFilterRule(field_name="status", operator="eq", value="ready")]
        ) is True

    def test_eq_no_match(self):
        assert RealtimeSubscriptionManager._match_filter(
            {"status": "pending"}, [EventFilterRule(field_name="status", operator="eq", value="ready")]
        ) is False

    def test_ne_match(self):
        assert RealtimeSubscriptionManager._match_filter(
            {"status": "error"}, [EventFilterRule(field_name="status", operator="ne", value="ready")]
        ) is True

    def test_gt_match(self):
        assert RealtimeSubscriptionManager._match_filter(
            {"count": 10}, [EventFilterRule(field_name="count", operator="gt", value=5)]
        ) is True

    def test_gt_no_match(self):
        assert RealtimeSubscriptionManager._match_filter(
            {"count": 3}, [EventFilterRule(field_name="count", operator="gt", value=5)]
        ) is False

    def test_lt_match(self):
        assert RealtimeSubscriptionManager._match_filter(
            {"count": 3}, [EventFilterRule(field_name="count", operator="lt", value=5)]
        ) is True

    def test_contains_match(self):
        assert RealtimeSubscriptionManager._match_filter(
            {"message": "error: connection failed"},
            [EventFilterRule(field_name="message", operator="contains", value="error")],
        ) is True

    def test_multiple_rules_and_semantics(self):
        rules = [
            EventFilterRule(field_name="status", operator="eq", value="ready"),
            EventFilterRule(field_name="priority", operator="gt", value=5),
        ]
        assert RealtimeSubscriptionManager._match_filter(
            {"status": "ready", "priority": 8}, rules
        ) is True
        assert RealtimeSubscriptionManager._match_filter(
            {"status": "ready", "priority": 3}, rules
        ) is False

    def test_empty_rules_always_match(self):
        assert RealtimeSubscriptionManager._match_filter({"any": "data"}, []) is True


# ---------- 输入映射测试 ----------

class TestInputMapping:
    """输入参数映射测试。"""

    def test_simple_mapping(self):
        inputs = RealtimeSubscriptionManager._map_inputs(
            {"user_id": 123, "action": "login"},
            {"user_id": "user_id", "event_type": "action"},
        )
        assert inputs == {"user_id": 123, "event_type": "login"}

    def test_nested_path_mapping(self):
        inputs = RealtimeSubscriptionManager._map_inputs(
            {"user": {"profile": {"name": "Alice"}}},
            {"username": "user.profile.name"},
        )
        assert inputs == {"username": "Alice"}

    def test_missing_field_returns_none(self):
        inputs = RealtimeSubscriptionManager._map_inputs(
            {"a": 1},
            {"b": "b"},
        )
        assert inputs == {"b": None}

    def test_empty_mapping(self):
        inputs = RealtimeSubscriptionManager._map_inputs({"a": 1}, {})
        assert inputs == {}


# ---------- 订阅管理器测试 ----------

class TestSubscriptionManager:
    """订阅管理器测试。"""

    @staticmethod
    async def _mock_trigger(definition_id: str, version: int, inputs: dict) -> str:
        return f"instance_{definition_id}_{len(inputs)}"

    @staticmethod
    async def _mock_listener(connection_ref: str, event_source: str):
        return
        yield

    def _make_manager(self):
        manager = RealtimeSubscriptionManager(self._mock_trigger)
        manager.register_event_listener(DataSourceType.KAFKA.value, self._mock_listener)
        return manager

    def test_create_subscription(self):
        manager = self._make_manager()
        request = SubscriptionCreateRequest(
            workflow_definition_id="wf_001",
            workflow_definition_version=1,
            source_type=DataSourceType.KAFKA,
            connection_reference="broker:9092",
            event_source="test_topic",
            input_mapping={"event_data": "raw"},
        )
        sub = run_coro(manager.create_subscription(request))
        assert sub.workflow_definition_id == "wf_001"
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.trigger_count == 0

    def test_get_subscription(self):
        manager = self._make_manager()
        request = SubscriptionCreateRequest(
            workflow_definition_id="wf_001",
            source_type=DataSourceType.KAFKA,
            connection_reference="broker:9092",
            event_source="test_topic",
        )
        sub = run_coro(manager.create_subscription(request))
        found = manager.get_subscription(sub.subscription_id)
        assert found is not None
        assert found.subscription_id == sub.subscription_id

    def test_get_nonexistent_subscription(self):
        manager = self._make_manager()
        assert manager.get_subscription("nonexistent") is None

    def test_list_subscriptions(self):
        manager = self._make_manager()
        for i in range(3):
            request = SubscriptionCreateRequest(
                workflow_definition_id=f"wf_{i}",
                source_type=DataSourceType.KAFKA,
                connection_reference="broker:9092",
                event_source=f"topic_{i}",
            )
            run_coro(manager.create_subscription(request))
        subs = manager.list_subscriptions()
        assert len(subs) == 3

    def test_list_subscriptions_with_status_filter(self):
        manager = self._make_manager()
        request = SubscriptionCreateRequest(
            workflow_definition_id="wf_001",
            source_type=DataSourceType.KAFKA,
            connection_reference="broker:9092",
            event_source="test_topic",
        )
        sub = run_coro(manager.create_subscription(request))
        run_coro(manager.pause_subscription(sub.subscription_id))
        active_subs = manager.list_subscriptions(status_filter=SubscriptionStatus.ACTIVE)
        paused_subs = manager.list_subscriptions(status_filter=SubscriptionStatus.PAUSED)
        assert len(active_subs) == 0
        assert len(paused_subs) == 1

    def test_pause_and_resume_subscription(self):
        manager = self._make_manager()
        request = SubscriptionCreateRequest(
            workflow_definition_id="wf_001",
            source_type=DataSourceType.KAFKA,
            connection_reference="broker:9092",
            event_source="test_topic",
        )
        sub = run_coro(manager.create_subscription(request))
        paused = run_coro(manager.pause_subscription(sub.subscription_id))
        assert paused.status == SubscriptionStatus.PAUSED
        resumed = run_coro(manager.resume_subscription(sub.subscription_id))
        assert resumed.status == SubscriptionStatus.ACTIVE

    def test_pause_nonexistent_raises(self):
        manager = self._make_manager()
        with pytest.raises(ValueError):
            run_coro(manager.pause_subscription("nonexistent"))

    def test_delete_subscription(self):
        manager = self._make_manager()
        request = SubscriptionCreateRequest(
            workflow_definition_id="wf_001",
            source_type=DataSourceType.KAFKA,
            connection_reference="broker:9092",
            event_source="test_topic",
        )
        sub = run_coro(manager.create_subscription(request))
        run_coro(manager.delete_subscription(sub.subscription_id))
        assert manager.get_subscription(sub.subscription_id) is None

    def test_trigger_history_initially_empty(self):
        manager = self._make_manager()
        assert manager.get_trigger_history() == []
