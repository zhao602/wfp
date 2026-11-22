# TODO(tech-debt): B档候选孤儿（族群 B3），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 用户管理器（governance/tenant/user_manager.py）
#
# 职责：管理用户创建、禁用与角色绑定。
# 设计意图（对应 spec 5.1、任务 T53.2）：
#   1. 用户至少归属一个组织节点；
#   2. 禁用用户时令牌立即失效；
#   3. 多角色权限取并集。
# =============================================================================

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from orchestration_platform.domain.models.enums import UserStatus
from orchestration_platform.governance.rbac.models import RoleBinding
from orchestration_platform.governance.tenant.models import User


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserManager:
    """用户管理器。"""

    def __init__(self) -> None:
        self._users: Dict[str, User] = {}
        self._bindings: Dict[str, List[RoleBinding]] = {}

    async def create_user(
        self,
        tenant_id: str,
        username: str,
        org_node_id: str,
        display_name: str = "",
        email: Optional[str] = None,
    ) -> User:
        """创建用户。

        用户至少归属一个组织节点。
        """
        for existing in self._users.values():
            if existing.tenant_id == tenant_id and existing.username == username:
                raise ValueError(f"用户名已存在: {username}")

        user_id = f"user_{uuid.uuid4().hex[:12]}"
        user = User(
            user_id=user_id,
            tenant_id=tenant_id,
            username=username,
            display_name=display_name,
            email=email,
            org_node_id=org_node_id,
        )
        self._users[user_id] = user
        self._bindings[user_id] = []
        return user

    def get_user(self, user_id: str) -> Optional[User]:
        """查询用户。"""
        return self._users.get(user_id)

    def list_users(self, tenant_id: str) -> List[User]:
        """列出租户内全部用户。"""
        return [
            user for user in self._users.values()
            if user.tenant_id == tenant_id
        ]

    async def disable_user(self, user_id: str) -> User:
        """禁用用户。

        禁用后令牌应立即失效（由 TokenManager 处理）。
        """
        user = self._users.get(user_id)
        if user is None:
            raise ValueError(f"用户不存在: {user_id}")
        user.status = UserStatus.DISABLED
        user.updated_at = _utc_now()
        return user

    async def bind_role(
        self,
        user_id: str,
        role_id: str,
        tenant_id: str,
        org_node_id: Optional[str] = None,
    ) -> RoleBinding:
        """为用户绑定角色。"""
        if user_id not in self._users:
            raise ValueError(f"用户不存在: {user_id}")
        binding = RoleBinding(
            binding_id=f"binding_{uuid.uuid4().hex[:12]}",
            role_id=role_id,
            user_id=user_id,
            org_node_id=org_node_id,
            tenant_id=tenant_id,
        )
        self._bindings.setdefault(user_id, []).append(binding)
        return binding

    def get_user_bindings(self, user_id: str) -> List[RoleBinding]:
        """获取用户的角色绑定列表。"""
        return self._bindings.get(user_id, [])
