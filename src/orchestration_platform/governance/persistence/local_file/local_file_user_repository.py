# 销项（2026-11-18，arch_review_v2_tasks G3/CHG-06/CHG-14）：原 B2 族成员，已由 RBAC 落库接线为生产调用点（role 族注入 RbacPermissionChecker；user/tenant 族经 rbac/bootstrap 幂等初始化），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# 本地文件用户仓储（governance/persistence/local_file/local_file_user_repository.py）
#
# 职责：以 JSON 文件持久化用户。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.3）：
#   1. 目录结构：{root}/governance/tenants/{tenant_id}/users/{user_id}.json
#   2. 数据按租户分目录存储，保证隔离。
# =============================================================================

from pathlib import Path
from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import UserStatus
from orchestration_platform.governance.tenant.models import User


class LocalFileUserRepository:
    """用户仓储——本地文件实现。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        root = storage_root or get_settings().local_storage_root
        self._base_dir = Path(root) / "governance" / "tenants"

    def _users_dir(self, tenant_id: str) -> Path:
        """返回租户用户目录。"""
        return self._base_dir / tenant_id / "users"

    def _user_file(self, tenant_id: str, user_id: str) -> Path:
        """返回用户文件路径。"""
        return self._users_dir(tenant_id) / f"{user_id}.json"

    async def create_user(self, user: User) -> User:
        """创建用户。"""
        users_dir = self._users_dir(user.tenant_id)
        users_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._user_file(user.tenant_id, user.user_id)
        file_path.write_text(user.model_dump_json(indent=2), encoding="utf-8")
        return user

    async def get_user(
        self, tenant_id: str, user_id: str
    ) -> Optional[User]:
        """按标识查询用户。"""
        file_path = self._user_file(tenant_id, user_id)
        if not file_path.exists():
            return None
        return User.model_validate_json(file_path.read_text(encoding="utf-8"))

    async def get_user_by_username(
        self, tenant_id: str, username: str
    ) -> Optional[User]:
        """按用户名查询用户。"""
        all_users = await self.list_users(tenant_id)
        for u in all_users:
            if u.username == username:
                return u
        return None

    async def update_user(self, user: User) -> User:
        """更新用户信息。"""
        file_path = self._user_file(user.tenant_id, user.user_id)
        file_path.write_text(user.model_dump_json(indent=2), encoding="utf-8")
        return user

    async def disable_user(self, tenant_id: str, user_id: str) -> None:
        """禁用用户。"""
        user = await self.get_user(tenant_id, user_id)
        if user is None:
            return
        user.status = UserStatus.DISABLED
        await self.update_user(user)

    async def list_users(
        self,
        tenant_id: str,
        org_node_id: Optional[str] = None,
    ) -> List[User]:
        """列出租户下全部用户，可按组织节点过滤。"""
        users_dir = self._users_dir(tenant_id)
        if not users_dir.exists():
            return []
        users: List[User] = []
        for file_path in users_dir.glob("*.json"):
            users.append(User.model_validate_json(file_path.read_text(encoding="utf-8")))
        if org_node_id is not None:
            users = [u for u in users if u.org_node_id == org_node_id]
        users.sort(key=lambda u: u.created_at)
        return users
