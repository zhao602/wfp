# TODO(tech-debt): B档候选孤儿（族群 B2），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 本地文件组织树仓储（governance/persistence/local_file/local_file_org_tree_repository.py）
#
# 职责：以 JSON 文件持久化组织节点。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.3）：
#   1. 目录结构：{root}/governance/tenants/{tenant_id}/org_nodes/{node_id}.json
#   2. 数据按租户分目录存储，保证隔离；
#   3. 子树查询通过 node_path 前缀匹配实现。
# =============================================================================

from pathlib import Path
from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.tenant.models import OrgNode


class LocalFileOrgTreeRepository:
    """组织树仓储——本地文件实现。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        root = storage_root or get_settings().local_storage_root
        self._base_dir = Path(root) / "governance" / "tenants"

    def _nodes_dir(self, tenant_id: str) -> Path:
        """返回租户组织节点目录。"""
        return self._base_dir / tenant_id / "org_nodes"

    def _node_file(self, tenant_id: str, org_node_id: str) -> Path:
        """返回组织节点文件路径。"""
        return self._nodes_dir(tenant_id) / f"{org_node_id}.json"

    async def create_node(self, node: OrgNode) -> OrgNode:
        """创建组织节点。"""
        nodes_dir = self._nodes_dir(node.tenant_id)
        nodes_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._node_file(node.tenant_id, node.org_node_id)
        file_path.write_text(node.model_dump_json(indent=2), encoding="utf-8")
        return node

    async def get_node(
        self, tenant_id: str, org_node_id: str
    ) -> Optional[OrgNode]:
        """按标识查询组织节点。"""
        file_path = self._node_file(tenant_id, org_node_id)
        if not file_path.exists():
            return None
        return OrgNode.model_validate_json(file_path.read_text(encoding="utf-8"))

    async def update_node(self, node: OrgNode) -> OrgNode:
        """更新组织节点信息。"""
        file_path = self._node_file(node.tenant_id, node.org_node_id)
        file_path.write_text(node.model_dump_json(indent=2), encoding="utf-8")
        return node

    async def delete_node(self, tenant_id: str, org_node_id: str) -> None:
        """删除组织节点。"""
        file_path = self._node_file(tenant_id, org_node_id)
        if file_path.exists():
            file_path.unlink()

    async def list_children(
        self, tenant_id: str, parent_node_id: Optional[str] = None
    ) -> List[OrgNode]:
        """列出指定父节点的直接子节点。"""
        all_nodes = await self.list_all_nodes(tenant_id)
        return [
            n for n in all_nodes
            if n.parent_node_id == parent_node_id
        ]

    async def get_subtree(
        self, tenant_id: str, root_node_id: str
    ) -> List[OrgNode]:
        """查询以指定节点为根的整棵子树。"""
        root = await self.get_node(tenant_id, root_node_id)
        if root is None:
            return []
        all_nodes = await self.list_all_nodes(tenant_id)
        prefix = root.node_path + "/"
        result = [root]
        for n in all_nodes:
            if n.node_path.startswith(prefix):
                result.append(n)
        return result

    async def list_all_nodes(self, tenant_id: str) -> List[OrgNode]:
        """列出租户下全部组织节点。"""
        nodes_dir = self._nodes_dir(tenant_id)
        if not nodes_dir.exists():
            return []
        nodes: List[OrgNode] = []
        for file_path in nodes_dir.glob("*.json"):
            nodes.append(OrgNode.model_validate_json(file_path.read_text(encoding="utf-8")))
        nodes.sort(key=lambda n: n.node_path)
        return nodes
