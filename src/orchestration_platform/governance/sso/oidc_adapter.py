# =============================================================================
# OIDC 适配器（governance/sso/oidc_adapter.py）
#
# 职责：OpenID Connect 协议适配。
# 设计意图（对应偏差修正 T167 / roadmap S2 验签落地）：
#   - cryptography/PyJWT 延迟导入
#   - id_token/授权码双源；PyJWT 验签（签名/受众/时效/签发者）
#   - 算法白名单与凭据类型绑定（对称 secret→HS256，PEM→RS/ES 族）
#   - 与 SAML 路径异常语义一致（一律 SSO_ASSERTION_INVALID）
# =============================================================================

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from orchestration_platform.domain.errors import SSO_ASSERTION_INVALID, DomainError
from orchestration_platform.governance.sso.models import (
    AssertionPrincipal,
    AuthRedirect,
    IdpConfiguration,
)

_logger = logging.getLogger(__name__)

# 非对称凭据（证书/公钥 PEM）允许的验签算法白名单
_ASYMMETRIC_ALGS = ("RS256", "RS512", "ES256", "ES512")

# claims → attributes 时跳过的保留声明
_RESERVED_CLAIMS = {"iss", "sub", "aud", "exp", "nbf", "iat", "jti"}


class OidcAdapter:
    """OIDC 协议适配器。

    实现 IIdentityProtocolAdapter。
    cryptography/PyJWT 延迟导入，导入失败标记 OIDC 协议不可用。
    """

    def __init__(self, http_client: Optional[httpx.Client] = None) -> None:
        """构造 OIDC 适配器。

        参数：
            http_client —— 可注入 HTTP 客户端（测试替身）；缺省按需创建
        """
        self._http_client = http_client

    def is_available(self) -> bool:
        """检查 OIDC 协议依赖是否可用。"""
        try:
            import jwt  # noqa: F401
            return True
        except ImportError:
            return False

    def build_auth_redirect(self, idp_config: IdpConfiguration, state: str) -> AuthRedirect:
        """构造 OIDC 授权 URL（授权码流）。"""
        if not self.is_available():
            return AuthRedirect(
                redirect_url="",
                state=state,
                is_available=False,
                degraded_message="OIDC 协议依赖未安装（PyJWT）",
            )

        redirect_url = (
            f"{idp_config.auth_endpoint}"
            f"?response_type=code&client_id={idp_config.audience}"
            f"&state={state}&scope=openid+profile+email"
        )
        return AuthRedirect(redirect_url=redirect_url, state=state)

    def parse_and_validate(
        self, idp_config: IdpConfiguration, callback_payload: dict
    ) -> AssertionPrincipal:
        """解析并校验 OIDC 回调。

        双源：id_token 直传或授权码经 token_endpoint 换取。
        PyJWT 验签：签名/受众/exp（必填）/签发者（配置 oidc_issuer 时）。
        断言标识与用户标识一律服务端从已验签令牌提取。
        """
        if not self.is_available():
            raise DomainError(
                SSO_ASSERTION_INVALID,
                "OIDC 协议依赖未安装，无法校验令牌",
            )

        id_token = callback_payload.get("id_token", "")
        code = callback_payload.get("code", "")

        if id_token:
            token = id_token
        elif code:
            token = self._exchange_code_for_token(idp_config, code)
        else:
            raise DomainError(SSO_ASSERTION_INVALID, "id_token 与授权码均缺失")

        claims = self._verify_id_token(idp_config, token)

        external_user_id = str(claims.get("sub", ""))
        if not external_user_id:
            raise DomainError(SSO_ASSERTION_INVALID, "id_token sub 声明缺失")

        # 断言标识服务端提取：jti 优先，缺失时退化为令牌摘要
        assertion_id = str(claims.get("jti", "")) or hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()[:32]

        now = datetime.now(timezone.utc)
        expires_at = claims.get("exp")
        not_on_or_after = (
            datetime.fromtimestamp(expires_at, tz=timezone.utc) if expires_at else now
        )
        issued_at = claims.get("nbf") or claims.get("iat")
        not_before = (
            datetime.fromtimestamp(issued_at, tz=timezone.utc) if issued_at else now
        )

        # claims → attributes：保留声明跳过；标量取字符串，多值逗号拼接
        attributes = {}
        for claim_name, claim_value in claims.items():
            if claim_name in _RESERVED_CLAIMS:
                continue
            if claim_value is None or isinstance(claim_value, bool):
                continue
            if isinstance(claim_value, (list, tuple)):
                joined = ",".join(
                    str(item)
                    for item in claim_value
                    if item is not None and not isinstance(item, bool)
                )
                if joined:
                    attributes[claim_name] = joined
            elif isinstance(claim_value, (str, int, float)):
                attributes[claim_name] = str(claim_value)

        _logger.info("OIDC 令牌验签通过: sub=%s", external_user_id)

        return AssertionPrincipal(
            external_user_id=external_user_id,
            attributes=attributes,
            assertion_id=assertion_id,
            not_before=not_before,
            not_on_or_after=not_on_or_after,
        )

    def _exchange_code_for_token(self, idp_config: IdpConfiguration, code: str) -> str:
        """授权码经 token_endpoint 换取 id_token（同步 POST）。"""
        if not idp_config.token_endpoint:
            raise DomainError(SSO_ASSERTION_INVALID, "token_endpoint 未配置，无法换取令牌")

        form_data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": idp_config.audience,
        }
        credential = (idp_config.encrypted_credential or "").strip()
        if credential and not credential.startswith("-----BEGIN"):
            form_data["client_secret"] = credential

        try:
            if self._http_client is not None:
                response = self._http_client.post(idp_config.token_endpoint, data=form_data)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(idp_config.token_endpoint, data=form_data)
        except httpx.HTTPError as exc:
            _logger.warning("token_endpoint 请求失败: %s", exc)
            raise DomainError(SSO_ASSERTION_INVALID, "token_endpoint 请求失败")

        if response.status_code != 200:
            raise DomainError(
                SSO_ASSERTION_INVALID,
                f"token_endpoint 返回非 200: {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError:
            raise DomainError(SSO_ASSERTION_INVALID, "token_endpoint 响应非 JSON")
        token = payload.get("id_token", "")
        if not token:
            raise DomainError(SSO_ASSERTION_INVALID, "token_endpoint 未返回 id_token")
        return token

    def _verify_id_token(self, idp_config: IdpConfiguration, token: str) -> dict:
        """PyJWT 验签 id_token，返回 claims。"""
        import jwt
        from jwt.exceptions import InvalidTokenError

        credential = (idp_config.encrypted_credential or "").strip()
        if not credential:
            raise DomainError(SSO_ASSERTION_INVALID, "OIDC 校验凭据缺失，拒绝")

        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError:
            raise DomainError(SSO_ASSERTION_INVALID, "id_token 头部解析失败")

        alg = str(header.get("alg", ""))

        if credential.startswith("-----BEGIN"):
            # 非对称凭据：证书/公钥 PEM，算法必须命中白名单
            if alg not in _ASYMMETRIC_ALGS:
                raise DomainError(
                    SSO_ASSERTION_INVALID,
                    f"算法不在非对称白名单: {alg}",
                )
            key: object = self._load_asymmetric_key(credential)
        else:
            # 对称凭据：client_secret 仅允许 HS256
            if alg != "HS256":
                raise DomainError(SSO_ASSERTION_INVALID, f"算法与对称凭据不匹配: {alg}")
            key = credential

        try:
            return jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=idp_config.audience,
                issuer=idp_config.oidc_issuer or None,
                options={"require": ["exp", "sub"]},
            )
        except InvalidTokenError as exc:
            _logger.warning("id_token 校验失败: %s", exc)
            raise DomainError(SSO_ASSERTION_INVALID, "id_token 校验失败，拒绝")

    @staticmethod
    def _load_asymmetric_key(credential_pem: str) -> object:
        """从证书/公钥 PEM 加载验签公钥。"""
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        if "BEGIN CERTIFICATE" in credential_pem:
            certificate = x509.load_pem_x509_certificate(credential_pem.encode("utf-8"))
            return certificate.public_key()
        if "BEGIN PUBLIC KEY" in credential_pem:
            return load_pem_public_key(credential_pem.encode("utf-8"))
        raise DomainError(SSO_ASSERTION_INVALID, "OIDC 凭据 PEM 无法识别（需证书或公钥）")
