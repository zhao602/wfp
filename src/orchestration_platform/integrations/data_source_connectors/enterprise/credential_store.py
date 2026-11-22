# =============================================================================
# 企业凭证仓库（enterprise/credential_store.py）
# =============================================================================

import logging
from typing import Dict, List, Optional

from orchestration_platform.governance.sso.credential_cipher import CredentialCipher
from orchestration_platform.integrations.data_source_connectors.enterprise.models import (
    ConnectorCredential,
    CredentialVersionInfo,
)

_logger = logging.getLogger(__name__)


class EnterpriseCredentialStore:
    """企业凭证仓库。

    AES-GCM 加密落盘（复用 CredentialCipher）。
    版本化追加，明文不可回读查询。
    凭证热生效：写入新版本→内存缓存失效→后续调用自动加载新凭证。
    """

    def __init__(self, cipher: Optional[CredentialCipher] = None) -> None:
        self._cipher = cipher or CredentialCipher()
        # connector_id -> [ConnectorCredential]
        self._credentials: Dict[str, List[ConnectorCredential]] = {}
        # connector_id -> 当前版本明文缓存
        self._active_plaintext: Dict[str, str] = {}

    def put(self, connector_id: str, plaintext: str, created_by: str) -> ConnectorCredential:
        """写入新版本凭证。"""
        if not self._cipher.is_available():
            _logger.warning("加密不可用，凭证拒绝持久化（仅内存态）")
            encrypted = ""
        else:
            encrypted = self._cipher.encrypt(plaintext)

        versions = self._credentials.setdefault(connector_id, [])
        new_version = (versions[-1].version + 1) if versions else 1

        credential = ConnectorCredential(
            encrypted_secret=encrypted,
            version=new_version,
            created_by=created_by,
        )
        versions.append(credential)

        # 热生效：更新内存缓存
        self._active_plaintext[connector_id] = plaintext

        _logger.info("凭证更新: connector_id=%s version=%d", connector_id, new_version)
        return credential

    def get_active(self, connector_id: str) -> str:
        """获取当前版本凭证明文（仅内部使用）。"""
        # 优先从缓存读取（热生效）
        if connector_id in self._active_plaintext:
            return self._active_plaintext[connector_id]

        versions = self._credentials.get(connector_id, [])
        if not versions:
            return ""

        latest = versions[-1]
        if not latest.encrypted_secret or not self._cipher.is_available():
            return ""

        try:
            plaintext = self._cipher.decrypt(latest.encrypted_secret)
            self._active_plaintext[connector_id] = plaintext
            return plaintext
        except Exception as exc:
            _logger.error("凭证解密失败: %s", exc)
            return ""

    def list_versions(self, connector_id: str) -> List[CredentialVersionInfo]:
        """列出凭证版本元数据（不含密文）。"""
        versions = self._credentials.get(connector_id, [])
        return [
            CredentialVersionInfo(
                version=v.version,
                created_by=v.created_by,
                created_at=v.created_at,
            )
            for v in versions
        ]
