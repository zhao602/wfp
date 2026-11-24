# =============================================================================
# 事件重放恢复服务（governance/event_bus/event_replay_service.py）
# =============================================================================

import logging

from orchestration_platform.governance.event_bus.abstractions.event_bus import IEventBus
from orchestration_platform.governance.event_bus.event_sequence_reorder import (
    EventSequenceReorder,
)
from orchestration_platform.governance.event_bus.models import ReplayResult

_logger = logging.getLogger(__name__)


class EventReplayService:
    """事件重放恢复服务。

    从 topic 按 offset 重放历史事件恢复实例状态。
    """

    def __init__(self, event_bus: IEventBus) -> None:
        self._bus = event_bus
        self._reorder = EventSequenceReorder()

    async def replay_and_restore(
        self,
        topic: str,
        offset: int,
        max_events: int = 10000,
    ) -> ReplayResult:
        """从 topic 重放历史事件。

        流程：重放→重排序→检测缺失区间。
        异常映射：REPLAY_GAP_DETECTED（事件缺失区间）。
        """
        replay_result = await self._bus.replay_from_offset(topic, offset, max_events)

        if not replay_result.events:
            return ReplayResult(events=[], has_gap=False)

        # 按序列号重排序
        reordered = self._reorder.reorder(replay_result.events)

        # 检测缺失区间
        gaps = self._reorder.detect_gaps(reordered)
        has_gap = len(gaps) > 0

        if has_gap:
            _logger.warning("事件重放检测到缺失区间: %s", gaps)

        return ReplayResult(events=reordered, has_gap=has_gap, gap_ranges=gaps)
