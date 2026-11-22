# TODO(tech-debt): B档候选孤儿（族群 B2），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# PostgreSQL 组织树仓储（governance/persistence/postgres/postgres_org_tree_repository.py）
#
# 职责：以 PostgreSQL 持久化组织节点。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.2）：
#   1. 全部查询注入 tenant_id 过滤条件保证隔离（第一道保险）；
#   2. 仓储基类向连接注入 app.current_tenant 会话变量（RLS 第二道保险，
#      roadmap B1，见 persistence/postgres/tenant_scoped_base.py）；
#   3. 子树查询通过 node_path 前缀匹配实现。
# =============================================================================

from typing import List, Optional

from orchestration_platform.governance.tenant.models import OrgNode
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


class PostgresOrgTreeRepository(TenantScopedPostgresRepository):
    """组织树仓储——PostgreSQL 实现。"""

    async def create_node(self, node: OrgNode) -> OrgNode:
        """创建组织节点。"""
        async with self._tenant_connection(node.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO governance_org_node
                    (org_node_id, tenant_id, node_name, parent_node_id, node_path, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (tenant_id, org_node_id) DO UPDATE
                SET node_name = $3, parent_node_id = $4, node_path = $5
                """,
                node.org_node_id,
                node.tenant_id,
                node.node_name,
                node.parent_node_id,
                node.node_path,
                node.created_at,
            )
        return node

    async def get_node(
        self, tenant_id: str, org_node_id: str
    ) -> Optional[OrgNode]:
        """按标识查询组织节点。"""
        async with self._tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT org_node_id, tenant_id, node_name, parent_node_id, node_path, created_at "
                "FROM governance_org_node WHERE tenant_id = $1 AND org_node_id = $2",
                tenant_id, org_node_id,
            )
        if row is None:
            return None
        return self._row_to_node(row)

    async def update_node(self, node: OrgNode) -> OrgNode:
        """更新组织节点信息。"""
        async with self._tenant_connection(node.tenant_id) as conn:
            await conn.execute(
                "UPDATE governance_org_node SET node_name = $3, node_path = $4 "
                "WHERE tenant_id = $1 AND org_node_id = $2",
                node.tenant_id, node.org_node_id, node.node_name, node.node_path,
            )
        return node

    async def delete_node(self, tenant_id: str, org_node_id: str) -> None:
        """删除组织节点。"""
        async with self._tenant_connection(tenant_id) as conn:
            await conn.execute(
                "DELETE FROM governance_org_node WHERE tenant_id = $1 AND org_node_id = $2",
                tenant_id, org_node_id,
            )

    async def list_children(
        self, tenant_id: str, parent_node_id: Optional[str] = None
    ) -> List[OrgNode]:
        """列出指定父节点的直接子节点。"""
        async with self._tenant_connection(tenant_id) as conn:
            if parent_node_id is None:
                rows = await conn.fetch(
                    "SELECT org_node_id, tenant_id, node_name, parent_node_id, node_path, created_at "
                    "FROM governance_org_node WHERE tenant_id = $1 AND parent_node_id IS NULL "
                    "ORDER BY node_name",
                    tenant_id,
                )
            else:
                rows = await conn.fetch(
                    "SELECT org_node_id, tenant_id, node_name, parent_node_id, node_path, created_at "
                    "FROM governance_org_node WHERE tenant_id = $1 AND parent_node_id = $2 "
                    "ORDER BY node_name",
                    tenant_id, parent_node_id,
                )
        return [self._row_to_node(r) for r in rows]

    async def get_subtree(
        self, tenant_id: str, root_node_id: str
    ) -> List[OrgNode]:
        """查询以指定节点为根的整棵子树（通过 node_path 前缀匹配）。"""
        async with self._tenant_connection(tenant_id) as conn:
            root = await conn.fetchrow(
                "SELECT node_path FROM governance_org_node "
                "WHERE tenant_id = $1 AND org_node_id = $2",
                tenant_id, root_node_id,
            )
            if root is None:
                return []
            prefix = root["node_path"] + "/"
            rows = await conn.fetch(
                "SELECT org_node_id, tenant_id, node_name, parent_node_id, node_path, created_at "
                "FROM governance_org_node WHERE tenant_id = $1 "
                "AND (node_path = $2 OR node_path LIKE $3) ORDER BY node_path",
                tenant_id, root["node_path"], prefix + "%",
            )
        return [self._row_to_node(r) for r in rows]

    async def list_all_nodes(self, tenant_id: str) -> List[OrgNode]:
        """列出租户下全部组织节点。"""
        async with self._tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT org_node_id, tenant_id, node_name, parent_node_id, node_path, created_at "
                "FROM governance_org_node WHERE tenant_id = $1 ORDER BY node_path",
                tenant_id,
            )
        return [self._row_to_node(r) for r in rows]

    @staticmethod
    def _row_to_node(row) -> OrgNode:
        """将数据库行映射为 OrgNode 模型。"""
        return OrgNode(
            org_node_id=row["org_node_id"],
            tenant_id=row["tenant_id"],
            node_name=row["node_name"],
            parent_node_id=row["parent_node_id"],
            node_path=row["node_path"],
            created_at=row["created_at"],
        )
