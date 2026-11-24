# TODO(tech-debt): B档候选孤儿（族群 B8），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 贡献仓储抽象（mcp_tool_market/contribution/abstractions/contribution_repository.py）
# =============================================================================

from typing import TYPE_CHECKING, List, Optional, Protocol, runtime_checkable

from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.mcp_tool_market.contribution.models import (
    NodeContribution,
)

if TYPE_CHECKING:
    from orchestration_platform.mcp_tool_market.contribution.models import (
        ScanReport,
        SignatureInfo,
    )
    from orchestration_platform.plugin_sdk.models import PluginManifest


@runtime_checkable
class IContributionRepository(Protocol):
    """贡献仓储抽象。"""

    def save(self, contribution: NodeContribution) -> None:
        """保存贡献（含状态、签名、扫描报告、流转历史）。"""
        ...

    def get(self, contribution_id: str) -> Optional[NodeContribution]:
        """按标识获取贡献。"""
        ...

    def list_by_state(self, state: ContributionState) -> List[NodeContribution]:
        """按状态列出贡献。"""
        ...

    def list_by_node(self, node_name: str) -> List[NodeContribution]:
        """按节点名称列出贡献。"""
        ...

    def exists_by_node_version(self, node_name: str, version: str) -> bool:
        """按名称+版本判重。"""
        ...


@runtime_checkable
class ISignatureVerifier(Protocol):
    """签名校验器抽象。"""

    def verify(self, package_digest: str, signature: "SignatureInfo") -> bool:
        """校验签名。"""
        ...


@runtime_checkable
class ISecurityScanner(Protocol):
    """安全扫描器抽象。"""

    def scan(self, package_path: str, manifest: "PluginManifest") -> "ScanReport":
        """执行静态安全扫描。"""
        ...
