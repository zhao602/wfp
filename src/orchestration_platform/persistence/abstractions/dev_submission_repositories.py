# =============================================================================
# 用户开发提交仓储抽象（persistence/abstractions/dev_submission_repositories.py）
#
# 职责（agent_market_user_development T10.1 / CHG-19 / DC-03）：
#   定义 IDevSubmissionRepository（@runtime_checkable Protocol）；
#   Protocol 落新文件——既有 persistence/abstractions/market_repositories.py
#   逐字节零变更（INV-D-19）。
# 约束：Python 3.10 兼容；零第三方新增依赖。
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.market.dev_models import DevSubmission


@runtime_checkable
class IDevSubmissionRepository(Protocol):
    """用户开发提交仓储接口（DC-03）：本地文件 / PostgreSQL 双实现。"""

    async def save(self, submission: DevSubmission) -> None:
        """保存提交（同标识幂等覆盖；报告与历史留档仅追加，AC-DEV-16）。"""
        ...

    async def get(self, submission_id: str) -> Optional[DevSubmission]:
        """按标识读取提交；不存在返回 None。"""
        ...

    async def list_all(self) -> List[DevSubmission]:
        """列出全部提交。"""
        ...

    async def list_by_state(self, state: ContributionState) -> List[DevSubmission]:
        """按状态列出提交。"""
        ...

    async def exists_by_node_version(
        self, developer_id: str, node_name: str, version: str
    ) -> bool:
        """按开发者 + 节点名 + 版本判重（§5.4.2 重复提交口径）。"""
        ...
