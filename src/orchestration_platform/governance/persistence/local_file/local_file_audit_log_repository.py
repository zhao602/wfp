# =============================================================================
# 本地文件审计日志仓储（governance/persistence/local_file/local_file_audit_log_repository.py）
#
# 职责：以追加写入文件持久化审计日志。
# 设计意图（对应 spec 5.3、design 2.6、任务 T63.3）：
#   1. 按 tenant_id 分目录存储；
#   2. 文件以追加模式打开，不可修改/删除；
#   3. 用于无 PostgreSQL 环境与测试。
# =============================================================================

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.audit.hash_chain_verifier import (
    HashChainVerifier,
)
from orchestration_platform.governance.audit.models import AuditLogEntry

if TYPE_CHECKING:
    from orchestration_platform.governance.audit.tombstone.models import TombstoneBatchResult


class LocalFileAuditLogRepository:
    """审计日志仓储——本地文件实现。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        root = storage_root or get_settings().local_storage_root
        self._base_dir = Path(root) / "governance" / "audit_logs"
        self._verifier = HashChainVerifier()

    def _tenant_dir(self, tenant_id: str) -> Path:
        return self._base_dir / tenant_id

    def _log_file(self, tenant_id: str) -> Path:
        return self._tenant_dir(tenant_id) / "audit_log.jsonl"

    async def append(self, entry: AuditLogEntry) -> str:
        """追加审计日志（追加写入模式）。"""
        file_path = self._log_file(entry.tenant_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")
        return entry.entry_id

    async def query(
        self,
        tenant_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        operation: Optional[str] = None,
        actor_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[AuditLogEntry]:
        """按条件过滤查询审计日志。"""
        entries: List[AuditLogEntry] = []

        if tenant_id is not None:
            tenant_ids = [tenant_id]
        else:
            tenant_ids = self._list_tenant_ids()

        for tid in tenant_ids:
            file_path = self._log_file(tid)
            if not file_path.exists():
                continue
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = AuditLogEntry.model_validate_json(line)
                    if start_time and entry.occurred_at < start_time:
                        continue
                    if end_time and entry.occurred_at >= end_time:
                        continue
                    if operation and entry.operation.value != operation:
                        continue
                    if actor_id and entry.actor_id != actor_id:
                        continue
                    entries.append(entry)

        entries.sort(key=lambda e: e.occurred_at)
        return entries[:limit]

    async def verify_hash_chain(
        self,
        tenant_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """校验哈希链完整性。"""
        entries = await self.query(
            tenant_id=tenant_id,
            start_time=start_time,
            end_time=end_time,
            limit=1000000,
        )
        return self._verifier.verify_chain(entries)

    async def get_last_hash(
        self, tenant_id: Optional[str] = None
    ) -> str:
        """获取最后一条日志的哈希值。"""
        entries = await self.query(tenant_id=tenant_id, limit=1)
        if not entries:
            return ""
        return entries[-1].entry_hash

    def _list_tenant_ids(self) -> List[str]:
        """列出全部租户标识。"""
        if not self._base_dir.exists():
            return []
        return [d.name for d in self._base_dir.iterdir() if d.is_dir()]

    # ---- 偏差修正：墓碑扩展方法 ----

    async def mark_tombstone_batch(
        self,
        entry_ids: List[str],
        operator_id: str,
        deleted_at: datetime,
        forget_operation_id: Optional[str] = None,
    ) -> "TombstoneBatchResult":
        """批量标记墓碑。"""
        from orchestration_platform.governance.audit.tombstone.models import (
            TombstoneBatchResult,
        )

        success_count = 0
        failed_count = 0
        failed_entry_ids: List[str] = []

        # 按租户分组处理
        all_entries = await self.query(limit=1000000)
        entry_map = {e.entry_id: e for e in all_entries}

        for entry_id in entry_ids:
            if entry_id not in entry_map:
                failed_count += 1
                failed_entry_ids.append(entry_id)
                continue

            entry = entry_map[entry_id]
            if entry.tombstone:
                failed_count += 1
                failed_entry_ids.append(entry_id)
                continue

            # 更新墓碑字段
            updated = entry.model_copy(update={
                "tombstone": True,
                "deleted_at": deleted_at,
                "deleted_by": operator_id,
                "forget_operation_id": forget_operation_id,
            })

            # 重写文件（读取全部、替换、写回）
            try:
                self._rewrite_entry(updated)
                success_count += 1
            except Exception:
                failed_count += 1
                failed_entry_ids.append(entry_id)

        return TombstoneBatchResult(
            success_count=success_count,
            failed_count=failed_count,
            failed_entry_ids=failed_entry_ids,
        )

    async def query_by_user(
        self,
        user_id: str,
        tenant_id: str,
    ) -> List[AuditLogEntry]:
        """按用户标识查询审计日志。"""
        return await self.query(
            tenant_id=tenant_id,
            actor_id=user_id,
            limit=1000000,
        )

    def _rewrite_entry(self, updated_entry: AuditLogEntry) -> None:
        """重写单条审计日志（墓碑标记用）。"""
        file_path = self._log_file(updated_entry.tenant_id)
        if not file_path.exists():
            return

        lines: List[str] = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = AuditLogEntry.model_validate_json(line)
                if entry.entry_id == updated_entry.entry_id:
                    lines.append(updated_entry.model_dump_json())
                else:
                    lines.append(line)

        with open(file_path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
