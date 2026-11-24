# TODO(tech-debt): B档候选孤儿（族群 B8），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 贡献服务抽象（mcp_tool_market/contribution/abstractions/contribution_service.py）
# =============================================================================

from typing import List, Protocol, runtime_checkable

from orchestration_platform.mcp_tool_market.contribution.models import (
    ContributionSubmitRequest,
    ContributionSubmitResult,
    ContributionTransitionRequest,
    ContributionTransitionResult,
    ScanReport,
)


@runtime_checkable
class IContributionService(Protocol):
    """贡献服务抽象。"""

    async def submit(self, request: ContributionSubmitRequest) -> ContributionSubmitResult:
        """提交贡献。"""
        ...

    async def transition(
        self,
        contribution_id: str,
        request: ContributionTransitionRequest,
    ) -> ContributionTransitionResult:
        """状态流转。"""
        ...

    async def rescan(self, contribution_id: str, operator_id: str) -> ScanReport:
        """重新扫描。"""
        ...

    def get_history(self, contribution_id: str) -> List:
        """获取流转历史。"""
        ...
