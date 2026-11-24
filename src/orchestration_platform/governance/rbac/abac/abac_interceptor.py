# TODO(tech-debt): B档候选孤儿（族群 B1），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# ABAC 拦截器（governance/rbac/abac/abac_interceptor.py）
#
# 职责：将 ABAC 组合决策注入拦截器链。
# 设计意图（对应偏差修正 spec 5.2 / design 模块B AbacInterceptor）：
#   - 实现 IGovernanceInterceptor 接口
#   - intercept_before 调用 CombinedDecisionEngine.decide
#   - 拒绝时返回 GovernanceDecision(allowed=False)
#   - intercept_after 空实现（ABAC 仅前置决策）
# =============================================================================

import logging

from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
    GovernanceResult,
)
from orchestration_platform.governance.rbac.abac.abstractions.abac_policy_engine import (
    ICombinedDecisionEngine,
)

_logger = logging.getLogger(__name__)


class AbacInterceptor:
    """ABAC 拦截器。

    在拦截器链中 auth 之后执行 ABAC 细化决策。
    实现 IGovernanceInterceptor 接口（结构性子类型，无需显式继承）。
    """

    def __init__(self, combined_engine: ICombinedDecisionEngine) -> None:
        """初始化 ABAC 拦截器。

        参数：
            combined_engine：组合决策引擎（RBAC+ABAC）
        """
        self._combined_engine = combined_engine

    async def intercept_before(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
    ) -> GovernanceDecision:
        """前置拦截：执行 ABAC 组合决策。

        若前序拦截器已拒绝，直接返回（不重复决策）。
        """
        if not decision.allowed:
            return decision

        try:
            abac_decision = await self._combined_engine.decide(request)
            if not abac_decision.allowed:
                _logger.info(
                    "ABAC 拦截器拒绝: trace=%s, reason=%s",
                    request.trace_id,
                    abac_decision.deny_reason,
                )
                return abac_decision
            return abac_decision
        except Exception as exc:
            _logger.error(
                "ABAC 拦截器异常: trace=%s, error=%s",
                request.trace_id,
                exc,
            )
            return GovernanceDecision(
                allowed=False,
                deny_reason=f"ABAC 决策异常: {type(exc).__name__}",
            )

    async def intercept_after(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
        result: GovernanceResult,
    ) -> GovernanceResult:
        """后置拦截：空实现（ABAC 仅前置决策）。"""
        return result
