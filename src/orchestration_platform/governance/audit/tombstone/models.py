# =============================================================================
# 墓碑领域模型（governance/audit/tombstone/models.py）
#
# 职责：定义墓碑标记与被遗忘权操作的领域模型。
# 设计意图（对应偏差修正 spec 6.3 / design 2.3.2 类图）：
#   - TombstoneResult：单条墓碑标记结果
#   - ForgetResult：被遗忘权批量标记结果
#   - TombstoneBatchResult：批量墓碑标记仓储结果
#   - RejectResult：物理删除拒绝结果
# =============================================================================

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    """UTC 当前时间工厂。"""
    return datetime.now(timezone.utc)


class TombstoneResult(BaseModel):
    """单条墓碑标记结果。"""

    success: bool = Field(description="是否成功")
    entry_id: str = Field(description="审计日志条目标识")
    error_code: Optional[str] = Field(default=None, description="错误码")
    error_message: Optional[str] = Field(default=None, description="错误描述")


class ForgetResult(BaseModel):
    """被遗忘权批量标记结果。"""

    success_count: int = Field(default=0, description="成功标记条目数")
    failed_count: int = Field(default=0, description="失败条目数")
    failed_entry_ids: List[str] = Field(default_factory=list, description="失败条目标识列表")
    operation_log_id: str = Field(description="本次操作的审计日志标识")
    total_count: int = Field(default=0, description="待标记总条目数")


class TombstoneBatchResult(BaseModel):
    """批量墓碑标记仓储结果。"""

    success_count: int = Field(default=0, description="成功标记条目数")
    failed_count: int = Field(default=0, description="失败条目数")
    failed_entry_ids: List[str] = Field(default_factory=list, description="失败条目标识列表")


class RejectResult(BaseModel):
    """物理删除拒绝结果。"""

    entry_id: str = Field(description="请求物理删除的条目标识")
    reason: str = Field(default="物理删除被禁止，请使用墓碑标记", description="拒绝原因")


class TombstoneMetadataView(BaseModel):
    """墓碑记录元数据视图（仅元数据，不含原始内容）。"""

    entry_id: str = Field(description="条目标识")
    tombstone: bool = Field(description="墓碑标记")
    deleted_at: Optional[datetime] = Field(default=None, description="删除时间")
    deleted_by: Optional[str] = Field(default=None, description="操作人")
    forget_operation_id: Optional[str] = Field(default=None, description="被遗忘权操作批次标识")
    tenant_id: str = Field(description="租户标识")
    occurred_at: datetime = Field(description="原始操作时间")
