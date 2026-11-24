# =============================================================================
# 墓碑标记服务实现（governance/audit/tombstone/tombstone_service.py）
#
# 职责：实现单条墓碑标记，脱敏原始内容并保持哈希链连续。
# 设计意图（对应偏差修正 spec 5.3.1 / design 2.1.3.2 流程）：
#   - 查询条目→脱敏原始内容→保留元数据→重算哈希→写入墓碑标记
#   - 哈希链断裂时中止标记并触发告警
# =============================================================================

import logging
import uuid
from datetime import datetime, timezone

from orchestration_platform.governance.audit.tombstone.models import TombstoneResult
from orchestration_platform.governance.persistence.abstractions.audit_log_repository import (
    IAuditLogRepository,
)

_logger = logging.getLogger(__name__)


class TombstoneService:
    """墓碑标记服务。

    构造注入审计日志仓储，执行单条墓碑标记。
    """

    def __init__(self, audit_repository: IAuditLogRepository) -> None:
        """初始化墓碑服务。

        参数：
            audit_repository：审计日志仓储
        """
        self._repository = audit_repository

    async def mark_tombstone(
        self,
        entry_id: str,
        operator_id: str,
        tenant_id: str,
    ) -> TombstoneResult:
        """单条墓碑标记。

        流程：查询条目→标记墓碑→保留元数据→哈希链连续。
        """
        deleted_at = datetime.now(timezone.utc)
        forget_op_id = f"forget_{uuid.uuid4().hex[:12]}"

        try:
            result = await self._repository.mark_tombstone_batch(
                entry_ids=[entry_id],
                operator_id=operator_id,
                deleted_at=deleted_at,
                forget_operation_id=forget_op_id,
            )
        except NotImplementedError:
            return TombstoneResult(
                success=False,
                entry_id=entry_id,
                error_code="NOT_IMPLEMENTED",
                error_message="仓储未实现墓碑标记",
            )

        if result.success_count > 0:
            _logger.info(
                "墓碑标记成功: entry=%s, operator=%s",
                entry_id,
                operator_id,
            )
            return TombstoneResult(success=True, entry_id=entry_id)

        error_code = "ENTRY_NOT_FOUND" if entry_id in result.failed_entry_ids else "ALREADY_TOMBSTONE"
        return TombstoneResult(
            success=False,
            entry_id=entry_id,
            error_code=error_code,
            error_message=f"墓碑标记失败: {error_code}",
        )
