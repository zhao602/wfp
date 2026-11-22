# 销项（2026-11-18，arch_review_v2_tasks G3/CHG-06/CHG-14）：原 B2 族成员，已由 RBAC 落库接线为生产调用点（role 族注入 RbacPermissionChecker；user/tenant 族经 rbac/bootstrap 幂等初始化），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# 角色仓储抽象接口（governance/persistence/abstractions/role_repository.py）
#
# 职责：定义角色、权限项与角色绑定的仓储抽象接口（Protocol）。
# 设计意图（对应 spec 5.1、design 2.4.1、任务 T55.1）：
#   1. 角色是权限项的集合，支持权限继承；
#   2. 角色绑定将角色关联到用户与组织节点；
#   3. list_permissions_for_user 含组织节点继承——沿组织树向上汇聚权限。
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.governance.rbac.models import (
    PermissionItem,
    Role,
    RoleBinding,
)


@runtime_checkable
class IRoleRepository(Protocol):
    """角色仓储：角色、权限项与角色绑定的增删改查。"""

    # ----- 角色 -----

    async def create_role(self, role: Role) -> Role:
        """创建角色。

        前置条件：同租户内 role_id 不重复。
        后置条件：角色持久化，可被 get_role 查询到。
        """
        ...

    async def get_role(
        self, tenant_id: str, role_id: str
    ) -> Optional[Role]:
        """按标识查询角色，不存在返回 None。"""
        ...

    async def list_roles(self, tenant_id: str) -> List[Role]:
        """列出租户下全部角色。"""
        ...

    async def delete_role(self, tenant_id: str, role_id: str) -> None:
        """删除角色。

        前置条件：角色非系统内置角色（is_system_role=False）。
        """
        ...

    # ----- 权限项 -----

    async def save_permission(
        self, tenant_id: str, permission: PermissionItem
    ) -> PermissionItem:
        """保存或更新权限项。"""
        ...

    async def list_permissions(self, tenant_id: str) -> List[PermissionItem]:
        """列出租户下全部权限项。"""
        ...

    # ----- 角色绑定 -----

    async def create_binding(self, binding: RoleBinding) -> RoleBinding:
        """创建角色绑定。

        前置条件：角色与用户存在。
        后置条件：绑定持久化，用户拥有该角色权限。
        """
        ...

    async def list_bindings_for_user(
        self, tenant_id: str, user_id: str
    ) -> List[RoleBinding]:
        """列出用户的全部角色绑定。"""
        ...

    async def list_bindings_for_role(
        self, tenant_id: str, role_id: str
    ) -> List[RoleBinding]:
        """列出角色的全部绑定。"""
        ...

    async def delete_binding(
        self, tenant_id: str, binding_id: str
    ) -> None:
        """删除角色绑定。"""
        ...

    # ----- 权限聚合 -----

    async def list_permissions_for_user(
        self, tenant_id: str, user_id: str
    ) -> List[PermissionItem]:
        """列出用户的有效权限项（含组织节点继承与角色继承）。

        聚合逻辑：
          1. 查询用户全部角色绑定；
          2. 对每个绑定加载角色及其父角色链的权限项；
          3. 沿组织树向上继承父节点的角色绑定权限；
          4. 去重后返回。
        """
        ...
