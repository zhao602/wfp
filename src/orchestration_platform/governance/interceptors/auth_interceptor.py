# =============================================================================
# 鉴权拦截器（governance/interceptors/auth_interceptor.py）
#
# 职责：校验访问令牌与操作权限。
# 设计意图（对应 spec 5.1、design 2.2.2、任务 T56.1）：
#   1. intercept_before 先认证后鉴权，拒绝或放行；
#   2. 鉴权通过后将 TokenClaims 与 AuthorizationResult 注入请求上下文；
#   3. fail-closed：异常时拒绝所有请求；
#   4. 未启用治理时自动放行（降级模式）。
# =============================================================================

from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
    GovernanceResult,
)
from orchestration_platform.governance.rbac.permission_checker import (
    RbacPermissionChecker,
)


class AuthInterceptor:
    """鉴权拦截器实现。

    校验令牌有效性并检查用户对目标资源的操作权限。
    实现 IAuthInterceptor 接口。
    """

    def __init__(
        self,
        permission_checker: RbacPermissionChecker,
        enabled: Optional[bool] = None,
    ) -> None:
        """初始化鉴权拦截器。

        Args:
            permission_checker: RBAC 鉴权引擎实例。
            enabled: 是否启用；缺省从配置读取 rbac_enabled。
        """
        self._checker = permission_checker
        self._enabled = enabled if enabled is not None else get_settings().rbac_enabled

    async def authenticate(
        self, request: GovernanceRequest
    ) -> GovernanceDecision:
        """校验访问令牌，填充用户标识与租户标识。"""
        return await self._checker.authenticate(request)

    async def authorize(
        self, request: GovernanceRequest
    ) -> GovernanceDecision:
        """校验用户对目标资源的操作权限。"""
        return await self._checker.authorize(request)

    async def intercept_before(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
    ) -> GovernanceDecision:
        """前置拦截：先认证后鉴权。

        未启用治理时自动放行（降级模式）。
        fail-closed：异常时拒绝所有请求。
        """
        if not self._enabled:
            return decision

        try:
            # 1. 认证：校验令牌，填充 tenant_id 与 user_id
            auth_decision = await self.authenticate(request)
            if not auth_decision.allowed:
                return auth_decision

            # 2. 鉴权：校验操作权限
            authz_decision = await self.authorize(request)
            if not authz_decision.allowed:
                return authz_decision

            # 3. 注入鉴权结果至请求上下文
            request.metadata["auth_decision"] = authz_decision.model_dump(mode="json")
            request.metadata["tenant_context"] = request.tenant_id
            request.metadata["user_context"] = request.user_id

            # 合并决策
            return GovernanceDecision(
                allowed=True,
                tenant_context=request.tenant_id,
                resource_scope_filter=authz_decision.resource_scope_filter,
                metadata=authz_decision.metadata,
            )
        except Exception as exc:
            # fail-closed：异常时拒绝
            return GovernanceDecision(
                allowed=False,
                deny_reason=f"鉴权拦截器异常: {type(exc).__name__}: {exc}",
            )

    async def intercept_after(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
        result: GovernanceResult,
    ) -> GovernanceResult:
        """后置拦截：鉴权无需后置处理。"""
        return result
