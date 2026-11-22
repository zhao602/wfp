# 销项（2026-11-18，arch_review_v2_tasks G3/CHG-06/CHG-14）：原 B2 族成员，已由 RBAC 落库接线为生产调用点（role 族注入 RbacPermissionChecker；user/tenant 族经 rbac/bootstrap 幂等初始化），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# PostgreSQL 角色仓储（governance/persistence/postgres/postgres_role_repository.py）
#
# 职责：以 PostgreSQL 持久化角色、权限项与角色绑定。
# 设计意图（对应 spec 5.1、design 2.4.1、任务 T55.2）：
#   1. 全部查询注入 tenant_id 过滤条件保证隔离（第一道保险）；
#   2. 仓储基类向连接注入 app.current_tenant 会话变量（RLS 第二道保险，
#      roadmap B1，见 persistence/postgres/tenant_scoped_base.py）；
#   3. list_permissions_for_user 含组织节点继承与角色继承；
#   4. 权限项以 JSON 存储于角色记录中。
# =============================================================================

import json
from typing import List, Optional

from orchestration_platform.governance.rbac.models import (
    PermissionItem,
    Role,
    RoleBinding,
)
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


class PostgresRoleRepository(TenantScopedPostgresRepository):
    """角色仓储——PostgreSQL 实现。"""

    # ----- 角色 -----

    async def create_role(self, role: Role) -> Role:
        """创建角色（幂等：同标识覆盖）。"""
        perms_json = json.dumps([p.model_dump(mode="json") for p in role.permissions])
        async with self._tenant_connection(role.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO governance_role
                    (role_id, tenant_id, role_name, permissions, parent_role_id,
                     is_system_role, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (tenant_id, role_id) DO UPDATE
                SET role_name = $3, permissions = $4, parent_role_id = $5
                """,
                role.role_id, role.tenant_id, role.role_name,
                perms_json, role.parent_role_id, role.is_system_role,
                role.created_at,
            )
        return role

    async def get_role(
        self, tenant_id: str, role_id: str
    ) -> Optional[Role]:
        """按标识查询角色。"""
        async with self._tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT role_id, tenant_id, role_name, permissions, parent_role_id, "
                "is_system_role, created_at FROM governance_role "
                "WHERE tenant_id = $1 AND role_id = $2",
                tenant_id, role_id,
            )
        if row is None:
            return None
        return self._row_to_role(row)

    async def list_roles(self, tenant_id: str) -> List[Role]:
        """列出租户下全部角色。"""
        async with self._tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT role_id, tenant_id, role_name, permissions, parent_role_id, "
                "is_system_role, created_at FROM governance_role "
                "WHERE tenant_id = $1 ORDER BY created_at",
                tenant_id,
            )
        return [self._row_to_role(r) for r in rows]

    async def delete_role(self, tenant_id: str, role_id: str) -> None:
        """删除角色（系统内置角色不可删除）。"""
        async with self._tenant_connection(tenant_id) as conn:
            await conn.execute(
                "DELETE FROM governance_role WHERE tenant_id = $1 AND role_id = $2 "
                "AND is_system_role = false",
                tenant_id, role_id,
            )

    # ----- 权限项 -----

    async def save_permission(
        self, tenant_id: str, permission: PermissionItem
    ) -> PermissionItem:
        """保存或更新权限项（存储于 governance_permission 表）。"""
        async with self._tenant_connection(tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO governance_permission
                    (permission_id, tenant_id, resource_type, operation, scope, description)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (tenant_id, permission_id) DO UPDATE
                SET resource_type = $3, operation = $4, scope = $5, description = $6
                """,
                permission.permission_id, tenant_id,
                permission.resource_type.value, permission.operation.value,
                permission.scope.value, permission.description,
            )
        return permission

    async def list_permissions(self, tenant_id: str) -> List[PermissionItem]:
        """列出租户下全部权限项。"""
        async with self._tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT permission_id, resource_type, operation, scope, description "
                "FROM governance_permission WHERE tenant_id = $1",
                tenant_id,
            )
        return [self._row_to_permission(r) for r in rows]

    # ----- 角色绑定 -----

    async def create_binding(self, binding: RoleBinding) -> RoleBinding:
        """创建角色绑定。"""
        async with self._tenant_connection(binding.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO governance_role_binding
                    (binding_id, role_id, user_id, org_node_id, tenant_id, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (tenant_id, binding_id) DO UPDATE
                SET role_id = $2, user_id = $3, org_node_id = $4
                """,
                binding.binding_id, binding.role_id, binding.user_id,
                binding.org_node_id, binding.tenant_id, binding.created_at,
            )
        return binding

    async def list_bindings_for_user(
        self, tenant_id: str, user_id: str
    ) -> List[RoleBinding]:
        """列出用户的全部角色绑定。"""
        async with self._tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT binding_id, role_id, user_id, org_node_id, tenant_id, created_at "
                "FROM governance_role_binding WHERE tenant_id = $1 AND user_id = $2",
                tenant_id, user_id,
            )
        return [self._row_to_binding(r) for r in rows]

    async def list_bindings_for_role(
        self, tenant_id: str, role_id: str
    ) -> List[RoleBinding]:
        """列出角色的全部绑定。"""
        async with self._tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT binding_id, role_id, user_id, org_node_id, tenant_id, created_at "
                "FROM governance_role_binding WHERE tenant_id = $1 AND role_id = $2",
                tenant_id, role_id,
            )
        return [self._row_to_binding(r) for r in rows]

    async def delete_binding(
        self, tenant_id: str, binding_id: str
    ) -> None:
        """删除角色绑定。"""
        async with self._tenant_connection(tenant_id) as conn:
            await conn.execute(
                "DELETE FROM governance_role_binding WHERE tenant_id = $1 AND binding_id = $2",
                tenant_id, binding_id,
            )

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
        async with self._tenant_connection(tenant_id) as conn:
            # 1. 查询用户直接绑定
            bindings = await conn.fetch(
                "SELECT binding_id, role_id, user_id, org_node_id, tenant_id, created_at "
                "FROM governance_role_binding WHERE tenant_id = $1 AND user_id = $2",
                tenant_id, user_id,
            )

            # 2. 沿组织树向上查找父节点的绑定
            user_row = await conn.fetchrow(
                "SELECT org_node_id FROM governance_user WHERE tenant_id = $1 AND user_id = $2",
                tenant_id, user_id,
            )
            inherited_bindings = list(bindings)
            if user_row and user_row["org_node_id"]:
                org_node = await conn.fetchrow(
                    "SELECT node_path FROM governance_org_node "
                    "WHERE tenant_id = $1 AND org_node_id = $2",
                    tenant_id, user_row["org_node_id"],
                )
                if org_node:
                    # 沿路径向上查找所有祖先节点的绑定
                    path_parts = org_node["node_path"].strip("/").split("/")
                    ancestor_paths = []
                    for i in range(1, len(path_parts)):
                        ancestor_paths.append("/" + "/".join(path_parts[:i]))
                    if ancestor_paths:
                        ancestor_nodes = await conn.fetch(
                            "SELECT org_node_id FROM governance_org_node "
                            "WHERE tenant_id = $1 AND node_path = ANY($2)",
                            tenant_id, ancestor_paths,
                        )
                        ancestor_ids = [n["org_node_id"] for n in ancestor_nodes]
                        if ancestor_ids:
                            ancestor_bindings = await conn.fetch(
                                "SELECT binding_id, role_id, user_id, org_node_id, tenant_id, created_at "
                                "FROM governance_role_binding WHERE tenant_id = $1 AND org_node_id = ANY($2)",
                                tenant_id, ancestor_ids,
                            )
                            inherited_bindings.extend(ancestor_bindings)

            # 3. 加载角色权限（含父角色继承）
            seen_perms: dict[str, PermissionItem] = {}
            for b in inherited_bindings:
                perms = await self._load_role_permissions_chain(
                    conn, tenant_id, b["role_id"]
                )
                for p in perms:
                    if p.permission_id not in seen_perms:
                        seen_perms[p.permission_id] = p

        return list(seen_perms.values())

    async def _load_role_permissions_chain(
        self, conn, tenant_id: str, role_id: str
    ) -> List[PermissionItem]:
        """加载角色及其父角色链的全部权限项。"""
        result: List[PermissionItem] = []
        visited: set[str] = set()
        current_id: Optional[str] = role_id

        while current_id and current_id not in visited:
            visited.add(current_id)
            row = await conn.fetchrow(
                "SELECT permissions, parent_role_id FROM governance_role "
                "WHERE tenant_id = $1 AND role_id = $2",
                tenant_id, current_id,
            )
            if row is None:
                break
            perms_data = json.loads(row["permissions"]) if row["permissions"] else []
            for pd in perms_data:
                result.append(PermissionItem.model_validate(pd))
            current_id = row["parent_role_id"]

        return result

    # ----- 行映射 -----

    @staticmethod
    def _row_to_role(row) -> Role:
        """将数据库行映射为 Role 模型。"""
        perms_data = json.loads(row["permissions"]) if row["permissions"] else []
        return Role(
            role_id=row["role_id"],
            role_name=row["role_name"],
            tenant_id=row["tenant_id"],
            permissions=[PermissionItem.model_validate(pd) for pd in perms_data],
            parent_role_id=row["parent_role_id"],
            is_system_role=row["is_system_role"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_permission(row) -> PermissionItem:
        """将数据库行映射为 PermissionItem 模型。"""
        from orchestration_platform.domain.models.enums import (
            OperationType, ResourceScope, ResourceType,
        )
        return PermissionItem(
            permission_id=row["permission_id"],
            resource_type=ResourceType(row["resource_type"]),
            operation=OperationType(row["operation"]),
            scope=ResourceScope(row["scope"]),
            description=row["description"],
        )

    @staticmethod
    def _row_to_binding(row) -> RoleBinding:
        """将数据库行映射为 RoleBinding 模型。"""
        return RoleBinding(
            binding_id=row["binding_id"],
            role_id=row["role_id"],
            user_id=row["user_id"],
            org_node_id=row["org_node_id"],
            tenant_id=row["tenant_id"],
            created_at=row["created_at"],
        )
