# =============================================================================
# JIT 开通与角色映射（governance/sso/jit_provisioner.py + role_mapper.py）
# =============================================================================

import logging
from typing import Dict, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import SSO_ATTR_MISSING, DomainError
from orchestration_platform.governance.sso.models import (
    AssertionPrincipal,
    IdpConfiguration,
)

_logger = logging.getLogger(__name__)


class RoleMapper:
    """角色映射器。

    按 IdpConfiguration.role_mappings 将 IdP 角色/组声明映射到既有 RBAC 角色。
    映射结果交由既有 RBAC/ABAC 引擎执行后续访问控制。
    """

    def map_roles(
        self,
        principal: AssertionPrincipal,
        idp_config: IdpConfiguration,
    ) -> list:
        """将 IdP 角色声明映射到 RBAC 角色。

        参数：
            principal   —— 断言主体（含属性声明）
            idp_config  —— IdP 配置（含角色映射规则）

        返回：
            RBAC 角色标识列表
        """
        mapped_roles = []
        idp_roles = principal.attributes.get("roles", "")

        for idp_role in idp_roles.split(","):
            idp_role = idp_role.strip()
            if not idp_role:
                continue
            rbac_role = idp_config.role_mappings.get(idp_role)
            if rbac_role:
                mapped_roles.append(rbac_role)

        return mapped_roles


class JitProvisioner:
    """JIT 开通器。

    断言用户在平台不存在对应账号时ECS时按 IdP 属性映射规则自动创建账号。
    """

    def __init__(self, role_mapper: Optional[RoleMapper] = None) -> None:
        """构造 JIT 开通器。"""
        self._role_mapper = role_mapper or RoleMapper()

    def provision(
        self,
        principal: AssertionPrincipal,
        idp_config: IdpConfiguration,
    ) -> dict:
        """JIT 开通或更新用户账号。

        参数：
            principal   —— 断言主体
            idp_config  —— IdP 配置

        返回：
            用户信息字典（user_id, is_new, roles）

        异常：
            DomainError(SSO_ATTR_MISSING) —— 必填属性缺失且无缺省角色
        """
        settings = get_settings()

        # 按属性映射提取平台用户字段
        user_fields = {}
        for idp_attr, platform_field in idp_config.attribute_mappings.items():
            value = principal.attributes.get(idp_attr)
            if value:
                user_fields[platform_field] = value

        # 检查必填属性
        required_fields = ["email", "name"]
        missing = [f for f in required_fields if f not in user_fields]
        if missing:
            default_role = idp_config.default_role_on_missing or settings.sso_default_role_on_missing_attr
            if default_role:
                _logger.warning("属性缺失，按缺省角色开通: missing=%s", missing)
                user_fields.setdefault("roles", [default_role])
            else:
                raise DomainError(
                    SSO_ATTR_MISSING,
                    "必填属性缺失，无法开通",
                    {"missing_attributes": missing},
                )

        # 角色映射
        roles = self._role_mapper.map_roles(principal, idp_config)
        user_fields["roles"] = roles

        # 简化实现：实际应调用用户仓储创建/更新账号
        user_id = f"sso:{principal.external_user_id}"
        is_new = True  # 简化：假设首次登录为新用户

        _logger.info("JIT 开通完成: user_id=%s is_new=%s", user_id, is_new)

        return {
            "user_id": user_id,
            "is_new": is_new,
            "roles": roles,
            "fields": user_fields,
        }
