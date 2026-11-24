# =============================================================================
# 审计日志仓储抽象接口（governance/persistence/abstractions/audit_log_repository.py）
#
# 职责：定义审计日志的仓储抽象接口。
# 设计意图（对应 spec 5.3、design 2.6、任务 T63.1）：
#   1. 追加写入（不可修改/删除）；
#   2. 按条件过滤查询；
#   3. 哈希链校验与获取前条哈希。
# =============================================================================

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, runtime_checkable

from orchestration_platform.governance.audit.models import AuditLogEntry

if TYPE_CHECKING:
    from orchestration_platform.governance.audit.tombstone.models import TombstoneBatchResult


@runtime_checkable
class IAuditLogRepository(Protocol):
    """审计日志仓储：追加写入与条件查询。"""

    async def append(self, entry: AuditLogEntry) -> str:
        """追加审计日志（不可变写入），返回 entry_id。

        同一 entry_id 重复追加时抛出错误。
        """
        ...

    async def query(
        self,
        tenant_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        operation: Optional[str] = None,
        actor_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[AuditLogEntry]:
        """按条件过滤查询审计日志。

        Args:
            tenant_id: 租户标识过滤。
            start_time: 起始时间过滤。
            end_time: 结束时间过滤。
            operation: 操作类型过滤。
            actor_id: 操作者过滤。
            limit: 返回条数上限。

        Returns:
            按时间升序排列的审计日志列表。
        """
        ...

    async def verify_hash_chain(
        self,
        tenant_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """校验哈希链完整性。

        Returns:
            校验结果：{verified_count, failed_count, failed_entry_ids, is_valid}。
        """
        ...

    async def get_last_hash(
        self, tenant_id: Optional[str] = None
    ) -> str:
        """获取最后一条日志的哈希值，用于哈希链计算。"""
        ...

    # ---- 偏差修正：墓碑扩展方法（既有方法签名不变）----

    async def mark_tombstone_batch(
        self,
        entry_ids: List[str],
        operator_id: str,
        deleted_at: datetime,
        forget_operation_id: Optional[str] = None,
    ) -> "TombstoneBatchResult":
        """批量标记墓碑。

        既有实现类未实现时抛出 NotImplementedError。
        """
        ...

    async def query_by_user(
        self,
        user_id: str,
        tenant_id: str,
    ) -> List[AuditLogEntry]:
        """按用户标识查询审计日志（被遗忘权前置查询）。"""
        ...
