# =============================================================================
# 墓碑查询服务（governance/audit/tombstone/tombstone_query_service.py）
#
# 职责：查询墓碑记录，仅返回元数据（不含原始内容）。
# =============================================================================

from typing import List

from orchestration_platform.governance.audit.tombstone.models import (
    TombstoneMetadataView,
)
from orchestration_platform.governance.persistence.abstractions.audit_log_repository import (
    IAuditLogRepository,
)


class TombstoneQueryService:
    """墓碑查询服务。"""

    def __init__(self, audit_repository: IAuditLogRepository) -> None:
        """初始化墓碑查询服务。"""
        self._repository = audit_repository

    async def query_tombstones(
        self,
        tenant_id: str,
        limit: int = 1000,
    ) -> List[TombstoneMetadataView]:
        """查询墓碑记录，仅返回元数据。

        后置条件：返回 TombstoneMetadataView 列表，不含原始内容。
        """
        entries = await self._repository.query(
            tenant_id=tenant_id,
            limit=limit,
        )
        tombstone_entries = [e for e in entries if e.tombstone]
        return [
            TombstoneMetadataView(
                entry_id=e.entry_id,
                tombstone=e.tombstone,
                deleted_at=e.deleted_at,
                deleted_by=e.deleted_by,
                forget_operation_id=e.forget_operation_id,
                tenant_id=e.tenant_id,
                occurred_at=e.occurred_at,
            )
            for e in tombstone_entries
        ]
