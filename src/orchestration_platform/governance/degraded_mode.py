# =============================================================================
# 治理降级模式（governance/degraded_mode.py）
#
# 职责：未启用治理模块时以"单租户默认全权限"模式运行。
# 设计意图（对应 spec 5.5、design 2.8、任务 T77.1）：
#   1. 全部治理配置项默认关闭时自动初始化默认租户与默认全权限角色；
#   2. 降级模式下所有拦截器自动放行；
#   3. 行为与第二阶段一致。
# =============================================================================

from typing import Any, Dict

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.domain_event import DEFAULT_TENANT_ID
from orchestration_platform.domain.models.enums import (
    OperationType,
    ResourceScope,
    ResourceType,
    TenantStatus,
)
from orchestration_platform.governance.rbac.models import (
    PermissionItem,
    Role,
)
from orchestration_platform.governance.tenant.models import Tenant, TenantQuota


# B7 W2b（2026-11-22）：本地第二定义删除，统一引用 domain_event 权威常量
# （值 "tenant_default"）——修复与 bootstrap 权限种子既有不一致（原值
# "default"），降级租户与 RLS/backfill 口径正向收敛。
DEFAULT_TENANT_NAME = "默认租户"
DEFAULT_ADMIN_ROLE_ID = "default_admin_role"
DEFAULT_ADMIN_USER_ID = "default_admin"


class DegradedModeInitializer:
    """降级模式初始化器。

    未启用治理模块时初始化默认租户与默认全权限角色。
    """

    @staticmethod
    def is_degraded_mode() -> bool:
        """判断是否处于降级模式。

        全部治理配置项默认关闭时为降级模式。
        """
        settings = get_settings()
        return (
            not settings.rbac_enabled
            and not settings.tenant_isolation_enabled
            and not settings.metering_enabled
            and not settings.audit_enabled
            and not settings.global_scheduler_enabled
        )

    @staticmethod
    def create_default_tenant() -> Tenant:
        """创建默认租户。"""
        return Tenant(
            tenant_id=DEFAULT_TENANT_ID,
            tenant_name=DEFAULT_TENANT_NAME,
            status=TenantStatus.ACTIVE,
            is_default=True,
        )

    @staticmethod
    def create_default_quota() -> TenantQuota:
        """创建默认租户配额（无限制）。"""
        return TenantQuota(
            tenant_id=DEFAULT_TENANT_ID,
            max_instances=10000,
            max_workflows=100000,
            max_tokens_per_day=None,
            max_storage_gb=None,
        )

    @staticmethod
    def create_default_admin_role() -> Role:
        """创建默认全权限角色。

        包含全部资源类型的全部操作权限。
        """
        permissions = []
        for resource_type in ResourceType:
            for operation in OperationType:
                permissions.append(
                    PermissionItem(
                        permission_id=f"default_{resource_type.value}_{operation.value}",
                        resource_type=resource_type,
                        operation=operation,
                        scope=ResourceScope.TENANT,
                        description=f"默认全权限: {resource_type.value}:{operation.value}",
                    )
                )
        return Role(
            role_id=DEFAULT_ADMIN_ROLE_ID,
            role_name="默认管理员",
            tenant_id=DEFAULT_TENANT_ID,
            permissions=permissions,
            is_system_role=True,
        )

    @staticmethod
    def initialize_degraded_mode() -> Dict[str, Any]:
        """初始化降级模式。

        创建默认租户、默认配额与默认全权限角色。

        Returns:
            初始化结果字典。
        """
        if not DegradedModeInitializer.is_degraded_mode():
            return {"degraded_mode": False, "message": "治理模块已启用，非降级模式"}

        tenant = DegradedModeInitializer.create_default_tenant()
        quota = DegradedModeInitializer.create_default_quota()
        role = DegradedModeInitializer.create_default_admin_role()

        return {
            "degraded_mode": True,
            "tenant": tenant.model_dump(mode="json"),
            "quota": quota.model_dump(mode="json"),
            "role": role.model_dump(mode="json"),
            "message": "降级模式已初始化：单租户默认全权限",
        }


# =============================================================================
# 第四阶段：沙箱降级状态（偏差修正 T139）
# =============================================================================

class SandboxDegradedState:
    """沙箱降级状态判定。

    汇总容器驱动缺失/端点未配置/resource 模块缺失的降级判定。
    降级日志标明缺失组件名称与当前运行模式。
    """

    @staticmethod
    def is_container_degraded() -> bool:
        """容器物理隔离是否降级。"""
        settings = get_settings()
        if not settings.sandbox_enabled:
            return False
        if settings.sandbox_default_isolation != "physical":
            return False
        # 物理隔离但端点未配置
        return not settings.sandbox_container_endpoint

    @staticmethod
    def is_resource_meter_degraded() -> bool:
        """资源计量器是否降级（resource 模块缺失）。"""
        try:
            import resource  # noqa: F401
            return False
        except ImportError:
            return True

    @staticmethod
    def get_degraded_components() -> list:
        """获取降级组件列表。"""
        components = []
        if SandboxDegradedState.is_container_degraded():
            components.append({
                "component": "container_runtime",
                "reason": "容器端点未配置或驱动缺失",
                "mode": "logical_isolation",
            })
        if SandboxDegradedState.is_resource_meter_degraded():
            components.append({
                "component": "resource_meter",
                "reason": "resource 模块不可用",
                "mode": "time_output_only",
            })
        return components

    @staticmethod
    def get_degraded_summary() -> Dict[str, Any]:
        """获取降级状态汇总。"""
        components = SandboxDegradedState.get_degraded_components()
        return {
            "sandbox_degraded": len(components) > 0,
            "degraded_components": components,
            "message": "沙箱降级模式运行中" if components else "沙箱正常运行",
        }
