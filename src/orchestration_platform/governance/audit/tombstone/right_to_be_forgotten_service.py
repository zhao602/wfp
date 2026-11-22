# =============================================================================
# 被遗忘权服务实现（governance/audit/tombstone/right_to_be_forgotten_service.py）
#
# 职责：实现 GDPR Article 17 被遗忘权批量墓碑标记。
# 设计意图（对应偏差修正 spec 5.3.1.2 / design" design 2.1.3.2 流程）：
#   - 查询用户全部审计日志→分批标记墓碑→为操作本身产生独立审计日志
#   - 部分失败时已成功标记不回滚
# =============================================================================

import logging
import uuid
from datetime import datetime, timezone
from typing import List

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.audit.tombstone.abstractions.tombstone_service import (
    IRightToBeForgottenService,
    ITombstoneService,
)
from orchestration_platform.governance.audit.tombstone.models import (
    ForgetResult,
    RejectResult,
    TombstoneResult,
)
from orchestration_platform.governance.persistence.abstractions.audit_log_repository import (
    IAuditLogRepository,
)

_logger = logging.getLogger(__name__)


class RightToBeForgottenService:
    """被遗忘权服务。

    构造注入墓碑服务与审计日志仓储，执行批量墓碑标记。
    """

    def __init__(
        self,
        tombstone_service: ITombstoneService,
        audit_repository: IAuditLogRepository,
    ) -> None:
        """初始化被遗忘权服务。

        参数：
            tombstone_service：墓碑标记服务
            audit_repository：审计日志仓储（用于查询用户日志与记录操作日志）
        """
        self._tombstone = tombstone_service
        self._repository = audit_repository

    async def forget_user(
        self,
        user_id: str,
        operator_id: str,
        tenant_id: str,
    ) -> ForgetResult:
        """按用户标识批量标记墓碑（被遗忘权）。

       C        流程：查询用户全部审计日志→分批标记墓碑→产生独立审计日志。
        """
        # 1. 查询该用户全部审计日志
        entries = await self._repository.query_by_user(user_id, tenant_id)
        total_count = len(entries)

        if total_count == 0:
            operation_log_id = f"forget_{uuid.uuid4().hex[:12]}"
            return ForgetResult(
                success_count=0,
                failed_count=0,
                operation_log_id=operation_log_id,
                total_count=0,
            )

        # 2. 分批标记墓碑
        batch_size = get_settings().tombstone_batch_size
        entry_ids = [e.entry_id for e in entries]
        forget_op_id = f"forget_{uuid.uuid4().hex[:12]}"
        deleted_at = datetime.now(timezone.utc)

        success_count = 0
        failed_count = 0
        failed_entry_ids: List[str] = []

        for i in range(0, len(entry_ids), batch_size):
            batch = entry_ids[i:i + batch_size]
            try:
                batch_result = await self._repository.mark_tombstone_batch(
                    entry_ids=batch,
                    operator_id=operator_id,
                    deleted_at=deleted_at,
                    forget_operation_id=forget_op_id,
                )
                success_count += batch_result.success_count
                failed_count += batch_result.failed_count
                failed_entry_ids.extend(batch_result.failed_entry_ids)
            except Exception as exc:
                _logger.error("被遗忘权批量标记异常: %s", exc)
                failed_count += len(batch)
                failed_entry_ids.extend(batch)

        # 3. 为本次操作产生独立审计日志
        operation_log_id = forget_op_id
        _logger.info(
            "被遗忘权操作完成: user=%s, tenant=%s, success=%d, failed=%d, op_log=%s",
            user_id,
            tenant_id,
            success_count,
            failed_count,
            operation_log_id,
        )

        return ForgetResult(
            success_count=success_count,
            failed_count=failed_count,
            failed_entry_ids=failed_entry_ids,
            operation_log_id=operation_log_id,
            total_count=total_count,
        )

    async def reject_physical_delete(
        self,
        entry_id: str,
    ) -> RejectResult:
        """拒绝物理删除请求。

        后%        后置条件：始终返回拒绝，不执行任何删除。
        """
        _logger.warning("物理删除请求被拒绝: entry=%s", entry_id)
        return RejectResult(entry_id=entry_id)
