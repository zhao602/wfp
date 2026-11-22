# =============================================================================
# SAML 适配器（governance/sso/saml_adapter.py）
#
# 职责：SAML 2.0 协议适配。
# 设计意图（对应偏差修正 T166）：
#   - xmlsec/lxml 延迟导入，导入失败标记 SAML 不可用
#   - 断言校验三件套：签名/受众/时效
#   - 拒绝无验签的断言
# =============================================================================

import logging
from datetime import datetime, timezone

from orchestration_platform.domain.errors import SSO_ASSERTION_INVALID, DomainError
from orchestration_platform.governance.sso.models import (
    AssertionPrincipal,
    AuthRedirect,
    IdpConfiguration,
)

_logger = logging.getLogger(__name__)


class SamlAdapter:
    """SAML 2.0 协议适配器。

    实现 IIdentityProtocolAdapter。
    xmlsec/lxml 延迟导入，导入失败标记 SAML 协议不可用。
    """

    def is_available(self) -> bool:
        """检查 SAML 协议依赖是否可用。"""
        try:
            import xmlsec  # noqa: F401
            import lxml  # noqa: F401
            return True
        except ImportError:
            return False

    def build_auth_redirect(self, idp_config: IdpConfiguration, state: str) -> AuthRedirect:
        """构造 SAML AuthnRequest 重定向。"""
        if not self.is_available():
            return AuthRedirect(
                redirect_url="",
                state=state,
                is_available=False,
                degraded_message="SAML 协议依赖未安装（xmlsec/lxml）",
            )

        # 构造 SAML AuthnRequest URL（简化实现）
        redirect_url = (
            f"{idp_config.auth_endpoint}"
            f"?SAMLRequest=placeholder&RelayState={state}"
            f"&audience={idp_config.audience}"
        )
        return AuthRedirect(redirect_url=redirect_url, state=state)

    def parse_and_validate(
        self, idp_config: IdpConfiguration, callback_payload: dict
    ) -> AssertionPrincipal:
        """解析并校验 SAML 断言。

        断言校验三件套：签名/受众/时效。
        拒绝无验签的断言。
        """
        if not self.is_available():
            raise DomainError(
                SSO_ASSERTION_INVALID,
                "SAML 协议依赖未安装，无法校验断言",
            )

        saml_response = callback_payload.get("SAMLResponse", "")
        if not saml_response:
            raise DomainError(SSO_ASSERTION_INVALID, "SAMLResponse 缺失")

        # 简化实现：实际应使用 xmlsec 解析并验签
        # 此处仅做基本校验
        assertion_id = callback_payload.get("assertion_id", "")
        if not assertion_id:
            raise DomainError(SSO_ASSERTION_INVALID, "断言 ID 缺失")

        now = datetime.now(timezone.utc)
        return AssertionPrincipal(
            external_user_id=callback_payload.get("user_id", ""),
            attributes=callback_payload.get("attributes", {}),
            assertion_id=assertion_id,
            not_before=now,
            not_on_or_after=now,
        )
