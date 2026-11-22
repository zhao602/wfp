# =============================================================================
# 资源池领域模型（governance/resource/models.py）
#
# 职责：定义资源池与资源需求的领域模型。
# 设计意图（对应 spec 5.4、design 2.7）：
#   1. CPU/内存/并发槽位池化管理；
#   2. 配额上限与超售比控制；
#   3. 资源需求估算指导调度决策。
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import ResourcePoolType


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ResourcePool(BaseModel):
    """资源池：池化管理的计算资源。

    支持并发槽位、CPU 与内存三类资源池。
    """

    pool_id: str = Field(description="资源池唯一标识")
    tenant_id: Optional[str] = Field(
        default=None, description="所属租户（None 表示全局池）"
    )
    pool_type: ResourcePoolType = Field(description="资源池类型")
    total_capacity: int = Field(ge=1, description="总容量")
    allocated: int = Field(default=0, ge=0, description="已分配量")
    available: int = Field(ge=0, description="可用量")
    oversubscription_ratio: float = Field(
        default=1.0, ge=1.0, description="超售比（允许分配超过总容量）"
    )
    max_capacity: int = Field(ge=1, description="最大容量（含超售）")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")


class ResourceRequirement(BaseModel):
    """资源需求：实例执行所需的资源估算。

    指导调度器决策与资源池分配。
    """

    requirement_id: str = Field(description="需求唯一标识")
    instance_id: str = Field(description="关联的实例标识")
    pool_type: ResourcePoolType = Field(description="资源池类型")
    requested_amount: int = Field(ge=1, description="请求量")
    estimated_duration_seconds: Optional[int] = Field(
        default=None, ge=1, description="预估执行时长（秒）"
    )
    is_fulfilled: bool = Field(default=False, description="是否已满足")
    allocated_pool_id: Optional[str] = Field(
        default=None, description="已分配的资源池标识"
    )
