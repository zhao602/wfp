# =============================================================================
# 租户隔离器（governance/tenant/tenant_isolator.py）
#
# 职责：注入租户上下文与校验数据隔离。
# 设计意图（对应 spec 5.1、任务 T52.2）：
#   1. 鉴权通过后注入 tenant_id 至请求上下文；
#   2. 所有数据查询自动注入 tenant_id 过滤条件；
#   3. 跨租户访问被拦截。
# =============================================================================

from typing import Any, Dict

from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
)
from orchestration_platform.governance.tenant.models import TenantQuota


class TenantIsolator:
    """租户隔离器。

    注入租户上下文并强制数据隔离。
    """

    def __init__(self) -> None:
        self._tenant_contexts: Dict[str, TenantQuota] = {}

    def set_tenant_quota(self, tenant_id: str, quota: TenantQuota) -> None:
        """设置租户配额上下文。"""
        self._tenant_contexts[tenant_id] = quota

    async def inject_tenant_context(
        self,
        request: GovernanceRequest,
    ) -> GovernanceDecision:
        """注入租户上下文至请求。

        鉴权通过后调用，确保 tenant_id 已填充。
        """
        if not request.tenant_id:
            return GovernanceDecision(allowed=False, deny_reason="租户标识缺失")
        return GovernanceDecision(
            allowed=True,
            tenant_context=request.tenant_id,
        )

    async def verify_isolation(
        self,
        request: GovernanceRequest,
    ) -> GovernanceDecision:
        """校验请求不跨租户访问数据。

        请求参数中指定其他租户标识时拒绝。
        """
        param_tenant_id = request.payload.get("tenant_id")
        if (
            param_tenant_id
            and request.tenant_id
            and param_tenant_id != request.tenant_id
        ):
            return GovernanceDecision(
                allowed=False,
                deny_reason=f"跨租户访问被拒绝: 请求租户={param_tenant_id}, 令牌租户={request.tenant_id}",
            )
        return GovernanceDecision(allowed=True)

    def inject_tenant_filter(
        self,
        query_params: Dict[str, Any],
        tenant_id: str,
    ) -> Dict[str, Any]:
        """为数据查询注入租户过滤条件。

        在 SQL 查询层强制注入 tenant_id 条件，不可通过参数绕过。
        """
        filtered = dict(query_params)
        filtered["tenant_id"] = tenant_id
        return filtered
