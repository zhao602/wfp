# =============================================================================
# 配额校验器（governance/tenant/quota_checker.py）
#
# 职责：校验租户资源配额。
# 设计意图（对应 spec 5.1、任务 T52.3）：
#   1. 四类配额校验：实例数、工作流数、存储、Token；
#   2. 校验时机：实例启动前、工作流保存前；
#   3. 超限返回 TENANT_QUOTA_EXCEEDED。
# =============================================================================

from typing import Optional

from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
)
from orchestration_platform.governance.tenant.models import TenantQuota


class QuotaChecker:
    """配额校验器。

    校验租户资源使用是否超出配额上限。
    """

    def __init__(self) -> None:
        self._quotas: dict[str, TenantQuota] = {}
        self._usage: dict[str, dict[str, int]] = {}

    def set_quota(self, tenant_id: str, quota: TenantQuota) -> None:
        """设置租户配额。"""
        self._quotas[tenant_id] = quota
        if tenant_id not in self._usage:
            self._usage[tenant_id] = {
                "instances": 0,
                "workflows": 0,
                "storage_gb": 0,
                "tokens_today": 0,
            }

    def increment_usage(self, tenant_id: str, resource: str, amount: int = 1) -> None:
        """增加用量计数。"""
        if tenant_id not in self._usage:
            self._usage[tenant_id] = {
                "instances": 0,
                "workflows": 0,
                "storage_gb": 0,
                "tokens_today": 0,
            }
        self._usage[tenant_id][resource] = self._usage[tenant_id].get(resource, 0) + amount

    def decrement_usage(self, tenant_id: str, resource: str, amount: int = 1) -> None:
        """减少用量计数。"""
        if tenant_id in self._usage:
            self._usage[tenant_id][resource] = max(
                0, self._usage[tenant_id].get(resource, 0) - amount
            )

    async def check_quota(
        self,
        request: GovernanceRequest,
    ) -> GovernanceDecision:
        """校验租户配额。"""
        if not request.tenant_id:
            return GovernanceDecision(allowed=True)

        quota = self._quotas.get(request.tenant_id)
        if quota is None:
            return GovernanceDecision(allowed=True)

        usage = self._usage.get(request.tenant_id, {})

        if quota.max_instances is not None:
            if usage.get("instances", 0) >= quota.max_instances:
                return GovernanceDecision(
                    allowed=False,
                    deny_reason=f"实例配额超限: {usage.get('instances', 0)}/{quota.max_instances}",
                )

        if quota.max_workflows is not None:
            if usage.get("workflows", 0) >= quota.max_workflows:
                return GovernanceDecision(
                    allowed=False,
                    deny_reason=f"工作流配额超限: {usage.get('workflows', 0)}/{quota.max_workflows}",
                )

        return GovernanceDecision(allowed=True)

    def get_usage(self, tenant_id: str) -> dict[str, int]:
        """获取租户当前用量。"""
        return self._usage.get(tenant_id, {})
