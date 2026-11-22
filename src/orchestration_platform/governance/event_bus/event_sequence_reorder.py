# =============================================================================
# 事件序列号重排序（governance/event_bus/event_sequence_reorder.py）
# =============================================================================

from typing import List, Tuple

from orchestration_platform.governance.event_bus.models import EventMessage


class EventSequenceReorder:
    """事件序列号重排序器。

    按事件携带的 sequence_number 单调递增排序。
    """

    def reorder(self, events: List[EventMessage]) -> List[EventMessage]:
        """按序列号排序事件。"""
        return sorted(events, key=lambda e: e.sequence_number)

    def detect_gaps(self, events: List[EventMessage]) -> List[Tuple[int, int]]:
        """检测序列号缺失区间。

        返回缺失区间列表 [(expected_seq, actual_seq), ...]。
        """
        gaps: List[Tuple[int, int]] = []
        if not events:
            return gaps

        sorted_events = self.reorder(events)
        expected = sorted_events[0].sequence_number

        for event in sorted_events:
            if event.sequence_number > expected:
                gaps.append((expected, event.sequence_number))
            expected = event.sequence_number + 1

        return gaps
