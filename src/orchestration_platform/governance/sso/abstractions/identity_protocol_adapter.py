# TODO(tech-debt): B档候选孤儿（族群 B5），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 协议适配器抽象（governance/sso/abstractions/identity_protocol_adapter.py）
# =============================================================================

from typing import Protocol, runtime_checkable

from orchestration_platform.governance.sso.models import (
    AssertionPrincipal,
    AuthRedirect,
    IdpConfiguration,
)


@runtime_checkable
class IIdentityProtocolAdapter(Protocol):
    """身份协议适配器抽象。

    SAML 2.0 与 OIDC 共用同一适配器契约。
    两种协议输出同一 AssertionPrincipal 统一断言视图。
    """

    def build_auth_redirect(self, idp_config: IdpConfiguration, state: str) -> AuthRedirect:
        """构造认证重定向。"""
        ...

    def parse_and_validate(
        self, idp_config: IdpConfiguration, callback_payload: dict
    ) -> AssertionPrincipal:
        """解析并校验回调载荷，返回统一断言视图。

        异常映射：SSO_ASSERTION_INVALID / SSO_REPLAY_DETECTED / SSO_DEGRADED
        """
        ...

    def is_available(self) -> bool:
        """协议依赖是否可用（xmlsec/cryptography 可导入且配置完整）。"""
        ...
