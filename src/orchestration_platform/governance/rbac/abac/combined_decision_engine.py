# =============================================================================
# 组合决策引擎实现（governance/rbac/abac/combined_decision_engine.py）
#
# 职责：编排 RBAC 先校验 + ABAC 再细化的两级组合决策。
# 设计意图（对应偏差修正 spec 5.2.1.3 / design 2.1.3.1 状态机）：
#   - RBAC 拒绝→直接拒绝（短路，不执行 ABAC）
#   - RBAC 允许→执行 ABAC 细化决策
#   - ABAC 无策略→返回允许（保持纯 RBAC 行为一致）
#   - ABAC 允许→返回允许；ABAC 拒绝→返回拒绝
# =============================================================================

import logging

from orchestration_platform.domain.models.enums import PolicyEffect
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
)
from orchestration_platform.governance.rbac.abac.abstractions.abac_policy_engine import (
    IAbacPolicyEngine,
    ICombinedDecisionEngine,
)
from orchestration_platform.governance.rbac.abac.models import AbacDecision
from orchestration_platform.governance.rbac.permission_checker import (
    RbacPermissionChecker,
)

_logger = logging.getLogger(__name__)


class CombinedDecisionEngine:
    """组合决策引擎：RBAC 先 + ABAC 后。

    构造注入 RbacPermissionChecker 与 IAbacPolicyEngine，
    按 spec 5.2.1.3 组合决策顺序执行两级校验。
    """

    def __init__(
        self,
        rbac_checker: RbacPermissionChecker,
        abac_engine: IAbacPolicyEngine,
    ) -> None:
        """初始化组合决策引擎。

        参数：
            rbac_checker：RBAC 鉴权引擎（既有）
            abac_engine：ABAC 策略引擎（新增）
        """
        self._rbac = rbac_checker
        self._abac = abac_engine

    async def decide(
        self,
        request: GovernanceRequest,
    ) -> GovernanceDecision:
        """执行组合决策。

        流程：RBAC 认证 → RBAC 鉴权 → ABAC 细化。
        RBAC 拒绝时短路，不执行 ABAC。
        """
        # 1. RBAC 认证（令牌解析）
        auth_decision = await self._rbac.authenticate(request)
        if not auth_decision.allowed:
            _logger.info(
                "组合决策-RBAC认证拒绝: trace=%s, reason=%s",
                request.trace_id,
                auth_decision.deny_reason,
            )
            return auth_decision

        # 2. RBAC 鉴权（权限校验）
        rbac_decision = await self._rbac.authorize(request)
        if not rbac_decision.allowed:
            _logger.info(
                "组合决策-RBAC鉴权拒绝: trace=%s, reason=%s",
                request.trace_id,
                rbac_decision.deny_reason,
            )
            return rbac_decision

        # 3. ABAC 细化决策
        abac_decision = await self._abac.decide(request)

        # ABAC 无策略：保持 RBAC 允许结果
        if abac_decision.is_no_policy:
            _logger.debug(
                "组合决策-ABAC无策略,保持允许: trace=%s",
                request.trace_id,
            )
            return rbac_decision

        # ABAC 允许
        if abac_decision.effect == PolicyEffect.ALLOW:
            _logger.debug(
                "组合决策-ABAC允许: trace=%s, policy=%s",
                request.trace_id,
                abac_decision.matched_policy_id,
            )
            return rbac_decision

        # ABAC 拒绝
        _logger.info(
            "组合决策-ABAC拒绝: trace=%s, policy=%s, errors=%s",
            request.trace_id,
            abac_decision.matched_policy_id,
            abac_decision.eval_errors,
        )
        return GovernanceDecision(
            allowed=False,
            deny_reason=f"ABAC 策略拒绝: {abac_decision.matched_policy_id}",
            tenant_context=rbac_decision.tenant_context,
            metadata={"abac_policy_id": abac_decision.matched_policy_id},
        )
