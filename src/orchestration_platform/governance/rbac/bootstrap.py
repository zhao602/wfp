# =============================================================================
# RBAC 启动期幂等初始化（governance/rbac/bootstrap.py）
#
# 职责：开关开启时幂等初始化 RBAC 基础数据（arch_review_v2_tasks G3，DC-06 规则 3，
#       CHG-14，卡 A4）。
# 设计意图：
#   1. 幂等顺序：默认租户存在性确认 → 三个内置角色（admin/operator/viewer 的权限项
#      映射，来源 = PermissionRegistry 默认角色，E15 create_default_registry）
#      → 可选初始管理员绑定（env rbac_bootstrap_admin_user 指定时创建）；
#   2. 幂等语义：已存在的租户/角色/用户/绑定全部跳过（重复执行无重复数据）；
#      不主动失效缓存（启动期缓存为空，design §7.2）；
#   3. 开关守卫：由 settings.rbac_bootstrap_enabled 门控（默认 False，HC-07），
#      在 create_app() 装配段调用（开关开启时）；
#   4. 「开启即 403」修复语义：仓储为空（未跑 bootstrap）→ 用户无权限 → 403 属
#      正确 fail-closed 语义；部署前置步骤「先 bootstrap 再开 RBAC」（deploy.md）。
# =============================================================================

import logging
from typing import Any, Dict, Optional, Tuple

from orchestration_platform.configuration.settings import Settings
from orchestration_platform.domain.models.domain_event import DEFAULT_TENANT_ID
from orchestration_platform.governance.rbac.models import RoleBinding
from orchestration_platform.governance.rbac.permission_registry import (
    create_default_registry,
)
from orchestration_platform.governance.tenant.models import Tenant, User

logger = logging.getLogger(__name__)

# B7 W2b（2026-11-22）：本地定义改为引用 domain_event 权威常量
# （值 "tenant_default"，与 create_default_registry 内置角色 tenant_id 一致，E15）
DEFAULT_TENANT_NAME = "默认租户"
# 初始管理员绑定目标角色（PermissionRegistry 内置租户管理员角色）
ADMIN_ROLE_ID = "role_tenant_admin"


def _create_repositories(
    settings: Settings,
) -> Tuple[Optional[object], Optional[object], Optional[object]]:
    """按 storage_backend 构造 租户/角色/用户 三仓储（B2 族既有双后端，E9）。

    Returns:
        (tenant_repository, role_repository, user_repository)；
        对应后端模块缺失时该槽位为 None。
    """
    backend = settings.storage_backend
    try:
        if backend == "postgres":
            from orchestration_platform.governance.persistence.postgres.postgres_tenant_repository import (
                PostgresTenantRepository,
            )
            from orchestration_platform.governance.persistence.postgres.postgres_role_repository import (
                PostgresRoleRepository,
            )
            from orchestration_platform.governance.persistence.postgres.postgres_user_repository import (
                PostgresUserRepository,
            )

            return (
                PostgresTenantRepository(),
                PostgresRoleRepository(),
                PostgresUserRepository(),
            )
        from orchestration_platform.governance.persistence.local_file.local_file_tenant_repository import (
            LocalFileTenantRepository,
        )
        from orchestration_platform.governance.persistence.local_file.local_file_role_repository import (
            LocalFileRoleRepository,
        )
        from orchestration_platform.governance.persistence.local_file.local_file_user_repository import (
            LocalFileUserRepository,
        )

        return (
            LocalFileTenantRepository(storage_root=settings.local_storage_root),
            LocalFileRoleRepository(storage_root=settings.local_storage_root),
            LocalFileUserRepository(storage_root=settings.local_storage_root),
        )
    except Exception as exc:
        logger.error(
            "bootstrap 仓储构造失败: %s", type(exc).__name__
        )
        return (None, None, None)


async def initialize_rbac_bootstrap(settings: Settings) -> Dict[str, Any]:
    """RBAC 启动期幂等初始化（幂等：已存在即跳过，重复执行无重复数据）。

    Args:
        settings: 应用配置（storage_backend / rbac_bootstrap_admin_user）。

    Returns:
        初始化摘要：{tenant_created, roles_created, admin_user_created,
        admin_binding_created}（幂等重跑时各项均为 False/空）。

    Raises:
        RuntimeError: 角色或租户仓储不可用（无法初始化，fail-fast）。
    """
    summary: Dict[str, Any] = {
        "tenant_created": False,
        "roles_created": [],
        "admin_user_created": False,
        "admin_binding_created": False,
    }

    tenant_repository, role_repository, user_repository = _create_repositories(
        settings
    )
    if tenant_repository is None or role_repository is None:
        raise RuntimeError(
            "RBAC bootstrap 失败：租户/角色仓储不可用"
            f"（storage_backend={settings.storage_backend}）"
        )

    # 1. 默认租户存在性确认
    if await tenant_repository.get_tenant(DEFAULT_TENANT_ID) is None:
        await tenant_repository.create_tenant(
            Tenant(
                tenant_id=DEFAULT_TENANT_ID,
                tenant_name=DEFAULT_TENANT_NAME,
                is_default=True,
            )
        )
        summary["tenant_created"] = True
        logger.info("bootstrap：默认租户 %s 已创建", DEFAULT_TENANT_ID)

    # 2. 内置角色（admin/operator/viewer 权限项映射 PermissionRegistry 默认角色，E15）
    registry = create_default_registry()
    for role in registry.list_default_roles():
        if await role_repository.get_role(role.tenant_id, role.role_id) is None:
            await role_repository.create_role(role)
            summary["roles_created"].append(role.role_id)
            logger.info(
                "bootstrap：内置角色 %s/%s 已创建", role.tenant_id, role.role_id
            )

    # 3. 可选初始管理员绑定（env rbac_bootstrap_admin_user 指定时创建）
    admin_user_id = (settings.rbac_bootstrap_admin_user or "").strip()
    if admin_user_id:
        # 3a. 管理员用户存在性确认（缺失则创建，幂等）
        if user_repository is not None:
            if await user_repository.get_user(
                DEFAULT_TENANT_ID, admin_user_id
            ) is None:
                await user_repository.create_user(
                    User(
                        user_id=admin_user_id,
                        tenant_id=DEFAULT_TENANT_ID,
                        username=admin_user_id,
                        display_name=admin_user_id,
                    )
                )
                summary["admin_user_created"] = True
                logger.info(
                    "bootstrap：初始管理员用户 %s 已创建", admin_user_id
                )
        # 3b. 管理员绑定存在性确认（用户已持有管理员角色则跳过，幂等；
        #     不主动失效缓存——启动期缓存为空，design §7.2）
        existing_bindings = await role_repository.list_bindings_for_user(
            DEFAULT_TENANT_ID, admin_user_id
        )
        already_bound = any(
            binding.role_id == ADMIN_ROLE_ID for binding in existing_bindings
        )
        if not already_bound:
            await role_repository.create_binding(
                RoleBinding(
                    binding_id=f"binding_bootstrap_admin_{admin_user_id}",
                    role_id=ADMIN_ROLE_ID,
                    user_id=admin_user_id,
                    tenant_id=DEFAULT_TENANT_ID,
                )
            )
            summary["admin_binding_created"] = True
            logger.info(
                "bootstrap：初始管理员绑定 %s → %s 已创建",
                admin_user_id,
                ADMIN_ROLE_ID,
            )

    return summary
