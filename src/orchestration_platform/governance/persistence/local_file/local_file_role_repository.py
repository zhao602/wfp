# 销项（2026-11-18，arch_review_v2_tasks G3/CHG-06/CHG-14）：原 B2 族成员，已由 RBAC 落库接线为生产调用点（role 族注入 RbacPermissionChecker；user/tenant 族经 rbac/bootstrap 幂等初始化），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# 本地文件角色仓储（governance/persistence/local_file/local_file_role_repository.py）
#
# 职责：以 JSON 文件持久化角色、权限项与角色绑定。
# 设计意图（对应 spec 5.1、design 2.4.1、任务 T55.3）：
#   1. 目录结构：
#      {root}/governance/tenants/{tenant_id}/roles/{role_id}.json
#      {root}/governance/tenants/{tenant_id}/permissions/{permission_id}.json
#      {root}/governance/tenants/{tenant_id}/bindings/{binding_id}.json
#   2. list_permissions_for_user 含组织节点继承与角色继承。
# =============================================================================

import json
from pathlib import Path
from typing import Dict, List, Optional, Set

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.rbac.models import (
    PermissionItem,
    Role,
    RoleBinding,
)


class LocalFileRoleRepository:
    """角色仓储——本地文件实现。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        root = storage_root or get_settings().local_storage_root
        self._base_dir = Path(root) / "governance" / "tenants"

    def _roles_dir(self, tenant_id: str) -> Path:
        return self._base_dir / tenant_id / "roles"

    def _role_file(self, tenant_id: str, role_id: str) -> Path:
        return self._roles_dir(tenant_id) / f"{role_id}.json"

    def _perms_dir(self, tenant_id: str) -> Path:
        return self._base_dir / tenant_id / "permissions"

    def _perm_file(self, tenant_id: str, permission_id: str) -> Path:
        return self._perms_dir(tenant_id) / f"{permission_id}.json"

    def _bindings_dir(self, tenant_id: str) -> Path:
        return self._base_dir / tenant_id / "bindings"

    def _binding_file(self, tenant_id: str, binding_id: str) -> Path:
        return self._bindings_dir(tenant_id) / f"{binding_id}.json"

    # ----- 角色 -----

    async def create_role(self, role: Role) -> Role:
        """创建角色。"""
        roles_dir = self._roles_dir(role.tenant_id)
        roles_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._role_file(role.tenant_id, role.role_id)
        file_path.write_text(role.model_dump_json(indent=2), encoding="utf-8")
        return role

    async def get_role(
        self, tenant_id: str, role_id: str
    ) -> Optional[Role]:
        """按标识查询角色。"""
        file_path = self._role_file(tenant_id, role_id)
        if not file_path.exists():
            return None
        return Role.model_validate_json(file_path.read_text(encoding="utf-8"))

    async def list_roles(self, tenant_id: str) -> List[Role]:
        """列出租户下全部角色。"""
        roles_dir = self._roles_dir(tenant_id)
        if not roles_dir.exists():
            return []
        roles: List[Role] = []
        for file_path in roles_dir.glob("*.json"):
            roles.append(Role.model_validate_json(file_path.read_text(encoding="utf-8")))
        roles.sort(key=lambda r: r.created_at)
        return roles

    async def delete_role(self, tenant_id: str, role_id: str) -> None:
        """删除角色（系统内置角色不可删除）。"""
        role = await self.get_role(tenant_id, role_id)
        if role is None or role.is_system_role:
            return
        file_path = self._role_file(tenant_id, role_id)
        file_path.unlink(missing_ok=True)

    # ----- 权限项 -----

    async def save_permission(
        self, tenant_id: str, permission: PermissionItem
    ) -> PermissionItem:
        """保存或更新权限项。"""
        perms_dir = self._perms_dir(tenant_id)
        perms_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._perm_file(tenant_id, permission.permission_id)
        file_path.write_text(permission.model_dump_json(indent=2), encoding="utf-8")
        return permission

    async def list_permissions(self, tenant_id: str) -> List[PermissionItem]:
        """列出租户下全部权限项。"""
        perms_dir = self._perms_dir(tenant_id)
        if not perms_dir.exists():
            return []
        perms: List[PermissionItem] = []
        for file_path in perms_dir.glob("*.json"):
            perms.append(PermissionItem.model_validate_json(file_path.read_text(encoding="utf-8")))
        return perms

    # ----- 角色绑定 -----

    async def create_binding(self, binding: RoleBinding) -> RoleBinding:
        """创建角色绑定。"""
        bindings_dir = self._bindings_dir(binding.tenant_id)
        bindings_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._binding_file(binding.tenant_id, binding.binding_id)
        file_path.write_text(binding.model_dump_json(indent=2), encoding="utf-8")
        return binding

    async def list_bindings_for_user(
        self, tenant_id: str, user_id: str
    ) -> List[RoleBinding]:
        """列出用户的全部角色绑定。"""
        bindings_dir = self._bindings_dir(tenant_id)
        if not bindings_dir.exists():
            return []
        bindings: List[RoleBinding] = []
        for file_path in bindings_dir.glob("*.json"):
            b = RoleBinding.model_validate_json(file_path.read_text(encoding="utf-8"))
            if b.user_id == user_id:
                bindings.append(b)
        return bindings

    async def list_bindings_for_role(
        self, tenant_id: str, role_id: str
    ) -> List[RoleBinding]:
        """列出角色的全部绑定。"""
        bindings_dir = self._bindings_dir(tenant_id)
        if not bindings_dir.exists():
            return []
        bindings: List[RoleBinding] = []
        for file_path in bindings_dir.glob("*.json"):
            b = RoleBinding.model_validate_json(file_path.read_text(encoding="utf-8"))
            if b.role_id == role_id:
                bindings.append(b)
        return bindings

    async def delete_binding(
        self, tenant_id: str, binding_id: str
    ) -> None:
        """删除角色绑定。"""
        file_path = self._binding_file(tenant_id, binding_id)
        file_path.unlink(missing_ok=True)

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
        # 1. 查询用户直接绑定
        direct_bindings = await self.list_bindings_for_user(tenant_id, user_id)

        # 2. 沿组织树向上查找祖先节点的绑定
        all_bindings: List[RoleBinding] = list(direct_bindings)
        users_dir = self._base_dir / tenant_id / "users"
        user_file = users_dir / f"{user_id}.json"
        if user_file.exists():
            import json as _json
            user_data = _json.loads(user_file.read_text(encoding="utf-8"))
            org_node_id = user_data.get("org_node_id")
            if org_node_id:
                # 加载组织节点获取路径
                org_file = self._base_dir / tenant_id / "org_nodes" / f"{org_node_id}.json"
                if org_file.exists():
                    org_data = _json.loads(org_file.read_text(encoding="utf-8"))
                    node_path = org_data.get("node_path", "")
                    # 沿路径向上查找祖先节点
                    path_parts = node_path.strip("/").split("/")
                    for i in range(1, len(path_parts)):
                        ancestor_path = "/" + "/".join(path_parts[:i])
                        org_nodes_dir = self._base_dir / tenant_id / "org_nodes"
                        if org_nodes_dir.exists():
                            for of in org_nodes_dir.glob("*.json"):
                                od = _json.loads(of.read_text(encoding="utf-8"))
                                if od.get("node_path") == ancestor_path:
                                    ancestor_id = od["org_node_id"]
                                    # 查找该祖先节点的全部绑定
                                    bindings_dir = self._bindings_dir(tenant_id)
                                    if bindings_dir.exists():
                                        for bf in bindings_dir.glob("*.json"):
                                            b = RoleBinding.model_validate_json(
                                                bf.read_text(encoding="utf-8")
                                            )
                                            if b.org_node_id == ancestor_id:
                                                all_bindings.append(b)

        # 3. 加载角色权限（含父角色继承）
        seen_perms: Dict[str, PermissionItem] = {}
        for binding in all_bindings:
            perms = await self._load_role_permissions_chain(
                tenant_id, binding.role_id
            )
            for p in perms:
                if p.permission_id not in seen_perms:
                    seen_perms[p.permission_id] = p

        return list(seen_perms.values())

    async def _load_role_permissions_chain(
        self, tenant_id: str, role_id: str
    ) -> List[PermissionItem]:
        """加载角色及其父角色链的全部权限项。"""
        result: List[PermissionItem] = []
        visited: Set[str] = set()
        current_id: Optional[str] = role_id

        while current_id and current_id not in visited:
            visited.add(current_id)
            role = await self.get_role(tenant_id, current_id)
            if role is None:
                break
            result.extend(role.permissions)
            current_id = role.parent_role_id

        return result
