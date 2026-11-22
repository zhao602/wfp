# =============================================================================
# 凭据加密器（governance/sso/credential_cipher.py）
#
# 职责：AES-GCM 加密/解密 IdP 凭据与企业连接器凭证。
# 设计意图（对应偏差修正 T163 / design 决策D2）：
#   - cryptography 库延迟导入
#   - 密钥未配置或加密库缺失时拒绝持久化凭据
#   - 加密输出含版本标识便于密钥轮换
# =============================================================================

import logging

from orchestration_platform.configuration.settings import get_settings

_logger = logging.getLogger(__name__)


class CredentialCipher:
    """凭据加密器。

    AES-GCM 加密（cryptography 库延迟导入）。
    密钥经 credential_encryption_key 环境变量注入。
    密钥未配置或加密库缺失时拒绝持久化凭据（仅内存态运行并告警）。
    """

    def __init__(self, encryption_key: str = None) -> None:
        """构造加密器。

        参数：
            encryption_key —— 加密密钥（缺省从配置读取）
        """
        settings = get_settings()
        self._key = encryption_key if encryption_key is not None else settings.credential_encryption_key

    def is_available(self) -> bool:
        """检查加密功能是否可用。"""
        if not self._key:
            return False
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
            return True
        except ImportError:
            return False

    def encrypt(self, plaintext: str) -> str:
        """加密明文。

        返回：
            加密后的密文（含版本标识前缀）

        异常：
            ValueError —— 密钥未配置或加密库缺失
        """
        if not self._key:
            _logger.warning("凭据加密密钥未配置，拒绝持久化凭据")
            raise ValueError("凭据加密密钥未配置")

        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            import os
        except ImportError:
            _logger.error("cryptography 库未安装，凭据加密不可用")
            raise ValueError("cryptography 库未安装")

        # 从密钥派生 32 字节 AES 密钥
        import hashlib
        key_bytes = hashlib.sha256(self._key.encode("utf-8")).digest()
        nonce = os.urandom(12)
        aesgcm = AESGCM(key_bytes)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        # 版本前缀 v1 + nonce + ciphertext（十六进制）
        return "v1:" + nonce.hex() + ":" + ciphertext.hex()

    def decrypt(self, ciphertext: str) -> str:
        """解密密文。

        返回：
            解密后的明文

        异常：
            ValueError —— 密钥未配置、加密库缺失或解密失败
        """
        if not self._key:
            raise ValueError("凭据加密密钥未配置")

        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            raise ValueError("cryptography 库未安装")

        # 解析版本前缀
        if not ciphertext.startswith("v1:"):
            raise ValueError("不支持的密文版本")

        parts = ciphertext.split(":", 2)
        if len(parts) != 3:
            raise ValueError("密文格式非法")

        import hashlib
        key_bytes = hashlib.sha256(self._key.encode("utf-8")).digest()
        nonce = bytes.fromhex(parts[1])
        ct_bytes = bytes.fromhex(parts[2])
        aesgcm = AESGCM(key_bytes)
        plaintext = aesgcm.decrypt(nonce, ct_bytes, None)
        return plaintext.decode("utf-8")
