# TODO(tech-debt): B档候选孤儿（族群 B2），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 组织树仓储抽象接口（governance/persistence/abstractions/org_tree_repository.py）
#
# 职责：定义组织节点的仓储抽象接口（Protocol）。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.1）：
#   1. 组织树支持多层级，每个节点归属一个租户；
#   2. 支持子树查询与路径维护；
#   3. 全部查询注入 tenant_id 过滤条件保证隔离。
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.governance.tenant.models import OrgNode


@runtime_checkable
class IOrgTreeRepository(Protocol):
    """组织树仓储：组织节点的增删改查与子树查询。"""

    async def create_node(self, node: OrgNode) -> OrgNode:
        """创建组织节点。

        前置条件：同租户内 org_node_id 不重复；parent_node_id 存在或为 None。
        后置条件：节点持久化，node_path 正确维护。
        """
        ...

    async def get_node(
        self, tenant_id: str, org_node_id: str
    ) -> Optional[OrgNode]:
        """按标识查询组织节点，不存在返回 None。"""
        ...

    async def update_node(self, node: OrgNode) -> OrgNode:
        """更新组织节点信息。"""
        ...

    async def delete_node(self, tenant_id: str, org_node_id: str) -> None:
        """删除组织节点。

        前置条件：节点无子节点。
        """
        ...

    async def list_children(
        self, tenant_id: str, parent_node_id: Optional[str] = None
    ) -> List[OrgNode]:
        """列出指定父节点的直接子节点。

        parent_node_id 为 None 时列出根节点。
        """
        ...

    async def get_subtree(
        self, tenant_id: str, root_node_id: str
    ) -> List[OrgNode]:
        """查询以指定节点为根的整棵子树（含自身）。"""
        ...

    async def list_all_nodes(self, tenant_id: str) -> List[OrgNode]:
        """列出租户下全部组织节点。"""
        ...
