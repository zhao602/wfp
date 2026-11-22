# =============================================================================
# 墓碑服务抽象接口（governance/audit/tombstone/abstractions/tombstone_service.py）
# =============================================================================

from typing import Protocol, runtime_checkable

from orchestration_platform.governance.audit.tombstone.models import (
    ForgetResult,
    RejectResult,
    TombstoneResult,
)


@runtime_checkable
class ITombstoneService(Protocol):
    """墓碑标记服务抽象接口。"""

    async def mark_tombstone(
        self,
        entry_id: str,
        operator_id: str,
        tenant_id: str,
    ) -> TombstoneResult:
        """单条墓碑标记。"""
        ...


@runtime_checkable
class IRightToBeForgottenService(Protocol):
    """被遗忘权服务抽象接口。"""

    async def forget_user(
        self,
        user_id: str,
        operator_id: str,
        tenant_id: str,
    ) -> ForgetResult:
        """按用户标识批量标记墓碑（被遗忘权）。"""
        ...

    async def reject_physical_delete(
        self,
        entry_id: str,
    ) -> RejectResult:
        """拒绝物理删除请求。"""
        ...
