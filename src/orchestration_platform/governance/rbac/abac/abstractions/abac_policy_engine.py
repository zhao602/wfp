# =============================================================================
# ABAC 策略引擎抽象接口（governance/rbac/abac/abstractions/abac_policy_engine.py）
#
# 职责：定义 ABAC 策略决策的统一契约。
# 设计意图（对应 design 2.2.2.2 IAbacPolicyEngine / ICombinedDecisionEngine）：
#   - IAbacPolicyEngine.decide：执行 ABAC 策略决策
#   - ICombinedDecisionEngine.decide：执行 RBAC+ABAC 组合决策
# =============================================================================

from typing import Protocol, runtime_checkable

from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
)
from orchestration_platform.governance.rbac.abac.models import AbacDecision


@runtime_checkable
class IAbacPolicyEngine(Protocol):
    """ABAC 策略引擎抽象接口。"""

    async def decide(
        self,
        request: GovernanceRequest,
    ) -> AbacDecision:
        """执行 ABAC 策略决策。

        前置条件：request 已通过 RBAC 认证（tenant_id/user_id 已填充）。
        后置条件：返回允许/拒绝/无策略决策。
        异常映射：ATTR_EVAL_FAILED（按策略 fail_mode 处理）。
        """
        ...


@runtime_checkable
class ICombinedDecisionEngine(Protocol):
    """组合决策引擎抽象接口：RBAC 先 + ABAC 后。"""

    async def decide(
        self,
        request: GovernanceRequest,
    ) -> GovernanceDecision:
        """执行组合决策。

        前置条件：request 包含完整令牌与资源信息。
        后置条件：RBAC 拒绝→直接拒绝；RBAC 允许→ABAC 细化；ABAC 无策略→允许。
        异常映射：继承 RBAC 与 ABAC 异常映射。
        """
        ...
