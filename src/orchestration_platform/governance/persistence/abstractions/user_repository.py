# 销项（2026-11-18，arch_review_v2_tasks G3/CHG-06/CHG-14）：原 B2 族成员，已由 RBAC 落库接线为生产调用点（role 族注入 RbacPermissionChecker；user/tenant 族经 rbac/bootstrap 幂等初始化），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# 用户仓储抽象接口（governance/persistence/abstractions/user_repository.py）
#
# 职责：定义用户的仓储抽象接口（Protocol）。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.1）：
#   1. 用户挂载于组织节点，归属一个租户；
#   2. 支持禁用用户（令牌立即失效）；
#   3. 全部查询注入 tenant_id 过滤条件保证隔离。
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.governance.tenant.models import User


@runtime_checkable
class IUserRepository(Protocol):
    """用户仓储：用户的增删改查与禁用。"""

    async def create_user(self, user: User) -> User:
        """创建用户。

        前置条件：同租户内 user_id 不重复、username 不重复。
        后置条件：用户记录持久化，可被 get_user 查询到。
        """
        ...

    async def get_user(
        self, tenant_id: str, user_id: str
    ) -> Optional[User]:
        """按标识查询用户，不存在返回 None。"""
        ...

    async def get_user_by_username(
        self, tenant_id: str, username: str
    ) -> Optional[User]:
        """按用户名查询用户。"""
        ...

    async def update_user(self, user: User) -> User:
        """更新用户信息。"""
        ...

    async def disable_user(self, tenant_id: str, user_id: str) -> None:
        """禁用用户（状态置为 DISABLED）。"""
        ...

    async def list_users(
        self,
        tenant_id: str,
        org_node_id: Optional[str] = None,
    ) -> List[User]:
        """列出租户下全部用户，可按组织节点过滤。"""
        ...
