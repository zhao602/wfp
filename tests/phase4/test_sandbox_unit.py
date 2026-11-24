# =============================================================================
# 沙箱单元测试（tests/phase4/test_sandbox_unit.py）
#
# 职责：验证沙箱三守卫（路径/网络/资源）与违规出口断言。
# 设计意图（对应偏差修正 T143 / 验收 A104-A109, B101-B103）：
#   1. 路径穿越拦截（../ / 符号链接 / 绝对路径逃逸）
#   2. 网络白名单放行与拒绝
#   3. 资源三维超限终止
#   4. 超时终止后宿主可用
#   5. 违规出口统一断言
#
# 根因组 F（fix_baseline_test_failures）：test_measure_usage 断言未区分
# 降级模式——Windows 无 resource 模块时计量降级（核心特征 memory_peak_mb == 0.0，
# cpu_seconds 为 monotonic 耗时差值不受降级影响），改为按 is_degraded 分支断言
# （FR-08 / INV-16，design.md §1.3 实测校准；resource_meter.py 零改动）。
# =============================================================================

import asyncio
import os
import tempfile
import unittest

from orchestration_platform.domain.models.enums import (
    IsolationMode,
    NetworkPolicyMode,
    ViolationType,
)
from orchestration_platform.integrations.sandbox.models import (
    SandboxPolicy,
)
from orchestration_platform.integrations.sandbox.path_guard import PathGuard
from orchestration_platform.integrations.sandbox.network_filter import NetworkFilter
from orchestration_platform.integrations.sandbox.resource_meter import ResourceMeter


