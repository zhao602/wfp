# 销项（2026-11-18，arch_review_v2_tasks G3/CHG-06/CHG-14）：原 B2 族成员，已由 RBAC 落库接线为生产调用点（role 族注入 RbacPermissionChecker；user/tenant 族经 rbac/bootstrap 幂等初始化），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# 租户仓储抽象接口（governance/persistence/abstractions/tenant_repository.py）
#
# 职责：定义租户与租户配额的仓储抽象接口（Protocol）。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.1）：
#   1. 接口与实现分离——业务层依赖 Protocol，由工厂按配置注入具体实现；
#   2. 双实现可切换——PostgreSQL（生产）与本地文件（验证降级）行为一致；
#   3. 异步接口——为 PostgreSQL 异步驱动预留，本地文件实现以 async def 对齐。
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.governance.tenant.models import Tenant, TenantQuota


@runtime_checkable
class ITenantRepository(Protocol):
    """租户仓储：租户的创建、查询、更新、禁用与配额管理。"""

    async def create_tenant(self, tenant: Tenant) -> Tenant:
        """创建租户。

        前置条件：tenant_id 不重复。
        后置条件：租户记录持久化，可被 get_tenant 查询到。
        """
        ...

    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """按标识查询租户，不存在返回 None。"""
        ...

    async def list_tenants(self) -> List[Tenant]:
        """列出全部租户。"""
        ...

    async def update_tenant(self, tenant: Tenant) -> Tenant:
        """更新租户信息（名称、状态等）。"""
        ...

    async def disable_tenant(self, tenant_id: str) -> None:
        """禁用租户（状态置为 DISABLED）。"""
        ...

    async def get_quota(self, tenant_id: str) -> Optional[TenantQuota]:
        """查询租户配额。"""
        ...

    async def save_quota(self, quota: TenantQuota) -> TenantQuota:
        """保存或更新租户配额。"""
        ...
