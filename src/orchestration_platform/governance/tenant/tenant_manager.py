# =============================================================================
# 租户管理器（governance/tenant/tenant_manager.py）
#
# 职责：管理租户的生命周期与配额。
# 设计意图（对应 spec 5.1、任务 T52.1）：
#   1. 租户创建自动初始化默认组织与角色；
#   2. 配额可配置且调整后立即生效。
# =============================================================================

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from orchestration_platform.domain.models.enums import TenantStatus
from orchestration_platform.governance.tenant.models import Tenant, TenantQuota


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TenantManager:
    """租户管理器。

    管理租户创建、查询、配额调整与禁用/启用。
    """

    def __init__(self) -> None:
        self._tenants: Dict[str, Tenant] = {}
        self._quotas: Dict[str, TenantQuota] = {}
        self._init_default_tenant()

    def _init_default_tenant(self) -> None:
        """初始化默认租户。"""
        tenant = Tenant(
            tenant_id="tenant_default",
            tenant_name="默认租户",
            is_default=True,
        )
        self._tenants[tenant.tenant_id] = tenant
        self._quotas[tenant.tenant_id] = TenantQuota(tenant_id=tenant.tenant_id)

    async def create_tenant(
        self,
        tenant_name: str,
        max_instances: int = 100,
        max_workflows: int = 1000,
    ) -> Tenant:
        """创建租户。

        自动生成标识、初始化配额。
        """
        if len(tenant_name) > 100:
            raise ValueError("租户名称长度不超过 100 字符")
        for existing in self._tenants.values():
            if existing.tenant_name == tenant_name:
                raise ValueError(f"租户名称已存在: {tenant_name}")

        tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"
        tenant = Tenant(tenant_id=tenant_id, tenant_name=tenant_name)
        self._tenants[tenant_id] = tenant
        self._quotas[tenant_id] = TenantQuota(
            tenant_id=tenant_id,
            max_instances=max_instances,
            max_workflows=max_workflows,
        )
        return tenant

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """查询租户。"""
        return self._tenants.get(tenant_id)

    def list_tenants(self) -> List[Tenant]:
        """列出全部租户。"""
        return list(self._tenants.values())

    def get_quota(self, tenant_id: str) -> Optional[TenantQuota]:
        """查询租户配额。"""
        return self._quotas.get(tenant_id)

    async def update_quota(
        self,
        tenant_id: str,
        max_instances: Optional[int] = None,
        max_workflows: Optional[int] = None,
    ) -> TenantQuota:
        """更新租户配额。"""
        quota = self._quotas.get(tenant_id)
        if quota is None:
            raise ValueError(f"租户不存在: {tenant_id}")
        if max_instances is not None:
            quota.max_instances = max_instances
        if max_workflows is not None:
            quota.max_workflows = max_workflows
        quota.updated_at = _utc_now()
        return quota

    async def suspend_tenant(self, tenant_id: str) -> Tenant:
        """禁用租户。"""
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise ValueError(f"租户不存在: {tenant_id}")
        if tenant.is_default:
            raise ValueError("默认租户不可禁用")
        tenant.status = TenantStatus.SUSPENDED
        tenant.updated_at = _utc_now()
        return tenant

    async def activate_tenant(self, tenant_id: str) -> Tenant:
        """启用租户。"""
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise ValueError(f"租户不存在: {tenant_id}")
        tenant.status = TenantStatus.ACTIVE
        tenant.updated_at = _utc_now()
        return tenant
