# =============================================================================
# SSO 服务（governance/sso/sso_service.py）
#
# 职责：协调协议适配器、防重放、JIT 开通与角色映射。
# 设计意图（对应偏差修正 T170 / design 2.2.2.4）：
#   - initiate：构造认证重定向
#   - handle_callback：适配器校验→防重放→JIT→角色映射→签发平台会话令牌
#   - sso_enabled=False 时服务不装配
# =============================================================================

import logging
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import IDP_UNREACHABLE, SSO_DEGRADED, DomainError
from orchestration_platform.domain.models.enums import SsoProtocolType
from orchestration_platform.governance.sso.idp_config_manager import IdpConfigManager
from orchestration_platform.governance.sso.jit_provisioner import JitProvisioner
from orchestration_platform.governance.sso.models import (
    AuthRedirect,
    IdpConfiguration,
    SsoLoginResult,
)
from orchestration_platform.governance.sso.oidc_adapter import OidcAdapter
from orchestration_platform.governance.sso.replay_guard import ReplayGuard
from orchestration_platform.governance.sso.saml_adapter import SamlAdapter

_logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SsoService:
    """SSO 服务。

    按 IdpConfiguration.protocol_type 选择适配器。
    主流程协议无关：适配器校验→防重放→JIT→角色映射→签发平台会话令牌。
    """

    def __init__(
        self,
        config_manager: Optional[IdpConfigManager] = None,
        replay_guard: Optional[ReplayGuard] = None,
        jit_provisioner: Optional[JitProvisioner] = None,
    ) -> None:
        """构造 SSO 服务。"""
        self._config_manager = config_manager or IdpConfigManager()
        self._replay_guard = replay_guard or ReplayGuard()
        self._jit_provisioner = jit_provisioner or JitProvisioner()
        self._saml_adapter = SamlAdapter()
        self._oidc_adapter = OidcAdapter()

    def initiate(self, idp_id: str) -> AuthRedirect:
        """构造认证重定向。

        IdP 未配置或不可达返回引导信息。
        """
        config = self._config_manager.get(idp_id)
        if config is None or not config.enabled:
            return AuthRedirect(
                redirect_url="",
                state="",
                is_available=False,
                degraded_message="企业登录暂不可用，可使用本地账号登录",
            )

        state = hashlib.sha256(f"{idp_id}:{_utc_now().isoformat()}".encode()).hexdigest()

        if config.protocol_type == SsoProtocolType.SAML2:
            return self._saml_adapter.build_auth_redirect(config, state)
        elif config.protocol_type == SsoProtocolType.OIDC:
            return self._oidc_adapter.build_auth_redirect(config, state)
        else:
            return AuthRedirect(
                redirect_url="",
                state=state,
                is_available=False,
                degraded_message=f"不支持的协议类型: {config.protocol_type}",
            )

    async def handle_callback(self, idp_id: str, callback_payload: dict) -> SsoLoginResult:
        """处理 IdP 回调。

        主流程协议无关：
        适配器校验→防重放→JIT→角色映射→签发平台会话令牌。
        """
        config = self._config_manager.get_with_credential(idp_id)
        if config is None:
            return SsoLoginResult(
                success=False,
                error_code="IDP_UNREACHABLE",
                error_message="IdP 配置不存在",
            )

        # 选择适配器
        if config.protocol_type == SsoProtocolType.SAML2:
            adapter = self._saml_adapter
        elif config.protocol_type == SsoProtocolType.OIDC:
            adapter = self._oidc_adapter
        else:
            return SsoLoginResult(
                success=False,
                error_code="SSO_DEGRADED",
                error_message=f"不支持的协议类型: {config.protocol_type}",
            )

        # 适配器校验
        try:
            principal = adapter.parse_and_validate(config, callback_payload)
        except DomainError as exc:
            return SsoLoginResult(
                success=False,
                error_code=exc.code,
                error_message=exc.message,
            )

        # 防重放检查
        try:
            self._replay_guard.check(principal.assertion_id)
        except DomainError as exc:
            return SsoLoginResult(
                success=False,
                error_code=exc.code,
                error_message=exc.message,
            )

        # JIT 开通
        try:
            user_info = self._jit_provisioner.provision(principal, config)
        except DomainError as exc:
            return SsoLoginResult(
                success=False,
                error_code=exc.code,
                error_message=exc.message,
            )

        # 签发平台会话令牌（复用既有格式 tenant_id:user_id:expires）
        settings = get_settings()
        expires_at = _utc_now() + timedelta(seconds=settings.sso_session_ttl_seconds)
        token = f"{config.tenant_id}:{user_info['user_id']}:{expires_at.isoformat()}"

        _logger.info(
            "SSO 登录成功: user_id=%s is_new=%s",
            user_info["user_id"],
            user_info["is_new"],
        )

        return SsoLoginResult(
            success=True,
            token=token,
            expires_at=expires_at,
            user_id=user_info["user_id"],
            is_new_user=user_info["is_new"],
        )
