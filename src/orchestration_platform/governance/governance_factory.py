# =============================================================================
# 治理组件工厂（governance/governance_factory.py）
#
# 职责：按配置装配治理拦截器链与相关组件。
# 设计意图（对应 spec 5.1、design 2.2.2、任务 T56.4）：
#   1. 根据配置选择 PostgreSQL 或本地文件仓储实现；
#   2. 装配权限注册表、缓存、鉴权引擎、隔离器、配额校验器；
#   3. 构造拦截器链——六槽装配（鉴权 → 租户隔离 → 审计 → 配额 → 预算 → 计量；
#      审计/预算/计量为 roadmap B0/D3 原候选孤儿销项接线；
#      global_scheduler/abac 槽保持 None，装配点移交后续批次）；
#   4. 未启用治理时返回 None（降级模式）。
# =============================================================================

from typing import Optional

import logging

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.interceptors.auth_interceptor import (
    AuthInterceptor,
)
from orchestration_platform.governance.interceptors.base import InterceptorChain
from orchestration_platform.governance.interceptors.quota_interceptor import (
    QuotaInterceptor,
)
from orchestration_platform.governance.interceptors.tenant_isolation_interceptor import (
    TenantIsolationInterceptor,
)
from orchestration_platform.governance.rbac.permission_cache import PermissionCache
from orchestration_platform.governance.rbac.permission_checker import (
    RbacPermissionChecker,
)
from orchestration_platform.governance.rbac.permission_registry import (
    PermissionRegistry,
    create_default_registry,
)
from orchestration_platform.governance.tenant.quota_checker import QuotaChecker
from orchestration_platform.governance.tenant.tenant_isolator import TenantIsolator

logger = logging.getLogger(__name__)


def _create_role_repository(
    settings: object,
) -> Optional[object]:
    """按存储后端构造角色仓储（arch_review_v2_tasks G3/CHG-06，DC-06 规则 1）。

    复用 B2 族既有双后端实现（local_file/postgres，E9）；
    构造失败降级 None——checker 侧对 None 保持既有写空行为（fail-closed）。

    Args:
        settings: 应用配置（storage_backend / local_storage_root）。

    Returns:
        角色仓储实例，或 None（构造失败降级）。
    """
    try:
        if settings.storage_backend == "postgres":
            from orchestration_platform.governance.persistence.postgres.postgres_role_repository import (
                PostgresRoleRepository,
            )

            return PostgresRoleRepository()
        from orchestration_platform.governance.persistence.local_file.local_file_role_repository import (
            LocalFileRoleRepository,
        )

        return LocalFileRoleRepository(storage_root=settings.local_storage_root)
    except Exception as exc:
        logger.warning(
            "角色仓储构造失败（降级 None，鉴权查库写空 fail-closed）: %s",
            type(exc).__name__,
        )
        return None


def _create_metering_interceptor(
    settings: object,
) -> Optional[object]:
    """构造计量拦截器（roadmap B0/D3，原 B1 族候选孤儿销项接线）。

    复用既有 TokenMeter/UsageBufferQueue 组件（E9）；
    构造失败降级 None——链编排对 None 跳过该槽（降级放行）。

    Args:
        settings: 应用配置（metering_enabled）。

    Returns:
        计量拦截器实例，或 None（构造失败降级）。
    """
    try:
        from orchestration_platform.governance.interceptors.metering_interceptor import (
            MeteringInterceptor,
        )
        from orchestration_platform.governance.metering.token_meter import TokenMeter
        from orchestration_platform.governance.metering.usage_buffer_queue import (
            UsageBufferQueue,
        )

        return MeteringInterceptor(
            token_meter=TokenMeter(),
            buffer_queue=UsageBufferQueue(),
            enabled=settings.metering_enabled,
        )
    except Exception as exc:
        logger.warning(
            "计量拦截器构造失败（降级 None，链编排跳过该槽）: %s",
            type(exc).__name__,
        )
        return None


