# =============================================================================
# 墓碑与被遗忘权测试（tests/governance/test_tombstone_gdpr.py）
#
# 职责：验证 GDPR Article 17 墓碑删除功能正确性。
# 设计意图（对应偏差修正 T105 / 验收 A86-A90, B89-B91）：
#   1. 单条墓碑标记
#   2. 被遗忘权批量标记
#   3. 墓碑元数据保留
#   4. 墓碑操作可审计
#   5. 哈希链连续性
#   6. 物理删除拒绝
#   7. 部分失败处理
# =============================================================================

import asyncio
import unittest
from datetime import datetime, timezone

from orchestration_platform.domain.models.enums import (
    AuditOperationResult,
    OperationType,
    ResourceType,
)
from orchestration_platform.governance.audit.models import AuditLogEntry
from orchestration_platform.governance.audit.tombstone.right_to_be_forgotten_service import (
    RightToBeForgottenService,
)
from orchestration_platform.governance.audit.tombstone.tombstone_service import (
    TombstoneService,
)
from orchestration_platform.governance.persistence.local_file.local_file_audit_log_repository import (
    LocalFileAuditLogRepository,
)


def _run_async(coro):
    """辅助：在同步测试中运行协程。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_entry(entry_id: str, tenant_id: str, actor_id: str) -> AuditLogEntry:
    """构造测试审计日志条目。"""
    return AuditLogEntry(
        entry_id=entry_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        resource_type=ResourceType.WORKFLOW_DEFINITION,
        resource_id="res_1",
        operation=OperationType.CREATE,
        operation_result=AuditOperationResult.SUCCESS,
        trace_id="trace_1",
        occurred_at=datetime.now(timezone.utc),
    )


class TestTombstoneService(unittest.TestCase):
    """墓碑标记服务测试（验收 A87, A88, B89）。"""

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self.repo = LocalFileAuditLogRepository(storage_root=self._tmpdir)
        self.service = TombstoneService(self.repo)

    def test_mark_tombstone_success(self) -> None:
        """单条墓碑标记成功。"""
        entry = _make_entry("e1", "t1", "u1")
        _run_async(self.repo.append(entry))

        result = _run_async(
            self.service.mark_tombstone("e1", "admin", "t1")
        )
        self.assertTrue(result.success)
        self.assertEqual(result.entry_id, "e1")

    def test_mark_tombstone_not_found(self) -> None:
        """标记不存在的条目返回错误。"""
        result = _run_async(
            self.service.mark_tombstone("non_existent", "admin", "t1")
        )
        self.assertFalse(result.success)

    def test_tombstone_metadata_preserved(self) -> None:
        """墓碑标记后元数据保留。"""
        entry = _make_entry("e1", "t1", "u1")
        _run_async(self.repo.append(entry))

        _run_async(self.service.mark_tombstone("e1", "admin", "t1"))

        # 查询验证墓碑标记
        entries = _run_async(self.repo.query(tenant_id="t1"))
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].tombstone)
        self.assertEqual(entries[0].deleted_by, "admin")
        self.assertIsNotNone(entries[0].deleted_at)


class TestRightToBeForgotten(unittest.TestCase):
    """被遗忘权服务测试（验收 A87, A89）。"""

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self.repo = LocalFileAuditLogRepository(storage_root=self._tmpdir)
        self.tombstone_svc = TombstoneService(self.repo)
        self.rtbf_svc = RightToBeForgottenService(self.tombstone_svc, self.repo)

    def test_forget_user_marks_all_entries(self) -> None:
        """被遗忘权标记用户全部审计日志。"""
        for i in range(5):
            entry = _make_entry(f"e{i}", "t1", "user_to_forget")
            _run_async(self.repo.append(entry))

        result = _run_async(
            self.rtbf_svc.forget_user("user_to_forget", "compliance_admin", "t1")
        )
        self.assertEqual(result.total_count, 5)
        self.assertEqual(result.success_count, 5)
        self.assertEqual(result.failed_count, 0)

    def test_forget_user_no_entries(self) -> None:
        """无审计日志时返回空结果。"""
        result = _run_async(
            self.rtbf_svc.forget_user("no_user", "admin", "t1")
        )
        self.assertEqual(result.total_count, 0)
        self.assertEqual(result.success_count, 0)

    def test_reject_physical_delete(self) -> None:
        """物理删除始终被拒绝。"""
        result = _run_async(
            self.rtbf_svc.reject_physical_delete("e1")
        )
        self.assertIn("禁止", result.reason)


if __name__ == "__main__":
    unittest.main()