def _run_async(coro):
    """辅助：在同步测试中运行协程。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_policy(workdir: str = None, **kwargs) -> SandboxPolicy:
    """构造测试沙箱策略。"""
    defaults = dict(
        policy_id="test_policy_1",
        tenant_id="test_tenant",
        workdir=workdir or tempfile.mkdtemp(),
        isolation_mode=IsolationMode.LOGICAL,
        network_policy=NetworkPolicyMode.DEFAULT_DENY,
        cpu_seconds_limit=10.0,
        memory_mb_limit=512,
        output_bytes_limit=1048576,
        execution_timeout_seconds=60,
    )
    defaults.update(kwargs)
    return SandboxPolicy(**defaults)


# =============================================================================
# 路径守卫测试
# =============================================================================

class TestPathGuard(unittest.TestCase):
    """路径守卫测试（验收 A106, B103）。"""

    def setUp(self) -> None:
        self.guard = PathGuard()
        self._tmpdir = tempfile.mkdtemp()
        self.policy = _make_policy(workdir=self._tmpdir)

    def test_path_within_workdir_passes(self) -> None:
        """工作目录内路径通过。"""
        safe_path = os.path.join(self._tmpdir, "subdir", "file.txt")
        violations = self.guard.check({"file_path": safe_path}, self.policy)
        self.assertEqual(len(violations), 0)

    def test_absolute_path_escape_detected(self) -> None:
        """绝对路径逃逸被检测。"""
        violations = self.guard.check({"file_path": "/etc/passwd"}, self.policy)
        self.assertGreater(len(violations), 0)
        self.assertEqual(violations[0].violation_type, ViolationType.FILE_ESCAPE)

    def test_dotdot_traversal_detected(self) -> None:
        """.. 目录穿越被检测。"""
        traversal_path = os.path.join(self._tmpdir, "..", "..", "etc", "passwd")
        violations = self.guard.check({"file_path": traversal_path}, self.policy)
        self.assertGreater(len(violations), 0)
        self.assertEqual(violations[0].violation_type, ViolationType.FILE_ESCAPE)

    def test_non_path_string_passes(self) -> None:
        """非路径字符串通过。"""
        violations = self.guard.check({"message": "hello world"}, self.policy)
        self.assertEqual(len(violations), 0)

    def test_allowed_prefixes(self) -> None:
        """额外允许前缀内路径通过。"""
        extra_dir = tempfile.mkdtemp()
        policy = _make_policy(
            workdir=self._tmpdir,
            allowed_path_prefixes=[extra_dir],
        )
        safe_path = os.path.join(extra_dir, "file.txt")
        violations = self.guard.check({"file_path": safe_path}, policy)
        self.assertEqual(len(violations), 0)


# =============================================================================
# 网络过滤器测试
# =============================================================================

class TestNetworkFilter(unittest.TestCase):
    """网络过滤器测试（验收 A107, B103）。"""

    def setUp(self) -> None:
        self.filter = NetworkFilter()

    def test_default_deny_blocks_all(self) -> None:
        """默认拒绝模式阻止所有网络访问。"""
        policy = _make_policy(network_policy=NetworkPolicyMode.DEFAULT_DENY)
        violation = self.filter.check("https://example.com", policy)
        self.assertIsNotNone(violation)
        self.assertEqual(violation.violation_type, ViolationType.NETWORK_DENIED)

    def test_whitelist_allows_matching(self) -> None:
        """白名单模式放行匹配目标。"""
        policy = _make_policy(
            network_policy=NetworkPolicyMode.WHITELIST,
            network_whitelist=["api.example.com"],
        )
        violation = self.filter.check("api.example.com", policy)
        self.assertIsNone(violation)

    def test_whitelist_blocks_non_matching(self) -> None:
        """白名单模式拒绝不匹配目标。"""
        policy = _make_policy(
            network_policy=NetworkPolicyMode.WHITELIST,
            network_whitelist=["api.example.com"],
        )
        violation = self.filter.check("evil.com", policy)
        self.assertIsNotNone(violation)
        self.assertEqual(violation.violation_type, ViolationType.NETWORK_DENIED)

    def test_ip_cidr_matching(self) -> None:
        """IP CIDR 网段匹配。"""
        policy = _make_policy(
            network_policy=NetworkPolicyMode.WHITELIST,
            network_whitelist=["192.168.1.0/24"],
        )
        violation = self.filter.check("192.168.1.100:8080", policy)
        self.assertIsNone(violation)

    def test_empty_whitelist_blocks(self) -> None:
        """空白名单阻止所有。"""
        policy = _make_policy(
            network_policy=NetworkPolicyMode.WHITELIST,
            network_whitelist=[],
        )
        violation = self.filter.check("any.host.com", policy)
        self.assertIsNotNone(violation)


# =============================================================================
# 资源计量器测试
# =============================================================================

class TestResourceMeter(unittest.TestCase):
    """资源计量器测试（验收 A104, A105, B103）。"""

    def setUp(self) -> None:
        self.meter = ResourceMeter()
        self.policy = _make_policy(
            cpu_seconds_limit=1.0,
            memory_mb_limit=512,
            output_bytes_limit=100,
        )

    def test_output_within_limit(self) -> None:
        """输出未超限。"""
        violation = self.meter.check_output_size(50, self.policy)
        self.assertIsNone(violation)

    def test_output_exceeds_limit(self) -> None:
        """输出超限。"""
        violation = self.meter.check_output_size(200, self.policy)
        self.assertIsNotNone(violation)
        self.assertEqual(violation.violation_type, ViolationType.RESOURCE_EXCEEDED)

    def test_cpu_within_limit(self) -> None:
        """CPU 时间未超限。"""
        violation = self.meter.check_cpu_time(0.5, self.policy)
        self.assertIsNone(violation)

    def test_cpu_exceeds_limit(self) -> None:
        """CPU 时间超限。"""
        violation = self.meter.check_cpu_time(2.0, self.policy)
        self.assertIsNotNone(violation)
        self.assertEqual(violation.violation_type, ViolationType.RESOURCE_EXCEEDED)

    def test_degraded_mode(self) -> None:
        """降级模式不计量内存。"""
        if not self.meter.is_degraded:
            self.skipTest("resource 模块可用，非降级模式")
        violation = self.meter.check_memory(99999, self.policy)
        self.assertIsNone(violation)

    def test_measure_usage(self) -> None:
        """测量资源用量（按 is_degraded 分支断言，两种环境均确定性通过）。"""
        import time
        start = time.monotonic()
        time.sleep(0.01)
        usage = self.meter.measure_usage(start, 50)
        self.assertEqual(usage.output_bytes, 50)
        if self.meter.is_degraded:
            # 降级分支（如 Windows 无 resource 模块）：核心特征为 memory_peak_mb == 0.0，
            # cpu_seconds 为 monotonic 耗时差值不受降级影响（可计量字段可获取）
            self.assertEqual(usage.memory_peak_mb, 0.0)
            self.assertGreaterEqual(usage.cpu_seconds, 0)
        else:
            # 全量分支（有 resource 模块的环境，如 CI Linux）：CPU 时间可真实计量
            self.assertGreater(usage.cpu_seconds, 0)


# =============================================================================
# 违规出口统一断言测试
# =============================================================================

class TestViolationExitAssertion(unittest.TestCase):
    """违规出口统一断言测试（验收 A109）。"""

    def test_violation_result_success_false(self) -> None:
        """违规非空时 success=False。"""
        from orchestration_platform.integrations.sandbox.models import (
            SandboxExecutionResult,
            SandboxViolationRecord,
        )

        # 构造含违规的结果
        result = SandboxExecutionResult(
            success=False,
            violations=[SandboxViolationRecord(
                violation_type=ViolationType.FILE_ESCAPE,
                detail="测试违规",
            )],
        )
        self.assertFalse(result.success)
        self.assertGreater(len(result.violations), 0)

    def test_timeout_result_success_false(self) -> None:
        """超时时 success=False。"""
        from orchestration_platform.integrations.sandbox.models import (
            SandboxExecutionResult,
        )

        result = SandboxExecutionResult(
            success=False,
            timed_out=True,
        )
        self.assertFalse(result.success)
        self.assertTrue(result.timed_out)


# =============================================================================
# 策略管理器测试
# =============================================================================

class TestSandboxPolicyManager(unittest.TestCase):
    """沙箱策略管理器测试（验收 A104）。"""

    def setUp(self) -> None:
        from orchestration_platform.integrations.sandbox.policy_repository_local import (
            LocalSandboxPolicyRepository,
        )
        from orchestration_platform.integrations.sandbox.policy_manager import (
            SandboxPolicyManager,
        )
        self._tmpdir = tempfile.mkdtemp()
        self.repo = LocalSandboxPolicyRepository(storage_root=self._tmpdir)
        self.manager = SandboxPolicyManager(self.repo)

    def test_save_and_load_policy(self) -> None:
        """保存并加载策略。"""
        policy = _make_policy()
        _run_async(self.manager.save_policy(policy))

        loaded, error = _run_async(
            self.manager.load_policy(policy.policy_id, policy.tenant_id)
        )
        self.assertIsNone(error)
        self.assertEqual(loaded.policy_id, policy.policy_id)

    def test_nonexistent_policy_returns_default(self) -> None:
        """不存在的策略回退默认安全策略。"""
        loaded, error = _run_async(
            self.manager.load_policy("nonexistent", "test_tenant")
        )
        self.assertIsNotNone(error)
        self.assertIsNotNone(loaded)
        self.assertIn("默认安全策略", loaded.name)

    def test_invalid_policy_rejected(self) -> None:
        """非法策略被拒绝。"""
        from orchestration_platform.integrations.sandbox.models import SandboxPolicy

        policy = SandboxPolicy(
            policy_id="",
            tenant_id="test",
            workdir=tempfile.mkdtemp(),
        )
        with self.assertRaises(ValueError):
            _run_async(self.manager.save_policy(policy))


# =============================================================================
# 降级状态测试
# =============================================================================

class TestSandboxDegradedState(unittest.TestCase):
    """沙箱降级状态测试（验收 B101）。"""

    def test_degraded_summary(self) -> None:
        """降级状态汇总。"""
        from orchestration_platform.governance.degraded_mode import (
            SandboxDegradedState,
        )
        summary = SandboxDegradedState.get_degraded_summary()
        self.assertIn("sandbox_degraded", summary)
        self.assertIn("degraded_components", summary)

    def test_resource_meter_degraded_detection(self) -> None:
        """资源计量器降级检测。"""
        from orchestration_platform.governance.degraded_mode import (
            SandboxDegradedState,
        )
        # 仅验证方法可调用
        result = SandboxDegradedState.is_resource_meter_degraded()
        self.assertIsInstance(result, bool)


if __name__ == "__main__":
    unittest.main()