def _create_budget_interceptor(
    settings: object,
) -> Optional[object]:
    """构造预算拦截器（roadmap B0/D3，原 B1 族候选孤儿销项接线）。

    复用既有 BudgetGuard/BudgetManager 组件（E9）；
    构造失败降级 None——链编排对 None 跳过该槽（降级放行）。

    Args:
        settings: 应用配置（budget_fail_closed）。

    Returns:
        预算拦截器实例，或 None（构造失败降级）。
    """
    try:
        from orchestration_platform.governance.billing.budget_guard import BudgetGuard
        from orchestration_platform.governance.billing.budget_manager import (
            BudgetManager,
        )
        from orchestration_platform.governance.interceptors.budget_interceptor import (
            BudgetInterceptor,
        )

        return BudgetInterceptor(
            budget_guard=BudgetGuard(
                budget_manager=BudgetManager(),
                fail_closed=settings.budget_fail_closed,
            ),
            enabled=settings.budget_fail_closed,
        )
    except Exception as exc:
        logger.warning(
            "预算拦截器构造失败（降级 None，链编排跳过该槽）: %s",
            type(exc).__name__,
        )
        return None


def _create_audit_interceptor(
    settings: object,
) -> Optional[object]:
    """构造审计拦截器（roadmap B0/D3，原 B1 族候选孤儿销项接线）。

    复用既有 AuditLogger 组件（E9，内部自读配置）；
    构造失败降级 None——链编排对 None 跳过该槽（降级放行）。

    Args:
        settings: 应用配置（audit_enabled）。

    Returns:
        审计拦截器实例，或 None（构造失败降级）。
    """
    try:
        from orchestration_platform.governance.audit.audit_logger import AuditLogger
        from orchestration_platform.governance.interceptors.audit_interceptor import (
            AuditInterceptor,
        )

        return AuditInterceptor(
            audit_logger=AuditLogger(),
            enabled=settings.audit_enabled,
        )
    except Exception as exc:
        logger.warning(
            "审计拦截器构造失败（降级 None，链编排跳过该槽）: %s",
            type(exc).__name__,
        )
        return None


def create_governance_chain(
    storage_backend: Optional[str] = None,
) -> Optional[InterceptorChain]:
    """创建治理拦截器链。

    按配置装配鉴权、租户隔离、审计、配额、预算与计量拦截器（B0/D3 六槽装配；
    global_scheduler/abac 保持 None，装配点移交后续批次）。
    未启用治理时返回 None（降级模式）。

    Args:
        storage_backend: 存储后端类型 ("postgres" 或 "local_file")；
                         缺省从配置读取。

    Returns:
        拦截器链实例，或 None（降级模式）。
    """
    settings = get_settings()

    # 降级模式判断
    if not settings.rbac_enabled and not settings.tenant_isolation_enabled:
        return None

    # 1. 权限注册表与缓存
    registry: PermissionRegistry = create_default_registry()
    cache = PermissionCache(
        ttl_seconds=settings.rbac_permission_cache_ttl_seconds
    )

    # 2. 鉴权引擎
    # RBAC 启用时按 storage_backend 构造 B2 族角色仓储注入 checker（CHG-06，DC-06）：
    # authorize 缓存未命中查库（含组织继承）；未注入时 checker 保持既有写空行为。
    role_repository = None
    if settings.rbac_enabled:
        role_repository = _create_role_repository(settings)
    permission_checker = RbacPermissionChecker(
        registry=registry,
        cache=cache,
        role_repository=role_repository,
    )

    # 3. 租户隔离器与配额校验器
    isolator = TenantIsolator()
    quota_checker = QuotaChecker()

    # 4. 构造拦截器
    auth_interceptor = AuthInterceptor(
        permission_checker=permission_checker,
        enabled=settings.rbac_enabled,
    )
    tenant_isolation_interceptor = TenantIsolationInterceptor(
        isolator=isolator,
        enabled=settings.tenant_isolation_enabled,
    )
    quota_interceptor = QuotaInterceptor(
        quota_checker=quota_checker,
        enabled=settings.tenant_isolation_enabled,
    )

    # 4b. 审计/预算/计量拦截器（roadmap B0/D3 销项接线：原 B1 族候选孤儿
    # 经治理链成为生产调用点；构造失败各自降级 None，链编排跳过该槽）
    audit_interceptor = _create_audit_interceptor(settings)
    budget_interceptor = _create_budget_interceptor(settings)
    metering_interceptor = _create_metering_interceptor(settings)

    # 5. 构造拦截器链
    chain = InterceptorChain(
        auth=auth_interceptor,
        tenant_isolation=tenant_isolation_interceptor,
        audit=audit_interceptor,
        quota=quota_interceptor,
        budget=budget_interceptor,
        metering=metering_interceptor,
        fail_closed=True,
    )

    return chain
