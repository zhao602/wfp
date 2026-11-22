# =============================================================================
# 事件总线集成测试（tests/governance/test_event_bus_integration.py）
#
# 职责：验证事件总线、事件发布、重放恢复功能。
# 设计意图（对应偏差修正 T126 / 验收 A96-A100, B95-B97）：
#   1. 内存事件总线：publish/subscribe
#   2. 事件序列号重排序
#   3. 工作流执行事件发布
#   4. 会话事件流发布
#   5. 事件重放恢复
#   6. 降级模式
# =============================================================================

import asyncio
import unittest

from orchestration_platform.domain.models.enums import MessageEventType
from orchestration_platform.governance.event_bus.event_bus_manager import EventBusManager
from orchestration_platform.governance.event_bus.event_replay_service import (
    EventReplayService,
)
from orchestration_platform.governance.event_bus.event_sequence_reorder import (
    EventSequenceReorder,
)
from orchestration_platform.governance.event_bus.in_memory_event_bus import (
    InMemoryEventBus,
)
from orchestration_platform.governance.event_bus.models import EventMessage
from orchestration_platform.governance.event_bus.session_event_publisher import (
    SessionEventPublisher,
)
from orchestration_platform.governance.event_bus.workflow_event_publisher import (
    WorkflowEventPublisher,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_event(seq: int) -> EventMessage:
    return EventMessage(
        event_id=f"evt_{seq}",
        event_type=MessageEventType.WORKFLOW_EXECUTION,
        sequence_number=seq,
        payload={"node": f"node_{seq}"},
        instance_id="inst_1",
    )


class TestInMemoryEventBus(unittest.TestCase):
    """内存事件总线测试（验收 A96, A100）。"""

    def setUp(self) -> None:
        self.bus = InMemoryEventBus()

    def test_publish_and_replay(self) -> None:
        """发布并重放事件。"""
        _run_async(self.bus.publish("topic1", _make_event(1)))
        _run_async(self.bus.publish("topic1", _make_event(2)))
        result = _run_async(self.bus.replay_from_offset("topic1", 0, 100))
        self.assertEqual(len(result.events), 2)

    def test_is_degraded(self) -> None:
        """内存总线标记为降级。"""
        self.assertTrue(self.bus.is_degraded)


class TestEventSequenceReorder(unittest.TestCase):
    """事件序列号重排序测试（验收 A99）。"""

    def setUp(self) -> None:
        self.reorder = EventSequenceReorder()

    def test_reorder(self) -> None:
        """乱序事件按序列号排序。"""
        events = [_make_event(3), _make_event(1), _make_event(2)]
        reordered = self.reorder.reorder(events)
        self.assertEqual([e.sequence_number for e in reordered], [1, 2, 3])

    def test_detect_gaps(self) -> None:
        """检测序列号缺失区间。"""
        events = [_make_event(1), _make_event(3)]
        gaps = self.reorder.detect_gaps(events)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0], (2, 3))


class TestWorkflowEventPublisher(unittest.TestCase):
    """工作流执行事件发布测试（验收 A97）。"""

    def test_publish_execution_event(self) -> None:
        """发布工作流执行事件。"""
        bus = InMemoryEventBus()
        publisher = WorkflowEventPublisher(bus, "workflow_events")
        _run_async(publisher.publish_execution_event("inst_1", "node_1", "completed"))
        result = _run_async(bus.replay_from_offset("workflow_events", 0, 100))
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].event_type, MessageEventType.WORKFLOW_EXECUTION)


class TestSessionEventPublisher(unittest.TestCase):
    """会话事件流发布测试（验收 A98）。"""

    def test_publish_session_event(self) -> None:
        """发布会话状态变更事件。"""
        bus = InMemoryEventBus()
        publisher = SessionEventPublisher(bus, "session_events")
        _run_async(publisher.publish_session_event("sess_1", "running", "completed"))
        result = _run_async(bus.replay_from_offset("session_events", 0, 100))
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].event_type, MessageEventType.SESSION_STATE_CHANGE)


class TestEventReplayService(unittest.TestCase):
    """事件重放恢复服务测试（验收 A99）。"""

    def test_replay_and_restore(self) -> None:
        """重放恢复。"""
        bus = InMemoryEventBus()
        _run_async(bus.publish("topic1", _make_event(1)))
        _run_async(bus.publish("topic1", _make_event(2)))
        _run_async(bus.publish("topic1", _make_event(3)))

        service = EventReplayService(bus)
        result = _run_async(service.replay_and_restore("topic1", 0, 100))
        self.assertEqual(len(result.events), 3)
        self.assertFalse(result.has_gap)

    def test_replay_with_gap(self) -> None:
        """重放检测缺失区间。"""
        bus = InMemoryEventBus()
        _run_async(bus.publish("topic1", _make_event(1)))
        _run_async(bus.publish("topic1", _make_event(3)))

        service = EventReplayService(bus)
        result = _run_async(service.replay_and_restore("topic1", 0, 100))
        self.assertTrue(result.has_gap)


class TestEventBusManager(unittest.TestCase):
    """事件总线管理器测试（验收 A100, B95）。"""

    def test_degraded_mode(self) -> None:
        """未启用 Kafka 时降级为内存总线。"""
        mgr = EventBusManager()
        self.assertTrue(mgr.is_degraded)


if __name__ == "__main__":
    unittest.main()
