# =============================================================================
# SSO 验签测试（tests/phase4/test_sso_verification.py）
#
# 职责：SAML/OIDC 真实验签正负路径 + SsoService e2e（roadmap S2）。
# 负路径全覆盖：篡改/错证书/无签名/错受众/过期/未生效/缺时效/包裹攻击/
# 算法混淆/错密钥/重放全拒。
# =============================================================================

import asyncio
import base64
import time
import unittest
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import xmlsec
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.x509.oid import NameOID
from lxml import etree

from orchestration_platform.domain.errors import (
    SSO_ASSERTION_INVALID,
    SSO_REPLAY_DETECTED,
    DomainError,
)
from orchestration_platform.domain.models.enums import SsoProtocolType
from orchestration_platform.governance.sso.models import IdpConfiguration
from orchestration_platform.governance.sso.oidc_adapter import OidcAdapter
from orchestration_platform.governance.sso.saml_adapter import SamlAdapter
from orchestration_platform.governance.sso.sso_service import SsoService

_SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
_SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"

_AUDIENCE = "https://sp.s2.example"
_IDP_ENTITY = "https://idp.s2.example"
_HS_SECRET = "s2-test-client-secret-0123456789abcdef"


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _utc_now():
    return datetime.now(timezone.utc)


def _generate_key_and_cert(common_name: str):
    """生成 RSA-2048 自签名密钥对，返回 (私钥 PEM, 证书 PEM)。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = _utc_now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    return key_pem, cert_pem


_IDP_KEY_PEM, IDP_CERT_PEM = _generate_key_and_cert("S2 Test IdP")
_OTHER_KEY_PEM, _OTHER_CERT_PEM = _generate_key_and_cert("S2 Other IdP")


def _saml_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _reencode_replacing(b64_text: str, old: str, new: str) -> str:
    """解码→字节级替换→重编码（模拟篡改，不重签）。"""
    raw = base64.b64decode(b64_text)
    return base64.b64encode(raw.replace(old.encode("utf-8"), new.encode("utf-8"))).decode(
        "utf-8"
    )


def _build_saml_response(
    *,
    assertion_id: str = "_assertion_1",
    audience: str = _AUDIENCE,
    not_before=None,
    not_on_or_after=None,
    omit_not_on_or_after: bool = False,
    name_id: str = "user@s2.example",
    attributes=None,
    status_success: bool = True,
    sign_assertion: bool = True,
    sign_response: bool = False,
    signing_key_pem: str = _IDP_KEY_PEM,
) -> str:
    """构造（可选签名）SAML Response，返回 base64 文本。"""
    now = _utc_now()
    if not_before is None:
        not_before = now - timedelta(minutes=5)
    if not_on_or_after is None and not omit_not_on_or_after:
        not_on_or_after = now + timedelta(minutes=10)
    if attributes is None:
        attributes = {
            "email": ["user@s2.example"],
            "name": ["S2 User"],
            "groups": ["g1", "g2"],
        }

    conditions_attrs = ""
    if not_before is not None:
        conditions_attrs += f' NotBefore="{_saml_timestamp(not_before)}"'
    if not_on_or_after is not None:
        conditions_attrs += f' NotOnOrAfter="{_saml_timestamp(not_on_or_after)}"'

    status_value = (
        "urn:oasis:names:tc:SAML:2.0:status:Success"
        if status_success
        else "urn:oasis:names:tc:SAML:2.0:status:Responder"
    )

    attribute_xml = ""
    for attr_name, values in attributes.items():
        values_xml = "".join(
            f"<saml:AttributeValue>{v}</saml:AttributeValue>" for v in values
        )
        attribute_xml += f'<saml:Attribute Name="{attr_name}">{values_xml}</saml:Attribute>'

    response_xml = (
        f'<samlp:Response xmlns:samlp="{_SAMLP_NS}" xmlns:saml="{_SAML_NS}"'
        f' ID="_response_1" Version="2.0" IssueInstant="{_saml_timestamp(now)}">'
        f"<saml:Issuer>{_IDP_ENTITY}</saml:Issuer>"
        f'<samlp:Status><samlp:StatusCode Value="{status_value}"/></samlp:Status>'
        f'<saml:Assertion ID="{assertion_id}" Version="2.0"'
        f' IssueInstant="{_saml_timestamp(now)}">'
        f"<saml:Issuer>{_IDP_ENTITY}</saml:Issuer>"
        f"<saml:Subject><saml:NameID>{name_id}</saml:NameID></saml:Subject>"
        f"<saml:Conditions{conditions_attrs}>"
        f"<saml:AudienceRestriction>"
        f"<saml:Audience>{audience}</saml:Audience>"
        f"</saml:AudienceRestriction>"
        f"</saml:Conditions>"
        f"<saml:AttributeStatement>{attribute_xml}</saml:AttributeStatement>"
        f"</saml:Assertion></samlp:Response>"
    )

    root = etree.fromstring(response_xml.encode("utf-8"))
    xmlsec.tree.add_ids(root, ["ID"])

    # 断言级签名（先签内层）
    if sign_assertion:
        assertion = root.find(f"{{{_SAML_NS}}}Assertion")
        signature = xmlsec.template.create(
            assertion, xmlsec.constants.TransformExclC14N, xmlsec.constants.TransformRsaSha256
        )
        reference = xmlsec.template.add_reference(
            signature, xmlsec.constants.TransformSha256, uri=f"#{assertion_id}"
        )
        xmlsec.template.add_transform(reference, xmlsec.constants.TransformEnveloped)
        xmlsec.template.add_transform(reference, xmlsec.constants.TransformExclC14N)
        assertion.insert(1, signature)
        ctx = xmlsec.SignatureContext()
        ctx.key = xmlsec.Key.from_memory(signing_key_pem, xmlsec.KeyFormat.PEM, None)
        ctx.sign(signature)

    # 响应级签名（后签外层）
    if sign_response:
        signature = xmlsec.template.create(
            root, xmlsec.constants.TransformExclC14N, xmlsec.constants.TransformRsaSha256
        )
        reference = xmlsec.template.add_reference(
            signature, xmlsec.constants.TransformSha256, uri="#_response_1"
        )
        xmlsec.template.add_transform(reference, xmlsec.constants.TransformEnveloped)
        xmlsec.template.add_transform(reference, xmlsec.constants.TransformExclC14N)
        root.insert(1, signature)
        ctx = xmlsec.SignatureContext()
        ctx.key = xmlsec.Key.from_memory(signing_key_pem, xmlsec.KeyFormat.PEM, None)
        ctx.sign(signature)

    return base64.b64encode(etree.tostring(root)).decode("utf-8")


def _saml_config(credential: str = IDP_CERT_PEM) -> IdpConfiguration:
    return IdpConfiguration(
        idp_id="idp-saml-1",
        tenant_id="t-1",
        protocol_type=SsoProtocolType.SAML2,
        auth_endpoint="https://idp.s2.example/auth",
        audience=_AUDIENCE,
        encrypted_credential=credential,
    )


def _oidc_config(
    credential: str,
    oidc_issuer: str = "",
    token_endpoint: str = "",
) -> IdpConfiguration:
    return IdpConfiguration(
        idp_id="idp-oidc-1",
        tenant_id="t-1",
        protocol_type=SsoProtocolType.OIDC,
        auth_endpoint="https://idp.s2.example/auth",
        token_endpoint=token_endpoint,
        audience=_AUDIENCE,
        oidc_issuer=oidc_issuer,
        encrypted_credential=credential,
    )


def _build_oidc_token(
    *,
    key,
    algorithm: str = "HS256",
    sub: str = "user-oidc-1",
    audience: str = _AUDIENCE,
    issuer: str = _IDP_ENTITY,
    expires_in: int = 3600,
) -> str:
    now_ts = int(time.time())
    claims = {
        "iss": issuer,
        "sub": sub,
        "aud": audience,
        "exp": now_ts + expires_in,
        "iat": now_ts,
        "jti": "jti-oidc-1",
        "email": "user@s2.example",
        "name": "S2 User",
    }
    return jwt.encode(claims, key, algorithm=algorithm)


class TestSamlVerification(unittest.TestCase):
    """SAML 真实验签测试。"""

    def setUp(self):
        self.adapter = SamlAdapter()

    def _expect_reject(self, config, payload):
        with self.assertRaises(DomainError) as ctx:
            self.adapter.parse_and_validate(config, payload)
        self.assertEqual(ctx.exception.code, SSO_ASSERTION_INVALID)

    def test_assertion_signed_roundtrip(self):
        nb = _utc_now().replace(microsecond=0) - timedelta(minutes=5)
        noa = _utc_now().replace(microsecond=0) + timedelta(minutes=10)
        response_b64 = _build_saml_response(not_before=nb, not_on_or_after=noa)
        principal = self.adapter.parse_and_validate(
            _saml_config(), {"SAMLResponse": response_b64}
        )
        self.assertEqual(principal.external_user_id, "user@s2.example")
        self.assertEqual(principal.assertion_id, "_assertion_1")
        self.assertEqual(principal.attributes["email"], "user@s2.example")
        self.assertEqual(principal.attributes["groups"], "g1,g2")
        self.assertEqual(principal.not_before, nb)
        self.assertEqual(principal.not_on_or_after, noa)

    def test_response_signed_roundtrip(self):
        response_b64 = _build_saml_response(sign_assertion=False, sign_response=True)
        principal = self.adapter.parse_and_validate(
            _saml_config(), {"SAMLResponse": response_b64}
        )
        self.assertEqual(principal.external_user_id, "user@s2.example")
        self.assertEqual(principal.assertion_id, "_assertion_1")

    def test_unsigned_rejected(self):
        response_b64 = _build_saml_response(sign_assertion=False, sign_response=False)
        self._expect_reject(_saml_config(), {"SAMLResponse": response_b64})

    def test_tampered_assertion_rejected(self):
        response_b64 = _build_saml_response(name_id="nameid-marker-1")
        tampered = _reencode_replacing(response_b64, "nameid-marker-1", "attacker-marker-1")
        self._expect_reject(_saml_config(), {"SAMLResponse": tampered})

    def test_wrong_certificate_rejected(self):
        response_b64 = _build_saml_response(signing_key_pem=_OTHER_KEY_PEM)
        self._expect_reject(_saml_config(), {"SAMLResponse": response_b64})

    def test_audience_mismatch_rejected(self):
        response_b64 = _build_saml_response(audience="https://other-sp.example")
        self._expect_reject(_saml_config(), {"SAMLResponse": response_b64})

    def test_expired_rejected(self):
        now = _utc_now().replace(microsecond=0)
        response_b64 = _build_saml_response(
            not_before=now - timedelta(hours=2),
            not_on_or_after=now - timedelta(minutes=5),
        )
        self._expect_reject(_saml_config(), {"SAMLResponse": response_b64})

    def test_not_yet_valid_rejected(self):
        now = _utc_now().replace(microsecond=0)
        response_b64 = _build_saml_response(
            not_before=now + timedelta(minutes=10),
            not_on_or_after=now + timedelta(hours=1),
        )
        self._expect_reject(_saml_config(), {"SAMLResponse": response_b64})

    def test_missing_not_on_or_after_rejected(self):
        response_b64 = _build_saml_response(omit_not_on_or_after=True)
        self._expect_reject(_saml_config(), {"SAMLResponse": response_b64})

    def test_non_success_status_rejected(self):
        response_b64 = _build_saml_response(status_success=False)
        self._expect_reject(_saml_config(), {"SAMLResponse": response_b64})

    def test_bad_base64_rejected(self):
        self._expect_reject(_saml_config(), {"SAMLResponse": "%%%not-base64%%%"})

    def test_missing_credential_rejected(self):
        response_b64 = _build_saml_response()
        self._expect_reject(_saml_config(credential=""), {"SAMLResponse": response_b64})

    def test_missing_saml_response_rejected(self):
        self._expect_reject(_saml_config(), {})

    def test_wrapped_reference_rejected(self):
        response_b64 = _build_saml_response()
        wrapped = _reencode_replacing(response_b64, ' ID="_assertion_1"', ' ID="_assertion_2"')
        self._expect_reject(_saml_config(), {"SAMLResponse": wrapped})


class TestOidcVerification(unittest.TestCase):
    """OIDC 真实验签测试。"""

    def setUp(self):
        self.adapter = OidcAdapter()

    def _expect_reject(self, config, payload):
        with self.assertRaises(DomainError) as ctx:
            self.adapter.parse_and_validate(config, payload)
        self.assertEqual(ctx.exception.code, SSO_ASSERTION_INVALID)

    def test_hs256_roundtrip_with_issuer(self):
        config = _oidc_config(credential=_HS_SECRET, oidc_issuer=_IDP_ENTITY)
        token = _build_oidc_token(key=_HS_SECRET)
        principal = self.adapter.parse_and_validate(config, {"id_token": token})
        self.assertEqual(principal.external_user_id, "user-oidc-1")
        self.assertEqual(principal.assertion_id, "jti-oidc-1")
        self.assertEqual(principal.attributes["email"], "user@s2.example")
        self.assertEqual(principal.attributes["name"], "S2 User")
        self.assertNotIn("iss", principal.attributes)
        self.assertTrue(principal.not_on_or_after > _utc_now())

    def test_rs256_certificate_roundtrip(self):
        private_key = load_pem_private_key(_IDP_KEY_PEM.encode("utf-8"), password=None)
        token = _build_oidc_token(key=private_key, algorithm="RS256")
        config = _oidc_config(credential=IDP_CERT_PEM, oidc_issuer=_IDP_ENTITY)
        principal = self.adapter.parse_and_validate(config, {"id_token": token})
        self.assertEqual(principal.external_user_id, "user-oidc-1")
        self.assertEqual(principal.assertion_id, "jti-oidc-1")

    def test_tampered_token_rejected(self):
        token = _build_oidc_token(key=_HS_SECRET)
        header, payload, signature = token.split(".")
        flipped = "A" if not payload.startswith("A") else "B"
        tampered = f"{header}.{flipped}{payload[1:]}.{signature}"
        self._expect_reject(
            _oidc_config(credential=_HS_SECRET, oidc_issuer=_IDP_ENTITY),
            {"id_token": tampered},
        )

    def test_wrong_key_rejected(self):
        token = _build_oidc_token(key="another-secret-2-0123456789abcdefghij")
        self._expect_reject(
            _oidc_config(credential=_HS_SECRET, oidc_issuer=_IDP_ENTITY),
            {"id_token": token},
        )

    def test_audience_mismatch_rejected(self):
        token = _build_oidc_token(key=_HS_SECRET, audience="https://other-client.example")
        self._expect_reject(
            _oidc_config(credential=_HS_SECRET, oidc_issuer=_IDP_ENTITY),
            {"id_token": token},
        )

    def test_expired_token_rejected(self):
        token = _build_oidc_token(key=_HS_SECRET, expires_in=-3600)
        self._expect_reject(
            _oidc_config(credential=_HS_SECRET, oidc_issuer=_IDP_ENTITY),
            {"id_token": token},
        )

    def test_issuer_mismatch_rejected(self):
        token = _build_oidc_token(key=_HS_SECRET, issuer="https://evil.example")
        self._expect_reject(
            _oidc_config(credential=_HS_SECRET, oidc_issuer=_IDP_ENTITY),
            {"id_token": token},
        )

    def test_alg_confusion_rejected(self):
        private_key = load_pem_private_key(_IDP_KEY_PEM.encode("utf-8"), password=None)
        token = _build_oidc_token(key=private_key, algorithm="RS256")
        self._expect_reject(
            _oidc_config(credential=_HS_SECRET, oidc_issuer=_IDP_ENTITY),
            {"id_token": token},
        )

    def test_code_exchange_roundtrip(self):
        token = _build_oidc_token(key=_HS_SECRET)
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode("utf-8")
            return httpx.Response(200, json={"id_token": token})

        adapter = OidcAdapter(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        config = _oidc_config(
            credential=_HS_SECRET,
            oidc_issuer=_IDP_ENTITY,
            token_endpoint="https://idp.s2.example/token",
        )
        principal = adapter.parse_and_validate(config, {"code": "auth-code-1"})
        self.assertEqual(principal.external_user_id, "user-oidc-1")
        self.assertEqual(principal.assertion_id, "jti-oidc-1")
        self.assertIn("grant_type=authorization_code", captured["body"])
        self.assertIn("code=auth-code-1", captured["body"])
        self.assertIn(f"client_secret={_HS_SECRET}", captured["body"])

    def test_missing_token_endpoint_rejected(self):
        self._expect_reject(_oidc_config(credential=_HS_SECRET), {"code": "auth-code-1"})

    def test_missing_code_and_token_rejected(self):
        self._expect_reject(_oidc_config(credential=_HS_SECRET), {})


class _StaticConfigManager:
    """测试替身：固定返回预置 IdP 配置。"""

    def __init__(self, config: IdpConfiguration):
        self._config = config

    def get_with_credential(self, idp_id: str):
        return self._config if idp_id == self._config.idp_id else None


class TestSsoServiceEndToEnd(unittest.TestCase):
    """SsoService e2e：已签名断言全链路成功 + 重放拒绝。"""

    def test_handle_callback_saml_signed_success(self):
        config = IdpConfiguration(
            idp_id="idp-saml-1",
            tenant_id="t-1",
            protocol_type=SsoProtocolType.SAML2,
            auth_endpoint="https://idp.s2.example/auth",
            audience=_AUDIENCE,
            encrypted_credential=IDP_CERT_PEM,
            attribute_mappings={"email": "email", "name": "name"},
        )
        service = SsoService(config_manager=_StaticConfigManager(config))
        result = _run_async(
            service.handle_callback("idp-saml-1", {"SAMLResponse": _build_saml_response()})
        )
        self.assertTrue(result.success)
        self.assertTrue(result.token.startswith("t-1:sso:"))
        self.assertEqual(result.user_id, "sso:user@s2.example")
        self.assertTrue(result.is_new_user)

    def test_handle_callback_replay_detected(self):
        config = IdpConfiguration(
            idp_id="idp-saml-1",
            tenant_id="t-1",
            protocol_type=SsoProtocolType.SAML2,
            auth_endpoint="https://idp.s2.example/auth",
            audience=_AUDIENCE,
            encrypted_credential=IDP_CERT_PEM,
            attribute_mappings={"email": "email", "name": "name"},
        )
        service = SsoService(config_manager=_StaticConfigManager(config))
        payload = {"SAMLResponse": _build_saml_response()}
        first = _run_async(service.handle_callback("idp-saml-1", payload))
        self.assertTrue(first.success)
        second = _run_async(service.handle_callback("idp-saml-1", payload))
        self.assertFalse(second.success)
        self.assertEqual(second.error_code, SSO_REPLAY_DETECTED)


if __name__ == "__main__":
    unittest.main()
