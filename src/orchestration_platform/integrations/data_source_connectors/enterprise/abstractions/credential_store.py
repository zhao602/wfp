# TODO(tech-debt): B档候选孤儿（族群 B14），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 凭证仓库抽象（enterprise/abstractions/credential_store.py）
# =============================================================================

from typing import List, Protocol, runtime_checkable

from orchestration_platform.integrations.data_source_connectors.enterprise.models import (
    ConnectorCredential,
    CredentialVersionInfo,
)


@runtime_checkable
class IEnterpriseCredentialStore(Protocol):
    """企业凭证仓库抽象。"""

    def put(self, connector_id: str, plaintext: str, created_by: str) -> ConnectorCredential:
        """写入新版本凭证。"""
        ...

    def get_active(self, connector_id: str) -> str:
        """获取当前版本凭证明文（仅内部使用）。"""
        ...

    def list_versions(self, connector_id: str) -> List[CredentialVersionInfo]:
        """列出凭证版本元数据（不含密文）。"""
        ...
