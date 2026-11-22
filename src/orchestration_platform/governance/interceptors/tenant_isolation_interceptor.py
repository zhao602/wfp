# =============================================================================
# 租户隔离拦截器（governance/interceptors/tenant_isolation_interceptor.py）
#
# 职责：注入租户上下文与校验数据隔离。
# 设计意图（对应 spec 5.1、design 2.2.2、任务 T56.2）：
#   1. intercept_before 注入租户上下文（tenant_id 与 TenantQuota）；
#   2. intercept_after 校验响应数据不含其他租户数据；
#   3. 调用 TenantIsolator.inject_tenant_context 与 verify_isolation；
#   4. 未启用治理时自动放行（降级模式）。
# =============================================================================

from typing import Any, Dict, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
    GovernanceResult,
    ITenantIsolationInterceptor,
)
from orchestration_platform.governance.tenant.tenant_isolator import TenantIsolator


class TenantIsolationInterceptor:
    """租户隔离拦截器实现。

    注入租户上下文并强制数据隔离。
    实现 ITenantIsolationInterceptor 接口。
    """

    def __init__(
        self,
        isolator: TenantIsolator,
        enabled: Optional[bool] = None,
    ) -> None:
        """初始化租户隔离拦截器。

        Args:
            isolator: 租户隔离器实例。
            enabled: 是否启用；缺省从配置读取 tenant_isolation_enabled。
        """
        self._isolator = isolator
        self._enabled = (
            enabled if enabled is not None else get_settings().tenant_isolation_enabled
        )

    async def inject_tenant_context(
        self, request: GovernanceRequest
    ) -> GovernanceDecision:
        """注入租户上下文至请求。"""
        return await self._isolator.inject_tenant_context(request)

    async def verify_isolation(
        self, request: GovernanceRequest
    ) -> GovernanceDecision:
        """校验请求不跨租户访问数据。"""
        return await self._isolator.verify_isolation(request)

    async def intercept_before(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
    ) -> GovernanceDecision:
        """前置拦截：注入租户上下文并校验隔离。

        未启用治理时自动放行（降级模式）。
        """
        if not self._enabled:
            return decision

        try:
            # 1. 注入租户上下文
            inject_decision = await self.inject_tenant_context(request)
            if not inject_decision.allowed:
                return inject_decision

            # 2. 校验数据隔离
            isolation_decision = await self.verify_isolation(request)
            if not isolation_decision.allowed:
                return isolation_decision

            # 3. 注入租户过滤条件至决策
            if request.tenant_id:
                scope_filter = self._isolator.inject_tenant_filter(
                    decision.resource_scope_filter or {}, request.tenant_id
                )
                return GovernanceDecision(
                    allowed=True,
                    tenant_context=request.tenant_id,
                    resource_scope_filter=scope_filter,
                    metadata=decision.metadata,
                )

            return decision
        except Exception as exc:
            return GovernanceDecision(
                allowed=False,
                deny_reason=f"租户隔离拦截器异常: {type(exc).__name__}: {exc}",
            )

    async def intercept_after(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
        result: GovernanceResult,
    ) -> GovernanceResult:
        """后置拦截：校验响应数据不含其他租户数据。

        检查 execution_result 中的 tenant_id 字段是否与请求租户一致。
        """
        if not self._enabled:
            return result

        try:
            if result.execution_result is not None and request.tenant_id:
                violation = self._check_result_tenant(
                    result.execution_result, request.tenant_id
                )
                if violation:
                    result.audit_summary = result.audit_summary or {}
                    result.audit_summary["isolation_violation"] = violation
        except Exception:
            pass

        return result

    @staticmethod
    def _check_result_tenant(
        result: Any, expected_tenant_id: str
    ) -> Optional[str]:
        """检查结果中的 tenant_id 是否与预期一致。

        Returns:
            违规描述（如有），None 表示无违规。
        """
        if isinstance(result, dict):
            result_tenant = result.get("tenant_id")
            if result_tenant and result_tenant != expected_tenant_id:
                return f"结果含其他租户数据: {result_tenant}"
            for value in result.values():
                violation = TenantIsolationInterceptor._check_result_tenant(
                    value, expected_tenant_id
                )
                if violation:
                    return violation
        elif isinstance(result, list):
            for item in result:
                violation = TenantIsolationInterceptor._check_result_tenant(
                    item, expected_tenant_id
                )
                if violation:
                    return violation
        return None
