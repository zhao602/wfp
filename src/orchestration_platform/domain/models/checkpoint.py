# =============================================================================
# 检查点模型（domain/models/checkpoint.py）
#
# 职责：定义检查点——位点信息、共享状态快照、执行轮次、可用性标记。
# 设计意图（对应需求规格 6.4）：
#   检查点是故障恢复与回滚的状态锚点；每个节点完成后生成一个检查点；
#   反序列化失败时标记 is_available=False 并回退更早检查点。
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.shared_state import SharedState


def _utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class Checkpoint(BaseModel):
    """检查点（对应需求规格 6.4）。"""

    checkpoint_id: str = Field(description="检查点标识（全局唯一）")
    instance_id: str = Field(description="所属实例标识")
    completed_node_id: Optional[str] = Field(default=None, description="已完成节点标识")
    next_node_id: Optional[str] = Field(default=None, description="下一个待执行节点标识")
    state_snapshot: SharedState = Field(description="共享状态快照（可反序列化）")
    round_index: int = Field(ge=0, description="执行轮次（同一实例内唯一且递增）")
    created_at: datetime = Field(default_factory=_utc_now, description="生成时间")
    is_available: bool = Field(default=True, description="是否可用（反序列化失败时置 False）")
    unavailable_reason: Optional[str] = Field(default=None, description="不可用原因")
    # ---- 第二阶段新增 ----
    branch_id: Optional[str] = Field(default=None, description="并行分支标识（并行分支检查点专用）")
    compression_meta: Optional["CompressionMeta"] = Field(
        default=None, description="压缩元数据（压缩启用时记录算法与压缩率）"
    )


class CompressionMeta(BaseModel):
    """检查点压缩元数据。"""

    algorithm: str = Field(description="压缩算法名称")
    original_size_bytes: int = Field(ge=0, description="原始体积（字节）")
    compressed_size_bytes: int = Field(ge=0, description="压缩后体积（字节）")
    compression_ratio: float = Field(ge=0.0, description="压缩率（压缩后/原始）")


class CheckpointSummary(BaseModel):
    """检查点摘要：用于列表查询，不含完整状态快照以减小载荷。"""

    checkpoint_id: str = Field(description="检查点标识")
    instance_id: str = Field(description="所属实例标识")
    completed_node_id: Optional[str] = Field(default=None, description="已完成节点标识")
    next_node_id: Optional[str] = Field(default=None, description="下一待执行节点标识")
    round_index: int = Field(description="执行轮次")
    created_at: datetime = Field(description="生成时间")
    is_available: bool = Field(description="是否可用")
