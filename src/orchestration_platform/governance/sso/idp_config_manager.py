# =============================================================================
# IdP 配置仓储与管理器（governance/sso/idp_config_manager.py）
#
# 职责：IdP 配置 CRUD 与凭据加密管理。
# 设计意图（对应偏差修正 T164）：
#   - 凭据写入时经 CredentialCipher 加密
#   - 查询接口不返回凭据明文（响应恒为掩码与版本号）
# =============================================================================

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import SsoProtocolType
from orchestration_platform.governance.sso.credential_cipher import CredentialCipher
from orchestration_platform.governance.sso.models import IdpConfiguration

_logger = logging.getLogger(__name__)


class IdpConfigManager:
    """IdP 配置管理器。

    本地文件存储（./.data/sso/idp_configs/）。
    凭据字段密文落盘，查询接口不返回明文。
    """

    def __init__(
        self,
        storage_root: Optional[str] = None,
        cipher: Optional[CredentialCipher] = None,
    ) -> None:
        """构造配置管理器。"""
        settings = get_settings()
        self._root = Path(storage_root or "./.data/sso/idp_configs")
        self._root.mkdir(parents=True, exist_ok=True)
        self._cipher = cipher or CredentialCipher()
        self._cache: Dict[str, IdpConfiguration] = {}

    def save(self, config: IdpConfiguration, credential_plaintext: str = "") -> IdpConfiguration:
        """保存 IdP 配置。

        参数：
            config               —— IdP 配置（不含凭据明文）
            credential_plaintext —— 凭据明文（非空时加密存储）

        返回：
            保存后的配置（凭据字段为掩码）
        """
        if credential_plaintext:
            if not self._cipher.is_available():
                _logger.warning("加密不可用，凭据拒绝持久化（仅内存态）")
            else:
                config.encrypted_credential = self._cipher.encrypt(credential_plaintext)
                config.credential_version += 1

        config_path = self._root / f"{config.idp_id}.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(mode="json"), f, ensure_ascii=False, indent=2)

        self._cache[config.idp_id] = config
        return self._mask_credential(config)

    def get(self, idp_id: str) -> Optional[IdpConfiguration]:
        """获取 IdP 配置（凭据字段为掩码）。"""
        if idp_id in self._cache:
            return self._mask_credential(self._cache[idp_id])

        config_path = self._root / f"{idp_id}.json"
        if not config_path.exists():
            return None

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            config = IdpConfiguration(**data)
            self._cache[idp_id] = config
            return self._mask_credential(config)
        except Exception as exc:
            _logger.error("加载 IdP 配置失败: %s", exc)
            return None

    def get_with_credential(self, idp_id: str) -> Optional[IdpConfiguration]:
        """获取 IdP 配置（含解密后的凭据明文，仅内部使用）。"""
        config = self._cache.get(idp_id)
        if config is None:
            config_path = self._root / f"{idp_id}.json"
            if not config_path.exists():
                return None
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                config = IdpConfiguration(**data)
                self._cache[idp_id] = config
            except Exception:
                return None

        if config.encrypted_credential and self._cipher.is_available():
            try:
                config.encrypted_credential = self._cipher.decrypt(config.encrypted_credential)
            except Exception as exc:
                _logger.error("凭据解密失败: %s", exc)
        return config

    def list_by_tenant(self, tenant_id: str) -> List[IdpConfiguration]:
        """按租户列出 IdP 配置（凭据字段为掩码）。"""
        results: List[IdpConfiguration] = []
        for config_file in self._root.glob("*.json"):
            config = self.get(config_file.stem)
            if config is not None and config.tenant_id == tenant_id:
                results.append(config)
        return results

    def delete(self, idp_id: str) -> bool:
        """删除 IdP 配置。"""
        config_path = self._root / f"{idp_id}.json"
        if config_path.exists():
            config_path.unlink()
        self._cache.pop(idp_id, None)
        return True

    @staticmethod
    def _mask_credential(config: IdpConfiguration) -> IdpConfiguration:
        """掩码凭据字段（查询接口不返回明文）。"""
        if config.encrypted_credential:
            config.encrypted_credential = "********"
        return config
