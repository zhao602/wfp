# TODO(tech-debt): B档候选孤儿（族群 B2），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 令牌仓储抽象接口（governance/persistence/abstractions/token_repository.py）
#
# 职责：定义访问令牌的仓储抽象接口（Protocol）。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.1）：
#   1. 令牌哈希存储，不保存原文；
#   2. 支持签发、校验、吊销与批量撤销；
#   3. 全部查询注入 tenant_id 过滤条件保证隔离。
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.governance.tenant.models import AccessToken


@runtime_checkable
class ITokenRepository(Protocol):
    """令牌仓储：令牌的签发、校验、吊销与查询。"""

    async def save_token(self, token: AccessToken) -> AccessToken:
        """保存令牌记录。

        前置条件：token_id 不重复。
        后置条件：令牌持久化，可被 get_token 查询到。
        """
        ...

    async def get_token(
        self, tenant_id: str, token_id: str
    ) -> Optional[AccessToken]:
        """按标识查询令牌，不存在返回 None。"""
        ...

    async def get_by_hash(
        self, token_hash: str
    ) -> Optional[AccessToken]:
        """按令牌哈希查询令牌（用于校验）。"""
        ...

    async def is_revoked(self, token_id: str) -> bool:
        """判断令牌是否已吊销。"""
        ...

    async def revoke_token(self, token_id: str) -> None:
        """吊销令牌。"""
        ...

    async def revoke_for_user(
        self, tenant_id: str, user_id: str
    ) -> int:
        """撤销用户的所有活跃令牌，返回撤销数量。"""
        ...

    async def list_tokens_for_user(
        self, tenant_id: str, user_id: str
    ) -> List[AccessToken]:
        """列出用户的全部令牌。"""
        ...

    async def update_last_used(
        self, token_id: str
    ) -> None:
        """更新令牌最近使用时间。"""
        ...
