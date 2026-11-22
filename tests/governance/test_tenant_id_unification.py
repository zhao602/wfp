# =============================================================================
# 双默认租户口径统一测试（tests/governance/test_tenant_id_unification.py）
#
# 职责：验证 B7 W2b——DEFAULT_TENANT_ID 单源化与值统一 "tenant_default"。
# 设计意图（对应 .codeartsdoer/specs/roadmap_b7_harvest spec W2b / design §3）：
#   1. 权威常量值 = "tenant_default"（domain_event 唯一定义）；
#   2. 三处引用单源：degraded_mode / rbac.bootstrap 引用同一对象
#      （本地第二定义已删除）；
#   3. 降级模式创建的默认租户/配额/角色与 bootstrap 权限种子口径正向收敛
#      （修复原 "default" vs "tenant_default" 不一致）。
# =============================================================================

from orchestration_platform.domain.models.domain_event import DEFAULT_TENANT_ID
from orchestration_platform.governance.degraded_mode import (
    DEFAULT_TENANT_ID as DEGRADED_DEFAULT_TENANT_ID,
    DegradedModeInitializer,
)
from orchestration_platform.governance.rbac import bootstrap


class TestTenantIdUnification:
    """W2b：默认租户标识单源 + 值统一。"""

    def test_authoritative_constant_value(self):
        assert DEFAULT_TENANT_ID == "tenant_default"

    def test_single_source_shared_constant(self):
        """degraded_mode / bootstrap 引用 domain_event 同一常量对象。"""
        assert DEGRADED_DEFAULT_TENANT_ID is DEFAULT_TENANT_ID, (
            "degraded_mode 本地第二定义已删除（B7 W2b）"
        )
        assert bootstrap.DEFAULT_TENANT_ID is DEFAULT_TENANT_ID, (
            "bootstrap 引用权威常量（B7 W2b）"
        )

    def test_degraded_mode_converges_to_tenant_default(self):
        """降级模式默认租户/配额/角色与 RLS/backfill 口径正向收敛。"""
        tenant = DegradedModeInitializer.create_default_tenant()
        quota = DegradedModeInitializer.create_default_quota()
        admin_role = DegradedModeInitializer.create_default_admin_role()

        assert tenant.tenant_id == "tenant_default"
        assert quota.tenant_id == "tenant_default"
        assert admin_role.tenant_id == "tenant_default", (
            "修复原降级口径 'default' 与权限种子 'tenant_default' 的不一致"
        )
