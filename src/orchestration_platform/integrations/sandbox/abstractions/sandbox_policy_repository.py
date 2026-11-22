# =============================================================================
# 沙箱策略仓储抽象接口（integrations/sandbox/abstractions/sandbox_policy_repository.py）
#
# 职责：定义沙箱策略仓储的抽象接口。
# 设计意图（对应偏差修正 design 2.2.2.1）：
#   - ISandboxPolicyRepository：策略持久化（保存/查询/列表/删除）
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.integrations.sandbox.models import SandboxPolicy


@runtime_checkable
class ISandboxPolicyRepository(Protocol):
    """沙箱策略仓储抽象接口。

    前置条件：策略已通过格式校验。
    后置条件：保存后可按标识查询；删除为软删除（is_active=False）。
    异常映射：
        SANDBOX_POLICY_INVALID —— 策略格式非法
    """

    async def save(self, policy: SandboxPolicy) -> str:
        """保存策略，返回策略标识。"""
        ...

    async def get(
        self,
        policy_id: str,
        tenant_id: str,
    ) -> Optional[SandboxPolicy]:
        """按标识查询策略。"""
        ...

    async def list_by_tenant(
        self,
        tenant_id: str,
    ) -> List[SandboxPolicy]:
        """列出租户全部启用策略。"""
        ...

    async def delete(
        self,
        policy_id: str,
        tenant_id: str,
    ) -> bool:
        """软删除策略（is_active=False）。"""
        ...
