# TODO(tech-debt): B档候选孤儿（族群 B3），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 组织树管理器（governance/tenant/org_tree_manager.py）
#
# 职责：管理组织节点的创建、嵌套与查询。
# 设计意图（对应 spec 5.1、任务 T53.1）：
#   1. 组织节点可嵌套子节点；
#   2. 循环引用校验；
#   3. 节点路径自动维护。
# =============================================================================

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from orchestration_platform.governance.tenant.models import OrgNode


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OrgTreeManager:
    """组织树管理器。"""

    def __init__(self) -> None:
        self._nodes: Dict[str, OrgNode] = {}

    async def create_node(
        self,
        tenant_id: str,
        node_name: str,
        parent_node_id: Optional[str] = None,
    ) -> OrgNode:
        """创建组织节点。

        自动维护节点路径，检测循环引用。
        """
        # 循环引用校验
        if parent_node_id:
            if parent_node_id not in self._nodes:
                raise ValueError(f"父节点不存在: {parent_node_id}")
            parent = self._nodes[parent_node_id]
            if parent.tenant_id != tenant_id:
                raise ValueError("跨租户创建组织节点被拒绝")
            # 检测循环（parent 不能是自己的后代）
            visited: Set[str] = set()
            current = parent_node_id
            while current:
                if current in visited:
                    raise ValueError(f"检测到循环引用: {current}")
                visited.add(current)
                current_node = self._nodes.get(current)
                current = current_node.parent_node_id if current_node else None

        # 生成节点路径
        if parent_node_id:
            parent = self._nodes[parent_node_id]
            node_path = f"{parent.node_path}/{node_name}"
        else:
            node_path = f"/{node_name}"

        org_node_id = f"org_{uuid.uuid4().hex[:12]}"
        node = OrgNode(
            org_node_id=org_node_id,
            tenant_id=tenant_id,
            node_name=node_name,
            parent_node_id=parent_node_id,
            node_path=node_path,
        )
        self._nodes[org_node_id] = node
        return node

    def get_node(self, org_node_id: str) -> Optional[OrgNode]:
        """查询组织节点。"""
        return self._nodes.get(org_node_id)

    def list_children(self, parent_node_id: str) -> List[OrgNode]:
        """列出直接子节点。"""
        return [
            node for node in self._nodes.values()
            if node.parent_node_id == parent_node_id
        ]

    def get_subtree(self, org_node_id: str) -> List[OrgNode]:
        """查询子树（基于 node_path 前缀匹配）。"""
        node = self._nodes.get(org_node_id)
        if node is None:
            return []
        prefix = node.node_path + "/"
        return [
            n for n in self._nodes.values()
            if n.node_path.startswith(prefix) or n.org_node_id == org_node_id
        ]

    async def delete_node(self, org_node_id: str) -> None:
        """删除组织节点（需无子节点）。"""
        children = self.list_children(org_node_id)
        if children:
            raise ValueError(f"组织节点存在子节点，无法删除: {org_node_id}")
        if org_node_id not in self._nodes:
            raise ValueError(f"组织节点不存在: {org_node_id}")
        del self._nodes[org_node_id]
