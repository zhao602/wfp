# =============================================================================
# 事件总线领域模型（governance/event_bus/models.py）
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import MessageEventType


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventMessage(BaseModel):
    """事件消息。"""
    event_id: str = Field(description="事件唯一标识")
    event_type: MessageEventType = Field(description="事件类型")
    sequence_number: int = Field(description="事件序列号（单调递增）")
    payload: Dict[str, Any] = Field(default_factory=dict, description="事件载荷")
    instance_id: str = Field(description="实例标识")
    occurred_at: datetime = Field(default_factory=_utc_now, description="事件时间")
    is_degraded: bool = Field(default=False, description="是否经降级内存总线发布")


class PublishResult(BaseModel):
    """事件发布结果。"""
    success: bool = Field(description="是否成功")
    offset: Optional[int] = Field(default=None, description="事件 offset")
    is_degraded: bool = Field(default=False, description="是否降级发布")


class ReplayResult(BaseModel):
    """事件重放结果。"""
    events: List[EventMessage] = Field(default_factory=list, description="重放事件列表")
    has_gap: bool = Field(default=False, description="是否存在缺失区间")
    gap_ranges: List[Tuple[int, int]] = Field(default_factory=list, description="缺失区间列表")
