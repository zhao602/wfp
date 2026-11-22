# =============================================================================
# 执行事件模型（domain/models/execution_event.py）
#
# 职责：定义不可变执行事件——类型、关联节点、序号、载荷、作废标记。
# 设计意图（对应需求规格 6.5）：
#   事件以追加写方式记录状态变更，序号在同一实例内严格递增；
#   回滚时仅将后续事件标记 voided=True，绝不物理删除。
# =============================================================================

from datetime import datetime, timezone
from typing import Dict, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.enums import EventType


def _utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class ExecutionEvent(BaseModel):
    """执行事件（对应需求规格 6.5）。"""

    event_id: str = Field(description="事件标识（全局唯一）")
    instance_id: str = Field(description="所属实例标识")
    event_type: EventType = Field(description="事件类型")
    node_id: Optional[str] = Field(default=None, description="关联节点标识（节点相关事件必填）")
    sequence: int = Field(ge=0, description="事件序号（同一实例内严格递增；0 表示由仓储自动分配）")
    payload: Dict[str, JsonValue] = Field(default_factory=dict, description="事件载荷（禁止含密钥原文）")
    voided: bool = Field(default=False, description="是否已作废（回滚后置真，不物理删除）")
    occurred_at: datetime = Field(default_factory=_utc_now, description="事件时间")
    # ---- 第二阶段新增 ----
    branch_id: Optional[str] = Field(default=None, description="并行分支标识")
    subworkflow_id: Optional[str] = Field(default=None, description="子工作流实例标识")
    nesting_depth: Optional[int] = Field(default=None, ge=0, description="子工作流嵌套深度")
