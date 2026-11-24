# 销项（2026-11-18，arch_review_v2_tasks G3/CHG-06/CHG-14）：原 B2 族成员，已由 RBAC 落库接线为生产调用点（role 族注入 RbacPermissionChecker；user/tenant 族经 rbac/bootstrap 幂等初始化），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# 本地文件租户仓储（governance/persistence/local_file/local_file_tenant_repository.py）
#
# 职责：以 JSON 文件持久化租户与租户配额。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.3）：
#   1. 目录结构：{root}/governance/tenants/{tenant_id}.json
#   2. 配额存储：{root}/governance/tenants/{tenant_id}/quota.json
#   3. 用于无 PostgreSQL 环境与测试。
# =============================================================================

from pathlib import Path
from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.tenant.models import Tenant, TenantQuota


class LocalFileTenantRepository:
    """租户仓储——本地文件实现。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        """构造仓储实例。

        参数：
            storage_root —— 存储根目录；缺省读取配置 local_storage_root。
        """
        root = storage_root or get_settings().local_storage_root
        self._base_dir = Path(root) / "governance" / "tenants"

    def _tenant_file(self, tenant_id: str) -> Path:
        """返回租户文件路径。"""
        return self._base_dir / f"{tenant_id}.json"

    def _quota_file(self, tenant_id: str) -> Path:
        """返回配额文件路径。"""
        return self._base_dir / tenant_id / "quota.json"

    async def create_tenant(self, tenant: Tenant) -> Tenant:
        """创建租户。"""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._tenant_file(tenant.tenant_id)
        file_path.write_text(tenant.model_dump_json(indent=2), encoding="utf-8")
        return tenant

    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """按标识查询租户。"""
        file_path = self._tenant_file(tenant_id)
        if not file_path.exists():
            return None
        return Tenant.model_validate_json(file_path.read_text(encoding="utf-8"))

    async def list_tenants(self) -> List[Tenant]:
        """列出全部租户。"""
        if not self._base_dir.exists():
            return []
        tenants: List[Tenant] = []
        for file_path in self._base_dir.glob("*.json"):
            tenants.append(Tenant.model_validate_json(file_path.read_text(encoding="utf-8")))
        tenants.sort(key=lambda t: t.created_at)
        return tenants

    async def update_tenant(self, tenant: Tenant) -> Tenant:
        """更新租户信息。"""
        file_path = self._tenant_file(tenant.tenant_id)
        file_path.write_text(tenant.model_dump_json(indent=2), encoding="utf-8")
        return tenant

    async def disable_tenant(self, tenant_id: str) -> None:
        """禁用租户。"""
        tenant = await self.get_tenant(tenant_id)
        if tenant is None:
            return
        from orchestration_platform.domain.models.enums import TenantStatus
        tenant.status = TenantStatus.SUSPENDED
        await self.update_tenant(tenant)

    async def get_quota(self, tenant_id: str) -> Optional[TenantQuota]:
        """查询租户配额。"""
        file_path = self._quota_file(tenant_id)
        if not file_path.exists():
            return None
        return TenantQuota.model_validate_json(file_path.read_text(encoding="utf-8"))

    async def save_quota(self, quota: TenantQuota) -> TenantQuota:
        """保存或更新租户配额。"""
        file_path = self._quota_file(quota.tenant_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(quota.model_dump_json(indent=2), encoding="utf-8")
        return quota
