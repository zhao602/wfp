# =============================================================================
# 租户领域模型（governance/tenant/models.py）
#
# 职责：定义租户、租户配额、组织节点、用户与访问令牌的领域模型。
# 设计意图（对应 spec 5.1、design 2.4.2）：
#   1. 多租户数据隔离，每条数据归属一个租户；
#   2. 租户配额限制资源使用上限；
#   3. 组织树支持层级管理，用户挂载于组织节点。
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import (
    TenantStatus,
    TokenStatus,
    UserStatus,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(BaseModel):
    """租户：资源隔离与配额管理的基本单位。"""

    tenant_id: str = Field(description="租户唯一标识")
    tenant_name: str = Field(description="租户显示名称")
    status: TenantStatus = Field(default=TenantStatus.ACTIVE, description="租户状态")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")
    is_default: bool = Field(default=False, description="是否默认租户")


class TenantQuota(BaseModel):
    """租户配额：限制租户的资源使用上限。"""

    tenant_id: str = Field(description="所属租户标识")
    max_instances: int = Field(default=100, ge=0, description="最大并发实例数")
    max_workflows: int = Field(default=1000, ge=0, description="最大工作流定义数")
    max_tokens_per_day: Optional[int] = Field(
        default=None, ge=0, description="每日 Token 上限（None 表示不限）"
    )
    max_storage_gb: Optional[int] = Field(
        default=None, ge=0, description="存储上限 GB（None 表示不限）"
    )
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")


class OrgNode(BaseModel):
    """组织节点：组织树的节点。

    组织树支持多层级，每个节点归属一个租户。
    """

    org_node_id: str = Field(description="节点唯一标识")
    tenant_id: str = Field(description="所属租户标识")
    node_name: str = Field(description="节点显示名称")
    parent_node_id: Optional[str] = Field(
        default=None, description="父节点标识（None 表示根节点）"
    )
    node_path: str = Field(description="节点路径（如 /root/dept1/team1）")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")


class User(BaseModel):
    """用户：平台使用者。

    用户挂载于组织节点，通过角色绑定获得权限。
    """

    user_id: str = Field(description="用户唯一标识")
    tenant_id: str = Field(description="所属租户标识")
    username: str = Field(description="用户名")
    display_name: str = Field(default="", description="显示名称")
    email: Optional[str] = Field(default=None, description="邮箱")
    org_node_id: Optional[str] = Field(
        default=None, description="所属组织节点标识"
    )
    status: UserStatus = Field(default=UserStatus.ACTIVE, description="用户状态")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")


class AccessToken(BaseModel):
    """访问令牌：用于 API 鉴权。

    令牌绑定到用户，有有效期与状态。
    """

    token_id: str = Field(description="令牌唯一标识")
    user_id: str = Field(description="关联的用户标识")
    tenant_id: str = Field(description="所属租户标识")
    token_hash: str = Field(description="令牌哈希（不存储原文）")
    status: TokenStatus = Field(default=TokenStatus.ACTIVE, description="令牌状态")
    issued_at: datetime = Field(default_factory=_utc_now, description="签发时间")
    expires_at: datetime = Field(description="过期时间")
    revoked_at: Optional[datetime] = Field(default=None, description="撤销时间")
    last_used_at: Optional[datetime] = Field(default=None, description="最近使用时间")
