# =============================================================================
# SAML 适配器（governance/sso/saml_adapter.py）
#
# 职责：SAML 2.0 协议适配。
# 设计意图（对应偏差修正 T166 / roadmap S2 验签落地）：
#   - xmlsec/lxml 延迟导入，导入失败标记 SAML 不可用
#   - 断言校验三件套：签名/受众/时效（xmlsec 真实验签，含 Reference 摘要校验）
#   - 拒绝无验签的断言；断言标识/用户标识一律服务端从已验签断言提取
#   - 包裹攻击加固：断言级签名 Reference URI 必须命中断言 ID
# =============================================================================

import base64
import logging
from datetime import datetime, timedelta, timezone

from orchestration_platform.domain.errors import SSO_ASSERTION_INVALID, DomainError
from orchestration_platform.governance.sso.models import (
    AssertionPrincipal,
    AuthRedirect,
    IdpConfiguration,
)

_logger = logging.getLogger(__name__)

# SAML 2.0 / XML-DSig 命名空间
_SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
_SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
_DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"

# IdP 成功状态码与时钟偏移容忍（秒）
_SAML_STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
_CLOCK_SKEW_SECONDS = 60


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

        校验链：base64→XML→状态码 Success→xmlsec 验签（响应级/断言级至少一处）
        →断言级 Reference URI 命中断言 ID（包裹加固）→受众/时效→NameID/属性提取。
        断言标识与用户标识一律服务端从已验签断言提取（防客户端伪造重放绕过）。
        """
        if not self.is_available():
            raise DomainError(
                SSO_ASSERTION_INVALID,
                "SAML 协议依赖未安装，无法校验断言",
            )

        saml_response = callback_payload.get("SAMLResponse", "")
        if not saml_response:
            raise DomainError(SSO_ASSERTION_INVALID, "SAMLResponse 缺失")

        import xmlsec
        from lxml import etree

        root = self._decode_and_parse(saml_response, etree)

        # IdP 状态码必须为 Success
        status_code = root.find(f"{{{_SAMLP_NS}}}Status/{{{_SAMLP_NS}}}StatusCode")
        if status_code is None or status_code.get("Value") != _SAML_STATUS_SUCCESS:
            raise DomainError(SSO_ASSERTION_INVALID, "IdP 状态码非 Success，拒绝")

        assertion = root.find(f"{{{_SAML_NS}}}Assertion")
        if assertion is None:
            raise DomainError(SSO_ASSERTION_INVALID, "SAML 断言缺失")

        assertion_id = assertion.get("ID", "")
        if not assertion_id:
            raise DomainError(SSO_ASSERTION_INVALID, "断言 ID 缺失")

        # 注册 ID 属性供签名 URI 引用解析（验签前置）
        xmlsec.tree.add_ids(root, ["ID"])

        # 验签凭据：IdP 验签证书 PEM（get_with_credential 解密后的明文）
        credential = (idp_config.encrypted_credential or "").strip()
        if not credential:
            raise DomainError(SSO_ASSERTION_INVALID, "IdP 验签证书缺失，拒绝")
        if not credential.startswith("-----BEGIN"):
            raise DomainError(SSO_ASSERTION_INVALID, "IdP 凭据非证书 PEM，无法验签")

        # 验签：响应级/断言级签名至少一处
        signatures = []
        response_signature = root.find(f"{{{_DSIG_NS}}}Signature")
        if response_signature is not None:
            signatures.append(response_signature)
        assertion_signature = assertion.find(f"{{{_DSIG_NS}}}Signature")
        if assertion_signature is not None:
            signatures.append(assertion_signature)
        if not signatures:
            raise DomainError(SSO_ASSERTION_INVALID, "断言未签名，拒绝")

        for signature in signatures:
            if signature is assertion_signature:
                # 包裹攻击加固：断言级签名必须覆盖断言本体
                reference = signature.find(
                    f"{{{_DSIG_NS}}}SignedInfo/{{{_DSIG_NS}}}Reference"
                )
                uri = reference.get("URI", "") if reference is not None else ""
                if uri != f"#{assertion_id}":
                    raise DomainError(
                        SSO_ASSERTION_INVALID,
                        "断言签名未覆盖断言本体（疑似包裹攻击），拒绝",
                    )
            self._verify_signature(signature, credential, xmlsec)

        # 受众校验
        audience_nodes = assertion.findall(
            f"{{{_SAML_NS}}}Conditions/"
            f"{{{_SAML_NS}}}AudienceRestriction/{{{_SAML_NS}}}Audience"
        )
        audience_values = [(node.text or "").strip() for node in audience_nodes]
        if idp_config.audience not in audience_values:
            raise DomainError(SSO_ASSERTION_INVALID, "断言受众不匹配，拒绝")

        # 时效校验：NotOnOrAfter 必填，NotBefore 可选（±时钟偏移）
        conditions = assertion.find(f"{{{_SAML_NS}}}Conditions")
        if conditions is None:
            raise DomainError(SSO_ASSERTION_INVALID, "断言缺少 Conditions，拒绝")
        not_on_or_after_raw = conditions.get("NotOnOrAfter", "")
        if not not_on_or_after_raw:
            raise DomainError(SSO_ASSERTION_INVALID, "断言缺少 NotOnOrAfter，拒绝")

        now = datetime.now(timezone.utc)
        skew = timedelta(seconds=_CLOCK_SKEW_SECONDS)
        not_on_or_after = self._parse_saml_time(not_on_or_after_raw)
        if now >= not_on_or_after + skew:
            raise DomainError(SSO_ASSERTION_INVALID, "断言已过期，拒绝")

        not_before = now
        not_before_raw = conditions.get("NotBefore", "")
        if not_before_raw:
            not_before = self._parse_saml_time(not_before_raw)
            if now < not_before - skew:
                raise DomainError(SSO_ASSERTION_INVALID, "断言尚未生效，拒绝")

        # 主体标识：NameID 必填（服务端提取）
        name_id = assertion.find(f"{{{_SAML_NS}}}Subject/{{{_SAML_NS}}}NameID")
        if name_id is None or not (name_id.text or "").strip():
            raise DomainError(SSO_ASSERTION_INVALID, "断言 NameID 缺失，拒绝")
        external_user_id = (name_id.text or "").strip()

        # 属性提取：AttributeStatement 可选，多值逗号拼接
        attributes = {}
        attribute_statement = assertion.find(f"{{{_SAML_NS}}}AttributeStatement")
        if attribute_statement is not None:
            for attribute in attribute_statement.findall(f"{{{_SAML_NS}}}Attribute"):
                attribute_name = attribute.get("Name", "")
                if not attribute_name:
                    continue
                values = [
                    (value.text or "").strip()
                    for value in attribute.findall(f"{{{_SAML_NS}}}AttributeValue")
                    if (value.text or "").strip()
                ]
                if values:
                    attributes[attribute_name] = ",".join(values)

        _logger.info("SAML 断言验签通过: assertion_id=%s", assertion_id)

        return AssertionPrincipal(
            external_user_id=external_user_id,
            attributes=attributes,
            assertion_id=assertion_id,
            not_before=not_before,
            not_on_or_after=not_on_or_after,
        )

    @staticmethod
    def _decode_and_parse(saml_response: str, etree):
        """base64 解码并解析为 XML 根元素（禁外部实体）。"""
        try:
            raw = base64.b64decode("".join(saml_response.split()), validate=False)
        except ValueError:
            raise DomainError(SSO_ASSERTION_INVALID, "SAMLResponse base64 解码失败")
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            return etree.fromstring(raw, parser=parser)
        except etree.XMLSyntaxError:
            raise DomainError(SSO_ASSERTION_INVALID, "SAMLResponse XML 解析失败")

    @staticmethod
    def _parse_saml_time(value: str) -> datetime:
        """解析 SAML UTC 时间戳（Z 后缀转 +00:00，Python 3.10 兼容）。"""
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _verify_signature(signature_node, credential_pem: str, xmlsec) -> None:
        """xmlsec 验证单处 XML-DSig 签名（含 Reference 摘要）。"""
        ctx = xmlsec.SignatureContext()
        try:
            ctx.key = xmlsec.Key.from_memory(credential_pem, xmlsec.KeyFormat.CERT_PEM, None)
        except Exception as exc:
            raise DomainError(SSO_ASSERTION_INVALID, f"IdP 验签证书加载失败: {exc}")
        try:
            ctx.verify(signature_node)
        except Exception as exc:
            _logger.warning("SAML 签名校验失败: %s", exc)
            raise DomainError(SSO_ASSERTION_INVALID, "SAML 签名校验失败，拒绝")
