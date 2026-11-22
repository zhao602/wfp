# =============================================================================
# ABAC 策略仓储抽象接口（governance/rbac/abac/abstractions/policy_repository.py）
#
# 职责：定义 ABAC 策略存储的统一契约。
# 设计意图（对应 design 2.2.2.2 / 模块B AbacPolicyRepository）：
#   - save：保存策略
#   - load_by_tenant：加载租户全部策略
#   - delete：软删除策略（is_active=False）
#   - get_by_id：按标识查询策略
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.governance.rbac.abac.models import AbacPolicy


@runtime_checkable
class IAbacPolicyRepository(Protocol):
    """ABAC 策略仓储抽象接口。"""

    async def save(self, policy: AbacPolicy) -> str:
        """保存策略。

        前置条件：策略格式合法。
        后置条件：策略持久化存储，返回策略标识。
        异常映射：POLICY_FORMAT_INVALID（格式非法）。
        """
        ...

    async def load_by_tenant(self, tenant_id: str) -> List[AbacPolicy]:
        """加载租户全部启用策略。

        后置条件：返回按 priority 排序的策略列表。
        """
        ...

    async def delete(self, policy_id: str, tenant_id: str) -> bool:
        """软删除策略（is_active=False）。

        后置条件：返回是否成功。
        """
        ...

    async def get_by_id(
        self,
        policy_id: str,
        tenant_id: str,
    ) -> Optional[AbacPolicy]:
        """按标识查询策略。"""
        ...
