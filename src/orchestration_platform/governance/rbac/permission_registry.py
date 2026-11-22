# =============================================================================
# 权限项注册表（governance/rbac/permission_registry.py）
#
# 职责：管理权限项的注册与查询。
# 设计意图（对应 spec 5.1、design 2.4.1、任务 T50）：
#   1. 权限项二元组（资源类型 + 操作类型）唯一性校验；
#   2. 扩展点：新增资源类型与操作类型只需在注册表登记权限项。
# =============================================================================

from typing import Dict, List, Optional

from orchestration_platform.domain.models.enums import (
    OperationType,
    ResourceScope,
    ResourceType,
)
from orchestration_platform.governance.rbac.models import PermissionItem, Role


class PermissionRegistry:
    """权限项注册表。

    管理平台全部权限项的注册与查询，支持按资源类型与操作类型检索。
    """

    def __init__(self) -> None:
        self._permissions: Dict[str, PermissionItem] = {}
        self._default_roles: Dict[str, Role] = {}

    def register_permission(self, permission: PermissionItem) -> None:
        """登记权限项。

        二元组（资源类型 + 操作类型）唯一，重复登记抛出异常。
        """
        key = self._make_key(permission.resource_type, permission.operation)
        if key in self._permissions:
            existing = self._permissions[key]
            if existing.scope != permission.scope:
                raise ValueError(f"权限项冲突: {key} 已存在且范围不同")
        self._permissions[key] = permission

    def get_permission(
        self,
        resource_type: ResourceType,
        operation: OperationType,
    ) -> Optional[PermissionItem]:
        """按资源类型与操作类型查询权限项。"""
        return self._permissions.get(self._make_key(resource_type, operation))

    def list_permissions(self) -> List[PermissionItem]:
        """列出全部权限项。"""
        return list(self._permissions.values())

    def list_by_resource_type(
        self, resource_type: ResourceType
    ) -> List[PermissionItem]:
        """按资源类型列出权限项。"""
        return [
            p for p in self._permissions.values()
            if p.resource_type == resource_type
        ]

    def register_default_role(self, role: Role) -> None:
        """登记默认角色。"""
        self._default_roles[role.role_id] = role

    def get_default_role(self, role_id: str) -> Optional[Role]:
        """查询默认角色。"""
        return self._default_roles.get(role_id)

    def list_default_roles(self) -> List[Role]:
        """列出全部默认角色。"""
        return list(self._default_roles.values())

    @staticmethod
    def _make_key(resource_type: ResourceType, operation: OperationType) -> str:
        """生成权限项唯一键。"""
        return f"{resource_type.value}:{operation.value}"


def create_default_registry() -> PermissionRegistry:
    """创建并初始化默认权限注册表。

    覆盖七类资源类型 × 六类操作类型，并定义三个默认角色。
    """
    registry = PermissionRegistry()

    # 登记全部权限项（七类资源 × 六类操作）
    for resource_type in ResourceType:
        for operation in OperationType:
            permission = PermissionItem(
                permission_id=f"{resource_type.value}_{operation.value}",
                resource_type=resource_type,
                operation=operation,
                scope=ResourceScope.TENANT,
                description=f"对{resource_type.value}执行{operation.value}操作",
            )
            registry.register_permission(permission)

    # 定义默认角色
    # 租户管理员：本租户全部权限
    admin_permissions = registry.list_permissions()
    registry.register_default_role(Role(
        role_id="role_tenant_admin",
        role_name="租户管理员",
        tenant_id="tenant_default",
        permissions=admin_permissions,
        is_system_role=True,
    ))

    # 编排工程师：工作流定义创建/读取/执行 + 实例读取
    orchestrator_perms = [
        registry.get_permission(ResourceType.WORKFLOW_DEFINITION, OperationType.CREATE),
        registry.get_permission(ResourceType.WORKFLOW_DEFINITION, OperationType.READ),
        registry.get_permission(ResourceType.WORKFLOW_DEFINITION, OperationType.EXECUTE),
        registry.get_permission(ResourceType.EXECUTION_INSTANCE, OperationType.READ),
    ]
    for p in orchestrator_perms:
        if p:
            p.scope = ResourceScope.SELF
    registry.register_default_role(Role(
        role_id="role_orchestrator",
        role_name="编排工程师",
        tenant_id="tenant_default",
        permissions=[p for p in orchestrator_perms if p],
        is_system_role=True,
    ))

    # 合规审计员：审计日志读取/导出
    auditor_perms = [
        registry.get_permission(ResourceType.AUDIT_LOG, OperationType.READ),
        registry.get_permission(ResourceType.AUDIT_LOG, OperationType.EXPORT),
    ]
    registry.register_default_role(Role(
        role_id="role_auditor",
        role_name="合规审计员",
        tenant_id="tenant_default",
        permissions=[p for p in auditor_perms if p],
        is_system_role=True,
    ))

    return registry
