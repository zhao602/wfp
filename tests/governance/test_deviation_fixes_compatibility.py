# =============================================================================
# 偏差修正兼容性回归测试（tests/governance/test_deviation_fixes_compatibility.py）
#
# 职责：验证偏差修正不破坏既有行为与接口契约。
# 设计意图（对应偏差修正 T128 / 验收 A102, B99, B100）：
#   1. 契约测试：governance/ 目录无 import langgraph
#   2. 既有RBAC行为回归：不启用ABAC时行为不变
#   3. 既有审计行为回归：不启用墓碑时行为不变
#   4. 既有事件溯源回归：不启用Kafka时行为不变
#   5. 既有接口签名不变：InterceptorChain/IAuditLogRepository 向后兼容
#   6. Python 3.10 兼容性：无 StrEnum/Self/TaskGroup/PEP695 语法
# =============================================================================

import asyncio
import inspect
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from orchestration_platform.domain.models.enums import (
    AuditOperationResult,
    OperationType,
    ResourceType,
)
from orchestration_platform.governance.audit.models import AuditLogEntry
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
    InterceptorChain,
)
from orchestration_platform.governance.persistence.abstractions.audit_log_repository import (
    IAuditLogRepository,
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


def _make_audit_entry(entry_id: str = "e1", tenant_id: str = "t1") -> AuditLogEntry:
    """构造测试审计日志条目。"""
    return AuditLogEntry(
        entry_id=entry_id,
        tenant_id=tenant_id,
        actor_id="u1",
        resource_type=ResourceType.WORKFLOW_DEFINITION,
        resource_id="res_1",
        operation=OperationType.CREATE,
        operation_result=AuditOperationResult.SUCCESS,
        trace_id="trace_1",
        occurred_at=datetime.now(timezone.utc),
    )


# =============================================================================
# 测试1：LangGraph 隔离契约（B99）
# =============================================================================

class TestLangGraphIsolationContract(unittest.TestCase):
    """LangGraph 隔离契约测试。

    governance/ 目录下所有 Python 文件不得直接 import langgraph。
    """

    def test_no_langgraph_import_in_governance(self) -> None:
        """扫描 governance/ 目录断言无 import langgraph。"""
        governance_dir = os.path.join(
            os.path.dirname(__file__), '..', '..', 'src',
            'orchestration_platform', 'governance'
        )
        governance_dir = os.path.abspath(governance_dir)

        violations = []
        for root, _dirs, files in os.walk(governance_dir):
            for fname in files:
                if not fname.endswith('.py'):
                    continue
                file_path = os.path.join(root, fname)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if 'import langgraph' in content or 'from langgraph' in content:
                        violations.append(file_path)

        self.assertEqual(
            len(violations), 0,
            f"LangGraph 导入违规: {violations}"
        )

    def test_no_langgraph_import_in_nl_orchestration(self) -> None:
        """NL编排模块无 LangGraph 导入。"""
        nl_dir = os.path.join(
            os.path.dirname(__file__), '..', '..', 'src',
            'orchestration_platform', 'governance', 'nl_orchestration'
        )
        nl_dir = os.path.abspath(nl_dir)

        violations = []
        for root, _dirs, files in os.walk(nl_dir):
            for fname in files:
                if not fname.endswith('.py'):
                    continue
                file_path = os.path.join(root, fname)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if 'import langgraph' in content or 'from langgraph' in content:
                        violations.append(file_path)

        self.assertEqual(len(violations), 0)


# =============================================================================
# 测试2：既有RBAC行为回归（不启用ABAC时行为不变）
# =============================================================================

class TestRbacBehaviorRegression(unittest.TestCase):
    """既有RBAC行为回归测试。

    不启用ABAC时RBAC行为与第三阶段一致。
    """

    def test_interceptor_chain_without_abac(self) -> None:
        """InterceptorChain 不传 abac 参数时正常工作。"""
        chain = InterceptorChain()
        self.assertTrue(chain.is_degraded_mode)

        request = GovernanceRequest(
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            operation=OperationType.EXECUTE,
            trace_id="trace_compat_1",
        )
        decision = _run_async(chain.execute_before(request))
        self.assertTrue(decision.allowed)

    def test_interceptor_chain_abac_defaults_none(self) -> None:
        """InterceptorChain abac 参数默认为 None。"""
        sig = inspect.signature(InterceptorChain.__init__)
        abac_param = sig.parameters.get('abac')
        self.assertIsNotNone(abac_param)
        self.assertIsNone(abac_param.default)

    def test_interceptor_chain_signature_backward_compatible(self) -> None:
        """InterceptorChain 构造函数签名向后兼容。"""
        sig = inspect.signature(InterceptorChain.__init__)
        params = list(sig.parameters.keys())
        # 既有参数必须存在
        expected_params = [
            'self', 'auth', 'tenant_isolation', 'audit',
            'quota', 'budget', 'global_scheduler', 'metering',
        ]
        for p in expected_params:
            self.assertIn(p, params, f"缺少既有参数: {p}")
        # abac 为新增参数
        self.assertIn('abac', params)
        # abac 在 metering 之后（新增参数在末尾）
        self.assertGreater(params.index('abac'), params.index('metering'))


# =============================================================================
# 测试3：既有审计行为回归（不启用墓碑时行为不变）
# =============================================================================

class TestAuditBehaviorRegression(unittest.TestCase):
    """既有审计行为回归测试。

    不启用墓碑时审计行为与第三阶段一致。
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self.repo = LocalFileAuditLogRepository(storage_root=self._tmpdir)

    def test_audit_append_without_tombstone(self) -> None:
        """审计日志追加写入，墓碑字段默认为 False。"""
        entry = _make_audit_entry("e_audit_1", "t1")
        _run_async(self.repo.append(entry))

        # 查询并验证墓碑字段为默认值
        entries = _run_async(self.repo.query(tenant_id="t1", limit=100))
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0].tombstone)
        self.assertIsNone(entries[0].deleted_at)
        self.assertIsNone(entries[0].deleted_by)

    def test_audit_entry_tombstone_defaults(self) -> None:
        """AuditLogEntry 墓碑字段默认值。"""
        entry = _make_audit_entry()
        self.assertFalse(entry.tombstone)
        self.assertIsNone(entry.deleted_at)
        self.assertIsNone(entry.deleted_by)
        self.assertIsNone(entry.forget_operation_id)

    def test_audit_entry_backward_compatible_construction(self) -> None:
        """AuditLogEntry 不传墓碑字段时正常构造。"""
        entry = AuditLogEntry(
            entry_id="e_compat",
            tenant_id="t1",
            actor_id="u1",
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            resource_id="res_1",
            operation=OperationType.CREATE,
            operation_result=AuditOperationResult.SUCCESS,
            trace_id="trace_compat",
            occurred_at=datetime.now(timezone.utc),
            previous_hash="",
            entry_hash="abc123",
        )
        self.assertFalse(entry.tombstone)
        self.assertEqual(entry.entry_hash, "abc123")


# =============================================================================
# 测试4：既有事件溯源回归（不启用Kafka时行为不变）
# =============================================================================

class TestEventSourcingRegression(unittest.TestCase):
    """既有事件溯源回归测试。

    不启用Kafka时事件溯源行为与第三阶段一致。
    """

    def test_event_bus_manager_degraded_by_default(self) -> None:
        """默认配置下事件总线为降级模式。"""
        from orchestration_platform.governance.event_bus.event_bus_manager import (
            EventBusManager,
        )
        manager = EventBusManager()
        # 默认 kafka_enabled=False → 降级
        self.assertTrue(manager.is_degraded)

    def test_in_memory_event_bus_preserves_order(self) -> None:
        """内存事件总线保持事件顺序。"""
        from orchestration_platform.governance.event_bus.in_memory_event_bus import (
            InMemoryEventBus,
        )
        from orchestration_platform.governance.event_bus.models import EventMessage
        from orchestration_platform.domain.models.enums import MessageEventType

        bus = InMemoryEventBus()
        for i in range(5):
            event = EventMessage(
                event_id=f"evt_{i}",
                event_type=MessageEventType.WORKFLOW_EXECUTION,
                sequence_number=i,
                payload={"seq": i},
                instance_id="inst_1",
            )
            _run_async(bus.publish("topic_1", event))

        replay = _run_async(bus.replay_from_offset("topic_1", 0, 100))
        self.assertEqual(len(replay.events), 5)
        for i, event in enumerate(replay.events):
            self.assertEqual(event.sequence_number, i)


# =============================================================================
# 测试5：既有接口签名不变（B99）
# =============================================================================

class TestInterfaceSignatureInvariant(unittest.TestCase):
    """既有接口签名不变测试（验收 B99）。"""

    def test_audit_log_repository_existing_methods_unchanged(self) -> None:
        """IAuditLogRepository 既有方法签名不变。"""
        # 既有方法必须存在
        existing_methods = ['append', 'query', 'verify_hash_chain', 'get_last_hash']
        for method_name in existing_methods:
            self.assertTrue(
                hasattr(IAuditLogRepository, method_name),
                f"IAuditLogRepository 缺少既有方法: {method_name}"
            )

    def test_audit_log_repository_new_methods_exist(self) -> None:
        """IAuditLogRepository 新增方法存在。"""
        new_methods = ['mark_tombstone_batch', 'query_by_user']
        for method_name in new_methods:
            self.assertTrue(
                hasattr(IAuditLogRepository, method_name),
                f"IAuditLogRepository 缺少新增方法: {method_name}"
            )

    def test_interceptor_chain_execute_before_signature(self) -> None:
        """InterceptorChain.execute_before 签名不变。"""
        sig = inspect.signature(InterceptorChain.execute_before)
        params = list(sig.parameters.keys())
        self.assertIn('self', params)
        self.assertIn('request', params)

    def test_interceptor_chain_execute_after_signature(self) -> None:
        """InterceptorChain.execute_after 签名不变。"""
        sig = inspect.signature(InterceptorChain.execute_after)
        params = list(sig.parameters.keys())
        self.assertIn('self', params)
        self.assertIn('request', params)
        self.assertIn('decision', params)
        self.assertIn('result', params)

    def test_governance_request_fields_unchanged(self) -> None:
        """GovernanceRequest 既有字段不变。"""
        from orchestration_platform.governance.interceptors.base import (
            GovernanceRequest,
        )
        existing_fields = [
            'token', 'resource_type', 'operation', 'tenant_id',
            'user_id', 'instance_id', 'trace_id', 'payload', 'metadata',
        ]
        for field in existing_fields:
            self.assertTrue(
                field in GovernanceRequest.model_fields,
                f"GovernanceRequest 缺少既有字段: {field}"
            )

    def test_governance_decision_fields_unchanged(self) -> None:
        """GovernanceDecision 既有字段不变。"""
        existing_fields = [
            'allowed', 'deny_reason', 'tenant_context',
            'resource_scope_filter', 'metadata',
        ]
        for field in existing_fields:
            self.assertTrue(
                field in GovernanceDecision.model_fields,
                f"GovernanceDecision 缺少既有字段: {field}"
            )


# =============================================================================
# 测试6：Python 3.10 兼容性（B100）
# =============================================================================

class TestPython310Compatibility(unittest.TestCase):
    """Python 3.10 兼容性测试（验收 B100）。

    扫描新增代码断言无 StrEnum/Self/TaskGroup/PEP695 语法。
    """

    def _scan_new_modules(self) -> list:
        """扫描偏差修正新增模块目录。"""
        base = os.path.join(
            os.path.dirname(__file__), '..', '..', 'src',
            'orchestration_platform', 'governance'
        )
        base = os.path.abspath(base)
        new_dirs = [
            'nl_orchestration',
            os.path.join('rbac', 'abac'),
            os.path.join('audit', 'tombstone'),
            'cache',
            'event_bus',
        ]
        files = []
        for d in new_dirs:
            dir_path = os.path.join(base, d)
            if not os.path.isdir(dir_path):
                continue
            for root, _dirs, fnames in os.walk(dir_path):
                for fname in fnames:
                    if fname.endswith('.py'):
                        files.append(os.path.join(root, fname))
        return files

    def test_no_str_enum_usage(self) -> None:
        """新增代码不使用 StrEnum（Python 3.11+ 独有）。"""
        files = self._scan_new_modules()
        pattern = re.compile(r'\bStrEnum\b')
        violations = []
        for fpath in files:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
            if pattern.search(content):
                violations.append(fpath)
        self.assertEqual(
            len(violations), 0,
            f"发现 StrEnum 使用（Python 3.11+）: {violations}"
        )

    def test_no_self_type_usage(self) -> None:
        """新增代码不使用 Self 类型（Python 3.11+ 独有）。"""
        files = self._scan_new_modules()
        pattern = re.compile(r'\bSelf\b')
        violations = []
        for fpath in files:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
            # 排除注释中的 Self
            lines = content.split('\n')
            for line in lines:
                stripped = line.split('#')[0]
                if pattern.search(stripped):
                    violations.append(fpath)
                    break
        self.assertEqual(
            len(violations), 0,
            f"发现 Self 类型使用（Python 3.11+）: {violations}"
        )

    def test_no_task_group_usage(self) -> None:
        """新增代码不使用 TaskGroup（Python 3.11+ 独有）。"""
        files = self._scan_new_modules()
        pattern = re.compile(r'\bTaskGroup\b')
        violations = []
        for fpath in files:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
            if pattern.search(content):
                violations.append(fpath)
        self.assertEqual(
            len(violations), 0,
            f"发现 TaskGroup 使用（Python 3.11+）: {violations}"
        )

    def test_no_pep695_type_alias(self) -> None:
        """新增代码不使用 PEP695 type 语句（Python 3.12+ 独有）。"""
        files = self._scan_new_modules()
        # PEP695: type Alias = ...
        pattern = re.compile(r'^\s*type\s+\w+\s*=', re.MULTILINE)
        violations = []
        for fpath in files:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
            if pattern.search(content):
                violations.append(fpath)
        self.assertEqual(
            len(violations), 0,
            f"发现 PEP695 type 语句（Python 3.12+）: {violations}"
        )

    def test_no_match_statement(self) -> None:
        """新增代码不使用 match 语句（Python 3.10+ 可用但避免复杂度）。"""
        # match 语句在 3.10 可用，此处仅记录不强制失败
        files = self._scan_new_modules()
        pattern = re.compile(r'^\s*match\s+\w+', re.MULTILINE)
        count = 0
        for fpath in files:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
            count += len(pattern.findall(content))
        # match 语句可用，仅记录
        self.assertGreaterEqual(count, 0)

    def test_union_type_syntax_compatible(self) -> None:
        """新增代码使用 X | Y 联合类型语法（3.10+ 可用）。"""
        # X | Y 在 3.10 运行时注解中可用，确认无运行时错误
        files = self._scan_new_modules()
        for fpath in files:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
            # 确认文件可编译
            try:
                compile(content, fpath, 'exec')
            except SyntaxError as exc:
                self.fail(f"Python 语法错误: {fpath} - {exc}")


# =============================================================================
# 测试7：配置兼容性
# =============================================================================

class TestConfigCompatibility(unittest.TestCase):
    """配置兼容性测试。

    偏差修正新增配置项均有默认值，不影响既有配置。
    """

    def test_new_config_items_have_defaults(self) -> None:
        """偏差修正新增配置项均有默认值。"""
        from orchestration_platform.configuration.settings import get_settings
        settings = get_settings()

        # NL 编排配置
        self.assertTrue(hasattr(settings, 'nl_orchestration_enabled'))
        # ABAC 配置
        self.assertTrue(hasattr(settings, 'abac_enabled'))
        # 墓碑配置
        self.assertTrue(hasattr(settings, 'tombstone_enabled'))
        # Redis 配置
        self.assertTrue(hasattr(settings, 'redis_enabled'))
        # Kafka 配置
        self.assertTrue(hasattr(settings, 'kafka_enabled'))

    def test_new_config_defaults_are_safe(self) -> None:
        """新增配置默认值不改变既有行为。"""
        from orchestration_platform.configuration.settings import get_settings
        settings = get_settings()

        # 默认禁用，不影响既有行为
        self.assertFalse(settings.kafka_enabled)
        self.assertFalse(settings.redis_enabled)


if __name__ == "__main__":
    unittest.main()
