# =============================================================================
# RBAC 领域模型（governance/rbac/models.py）
#
# 职责：定义权限项、角色与角色绑定的领域模型。
# 设计意图（对应 spec 5.1、design 2.4.1）：
#   1. 权限项 = 资源类型 × 操作类型 × 资源范围；
#   2. 角色是权限项的集合，支持权限继承；
#   3. 角色绑定将角色关联到用户与组织节点。
# =============================================================================

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import (
    OperationType,
    ResourceScope,
    ResourceType,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PermissionItem(BaseModel):
    """权限项：资源类型 × 操作类型 × 资源范围。

    一个权限项声明"对某类资源执行某种操作的粒度"。
    """

    permission_id: str = Field(description="权限项唯一标识")
    resource_type: ResourceType = Field(description="受保护的资源类型")
    operation: OperationType = Field(description="允许的操作")
    scope: ResourceScope = Field(
        default=ResourceScope.TENANT, description="资源范围粒度"
    )
    description: str = Field(default="", description="权限项描述")


class Role(BaseModel):
    """角色：权限项的命名集合。

    角色可继承父角色，子角色自动拥有父角色的全部权限。
    """

    role_id: str = Field(description="角色唯一标识")
    role_name: str = Field(description="角色显示名称")
    tenant_id: str = Field(description="所属租户标识")
    permissions: List[PermissionItem] = Field(
        default_factory=list, description="直接授予的权限项列表"
    )
    parent_role_id: Optional[str] = Field(
        default=None, description="父角色标识（权限继承）"
    )
    is_system_role: bool = Field(
        default=False, description="是否系统内置角色（不可删除）"
    )
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")


class RoleBinding(BaseModel):
    """角色绑定：将角色关联到用户与组织节点。

    一个绑定声明"某用户在某组织节点下拥有某角色"。
    """

    binding_id: str = Field(description="绑定唯一标识")
    role_id: str = Field(description="关联的角色标识")
    user_id: str = Field(description="关联的用户标识")
    org_node_id: Optional[str] = Field(
        default=None, description="组织节点标识（None 表示租户级绑定）"
    )
    tenant_id: str = Field(description="所属租户标识")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
