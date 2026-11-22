# =============================================================================
# 签名校验器（mcp_tool_market/contribution/signature_verifier.py）
#
# 职责：校验贡献包签名。
# 设计意图（对应偏差修正 T156 / design 4.4）：
#   1. 默认 HMAC-SHA256（hmac 标准库零依赖）
#   2. RSA-SHA256 可选模式（密码学库延迟导入）
#   3. 签名无效或缺失返回 SIGNATURE_INVALID
#   4. 验签前摘要比对防包替换攻击
# =============================================================================

import hashlib
import hmac
import logging

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.mcp_tool_market.contribution.models import (
    SignatureInfo,
)

_logger = logging.getLogger(__name__)


class SignatureVerifier:
    """签名校验器。

    默认 HMAC-SHA256（纯标准库）；RSA-SHA256 可选（延迟导入）。
    密钥经 node_contribution_signature_secret 环境变量注入。
    """

    def __init__(self, algorithm: str = None, secret: str = None) -> None:
        """构造签名校验器。

        参数：
            algorithm —— 签名算法（缺省从配置读取）
            secret    —— 签名密钥（缺省从配置读取）
        """
        settings = get_settings()
        self._algorithm = algorithm or settings.node_contribution_signature_algorithm
        self._secret = secret or settings.node_contribution_signature_secret

    def verify(self, package_digest: str, signature: SignatureInfo) -> bool:
        """校验签名。

        参数：
            package_digest —— 包内容 SHA-256 摘要
            signature      —— 签名信息

        返回：
            True 表示签名有效；False 表示无效
        """
        if not self._secret:
            _logger.warning("签名密钥未配置，拒绝校验")
            return False

        if self._algorithm == "hmac-sha256":
            return self._verify_hmac_sha256(package_digest, signature)
        elif self._algorithm == "rsa-sha256":
            return self._verify_rsa_sha256(package_digest, signature)
        else:
            _logger.error("不支持的签名算法: %s", self._algorithm)
            return False

    def sign(self, package_digest: str) -> str:
        """对包摘要生成签名（HMAC-SHA256 模式）。

        参数：
            package_digest —— 包内容 SHA-256 摘要

        返回：
            签名值（十六进制）
        """
        if not self._secret:
            raise ValueError("签名密钥未配置")

        return hmac.new(
            self._secret.encode("utf-8"),
            package_digest.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _verify_hmac_sha256(self, package_digest: str, signature: SignatureInfo) -> bool:
        """HMAC-SHA256 校验。"""
        expected = hmac.new(
            self._secret.encode("utf-8"),
            package_digest.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature.signature_value)

    def _verify_rsa_sha256(self, package_digest: str, signature: SignatureInfo) -> bool:
        """RSA-SHA256 校验（密码学库延迟导入）。"""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
            from cryptography.exceptions import InvalidSignature
        except ImportError:
            _logger.error("RSA 签名校验需要 cryptography 库，当前未安装")
            return False

        # RSA 验签需要公钥，此处简化为密钥作为公钥 PEM
        try:
            public_key = serialization.load_pem_public_key(
                self._secret.encode("utf-8")
            )
            public_key.verify(
                bytes.fromhex(signature.signature_value),
                package_digest.encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except (InvalidSignature, Exception) as exc:
            _logger.warning("RSA 签名校验失败: %s", exc)
            return False
