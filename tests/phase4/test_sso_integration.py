# =============================================================================
# SSO 集成测试（tests/phase4/test_sso_integration.py）
#
# 职责：验证 SSO 协议无关性、断言校验、JIT 开通、角色映射与降级。
# 设计意图（对应偏差修正 T173 / 验收 A121-A126, B108-B110）：
# =============================================================================

import asyncio
import unittest

from orchestration_platform.domain.errors import SSO_REPLAY_DETECTED, DomainError
from orchestration_platform.domain.models.enums import SsoProtocolType
from orchestration_platform.governance.sso.models import (
    AssertionPrincipal,
    IdpConfiguration,
)
from orchestration_platform.governance.sso.replay_guard import ReplayGuard
from orchestration_platform.governance.sso.jit_provisioner import (
    JitProvisioner,
    RoleMapper,
)
from datetime import datetime, timezone


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _utc_now():
    return datetime.now(timezone.utc)


class TestReplayGuard(unittest.TestCase):
    """防重放守卫测试。"""

    def test_first_assertion_passes(self):
        guard = ReplayGuard()
        guard.check("assertion-1")

    def test_repeated_assertion_rejected(self):
        guard = ReplayGuard()
        guard.check("assertion-1")
        with self.assertRaises(DomainError) as ctx:
            guard.check("assertion-1")
        self.assertEqual(ctx.exception.code, SSO_REPLAY_DETECTED)

    def test_different_assertions_pass(self):
        guard = ReplayGuard()
        guard.check("assertion-1")
        guard.check("assertion-2")


class TestRoleMapper(unittest.TestCase):
    """角色映射测试。"""

    def test_role_mapping(self):
        principal = AssertionPrincipal(
            external_user_id="user-1",
            attributes={"roles": "admin,auditor"},
            assertion_id="a-1",
            not_before=_utc_now(),
            not_on_or_after=_utc_now(),
        )
        config = IdpConfiguration(
            idp_id="idp-1",
            tenant_id="t-1",
            protocol_type=SsoProtocolType.SAML2,
            auth_endpoint="https://idp.example.com/auth",
            audience="sp-1",
            role_mappings={"admin": "platform_admin", "auditor": "platform_auditor"},
        )
        mapper = RoleMapper()
        roles = mapper.map_roles(principal, config)
        self.assertIn("platform_admin", roles)
        self.assertIn("platform_auditor", roles)

    def test_unmapped_role_ignored(self):
        principal = AssertionPrincipal(
            external_user_id="user-1",
            attributes={"roles": "unknown_role"},
            assertion_id="a-1",
            not_before=_utc_now(),
            not_on_or_after=_utc_now(),
        )
        config = IdpConfiguration(
            idp_id="idp-1",
            tenant_id="t-1",
            protocol_type=SsoProtocolType.SAML2,
            auth_endpoint="https://idp.example.com/auth",
            audience="sp-1",
            role_mappings={},
        )
        mapper = RoleMapper()
        roles = mapper.map_roles(principal, config)
        self.assertEqual(len(roles), 0)


class TestJitProvisioner(unittest.TestCase):
    """JIT 开通测试。"""

    def test_provision_with_valid_attributes(self):
        principal = AssertionPrincipal(
            external_user_id="user-1",
            attributes={"email": "user@example.com", "name": "User", "roles": "admin"},
            assertion_id="a-1",
            not_before=_utc_now(),
            not_on_or_after=_utc_now(),
        )
        config = IdpConfiguration(
            idp_id="idp-1",
            tenant_id="t-1",
            protocol_type=SsoProtocolType.OIDC,
            auth_endpoint="https://idp.example.com/auth",
            audience="sp-1",
            attribute_mappings={"email": "email", "name": "name"},
            role_mappings={"admin": "platform_admin"},
        )
        provisioner = JitProvisioner()
        result = provisioner.provision(principal, config)
        self.assertTrue(result["is_new"])
        self.assertIn("platform_admin", result["roles"])

    def test_provision_missing_required_attr_rejects(self):
        principal = AssertionPrincipal(
            external_user_id="user-1",
            attributes={},
            assertion_id="a-1",
            not_before=_utc_now(),
            not_on_or_after=_utc_now(),
        )
        config = IdpConfiguration(
            idp_id="idp-1",
            tenant_id="t-1",
            protocol_type=SsoProtocolType.OIDC,
            auth_endpoint="https://idp.example.com/auth",
            audience="sp-1",
        )
        provisioner = JitProvisioner()
        with self.assertRaises(DomainError) as ctx:
            provisioner.provision(principal, config)
        self.assertEqual(ctx.exception.code, "SSO_ATTR_MISSING")


class TestSsoCompatibility(unittest.TestCase):
    """SSO 兼容性测试。"""

    def test_sso_disabled_by_default(self):
        from orchestration_platform.configuration.settings import get_settings
        settings = get_settings()
        self.assertFalse(settings.sso_enabled)

    def test_enterprise_connector_disabled_by_default(self):
        from orchestration_platform.configuration.settings import get_settings
        settings = get_settings()
        self.assertFalse(settings.enterprise_connector_enabled)


if __name__ == "__main__":
    unittest.main()
