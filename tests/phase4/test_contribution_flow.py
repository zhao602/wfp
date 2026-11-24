# =============================================================================
# 贡献市场测试（tests/phase4/test_contribution_flow.py）
#
# 职责：验证贡献状态机、签名校验、安全扫描与发布全链路。
# 设计意图（对应偏差修正 T161 / 验收 A116-A120, B106-B107）：
#   1. 状态机测试：正常全链路流转 + 非法流转拒绝 + 流转历史可追溯
#   2. 签名测试：有效签名通过 + 篡改签名拒绝 + 缺失签名驳回
#   3. 扫描测试：未声明网络外联→不通过 + 扫描器失败可重试
#   4. 兼容测试：node_contribution_enabled=False 时行为一致
#   5. 事件一致性：发布失败状态以本地为准可重试
# =============================================================================

import asyncio
import hashlib
import hmac
import os
import tempfile
import unittest

from orchestration_platform.domain.errors import (
    CONTRIBUTION_INVALID_TRANSITION,
    DomainError,
)
from orchestration_platform.domain.models.enums import (
    ContributionState,
    ContributorType,
)
from orchestration_platform.mcp_tool_market.contribution.models import (
    NodeContribution,
    SignatureInfo,
    StateTransitionRecord,
)
from orchestration_platform.mcp_tool_market.contribution.state_machine import (
    ContributionStateMachine,
)
from orchestration_platform.mcp_tool_market.contribution.signature_verifier import (
    SignatureVerifier,
)
from orchestration_platform.mcp_tool_market.contribution.static_security_scanner import (
    StaticSecurityScanner,
)
from orchestration_platform.plugin_sdk.models import PluginManifest


def _run_async(coro):
    """辅助：在同步测试中运行协程。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_contribution(
    state: ContributionState = ContributionState.DRAFT,
) -> NodeContribution:
    """构造测试贡献。"""
    return NodeContribution(
        contribution_id="test-contrib-1",
        node_name="test-node",
        version="1.0.0",
        contributor_id="dev-1",
        state=state,
    )


def _make_manifest() -> PluginManifest:
    """构造测试 manifest。"""
    return PluginManifest(
        node_name="test-node",
        version="1.0.0",
        declared_file_paths=["./data/"],
        declared_network_targets=[],
    )


# =============================================================================
# 状态机测试（A116, B106）
# =============================================================================

class TestContributionStateMachine(unittest.TestCase):
    """贡献状态机测试。"""

    def test_normal_full_chain_transition(self) -> None:
        """正常全链路流转 DRAFT→SUBMITTED→SECURITY_SCAN→REVIEWING→APPROVED→PUBLISHED（A116）。"""
        contribution = _make_contribution(ContributionState.DRAFT)

        # DRAFT → SUBMITTED
        contribution = ContributionStateMachine.transition(
            contribution, ContributionState.SUBMITTED, "dev-1", "提交"
        )
        self.assertEqual(contribution.state, ContributionState.SUBMITTED)

        # SUBMITTED → SECURITY_SCAN
        contribution = ContributionStateMachine.transition(
            contribution, ContributionState.SECURITY_SCAN, "system", "自动扫描"
        )
        self.assertEqual(contribution.state, ContributionState.SECURITY_SCAN)

        # SECURITY_SCAN → REVIEWING
        contribution = ContributionStateMachine.transition(
            contribution, ContributionState.REVIEWING, "reviewer-1", "审核"
        )
        self.assertEqual(contribution.state, ContributionState.REVIEWING)

        # REVIEWING → APPROVED
        contribution = ContributionStateMachine.transition(
            contribution, ContributionState.APPROVED, "reviewer-1", "批准"
        )
        self.assertEqual(contribution.state, ContributionState.APPROVED)

        # APPROVED → PUBLISHED
        contribution = ContributionStateMachine.transition(
            contribution, ContributionState.PUBLISHED, "publisher-1", "发布"
        )
        self.assertEqual(contribution.state, ContributionState.PUBLISHED)

    def test_invalid_transition_rejected(self) -> None:
        """非法流转被拒绝（PUBLISHED→DRAFT 被拒，A116）。"""
        contribution = _make_contribution(ContributionState.PUBLISHED)
        with self.assertRaises(DomainError) as ctx:
            ContributionStateMachine.transition(
                contribution, ContributionState.DRAFT, "dev-1", "回退"
            )
        self.assertEqual(ctx.exception.code, CONTRIBUTION_INVALID_TRANSITION)

    def test_rejected_is_terminal(self) -> None:
        """REJECTED 为终态，不可流转（A116）。"""
        contribution = _make_contribution(ContributionState.REJECTED)
        with self.assertRaises(DomainError) as ctx:
            ContributionStateMachine.transition(
                contribution, ContributionState.DRAFT, "dev-1", "回退"
            )
        self.assertEqual(ctx.exception.code, CONTRIBUTION_INVALID_TRANSITION)

    def test_published_is_terminal(self) -> None:
        """PUBLISHED 为终态，不可流转（A116）。"""
        contribution = _make_contribution(ContributionState.PUBLISHED)
        with self.assertRaises(DomainError):
            ContributionStateMachine.transition(
                contribution, ContributionState.REVIEWING, "dev-1", "回退"
            )

    def test_security_scan_self_loop_retry(self) -> None:
        """SECURITY_SCAN 自环重试允许（spec 5.3.3.1）。"""
        contribution = _make_contribution(ContributionState.SECURITY_SCAN)
        contribution = ContributionStateMachine.transition(
            contribution, ContributionState.SECURITY_SCAN, "system", "重新扫描"
        )
        self.assertEqual(contribution.state, ContributionState.SECURITY_SCAN)

    def test_state_history_traceable(self) -> None:
        """流转历史完整可追溯（B106）。"""
        contribution = _make_contribution(ContributionState.DRAFT)
        contribution = ContributionStateMachine.transition(
            contribution, ContributionState.SUBMITTED, "dev-1", "提交"
        )
        contribution = ContributionStateMachine.transition(
            contribution, ContributionState.SECURITY_SCAN, "system", "扫描"
        )

        self.assertEqual(len(contribution.state_history), 2)
        self.assertEqual(contribution.state_history[0].from_state, ContributionState.DRAFT)
        self.assertEqual(contribution.state_history[0].to_state, ContributionState.SUBMITTED)
        self.assertEqual(contribution.state_history[0].operator_id, "dev-1")
        self.assertEqual(contribution.state_history[1].from_state, ContributionState.SUBMITTED)
        self.assertEqual(contribution.state_history[1].to_state, ContributionState.SECURITY_SCAN)

    def test_can_transition_check(self) -> None:
        """can_transition 正确判断合法迁移。"""
        self.assertTrue(ContributionStateMachine.can_transition(
            ContributionState.DRAFT, ContributionState.SUBMITTED
        ))
        self.assertFalse(ContributionStateMachine.can_transition(
            ContributionState.DRAFT, ContributionState.PUBLISHED
        ))
        self.assertFalse(ContributionStateMachine.can_transition(
            ContributionState.PUBLISHED, ContributionState.DRAFT
        ))

    def test_is_terminal_check(self) -> None:
        """is_terminal 正确判断终态。"""
        self.assertTrue(ContributionStateMachine.is_terminal(ContributionState.PUBLISHED))
        self.assertTrue(ContributionStateMachine.is_terminal(ContributionState.REJECTED))
        self.assertFalse(ContributionStateMachine.is_terminal(ContributionState.DRAFT))
        self.assertFalse(ContributionStateMachine.is_terminal(ContributionState.REVIEWING))


# =============================================================================
# 签名校验测试（A117, A120）
# =============================================================================

class TestSignatureVerifier(unittest.TestCase):
    """签名校验器测试。"""

    def test_valid_hmac_signature_passes(self) -> None:
        """有效 HMAC-SHA256 签名通过（A117）。"""
        secret = "test-secret-key"
        verifier = SignatureVerifier(algorithm="hmac-sha256", secret=secret)

        package_digest = hashlib.sha256(b"test content").hexdigest()
        signature_value = hmac.new(
            secret.encode("utf-8"),
            package_digest.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        signature = SignatureInfo(
            algorithm="hmac-sha256",
            signature_value=signature_value,
            signed_by="dev-1",
        )

        self.assertTrue(verifier.verify(package_digest, signature))

    def test_tampered_signature_rejected(self) -> None:
        """篡改签名的节点包拒绝（A117）。"""
        verifier = SignatureVerifier(algorithm="hmac-sha256", secret="test-secret-key")

        package_digest = hashlib.sha256(b"test content").hexdigest()
        signature = SignatureInfo(
            algorithm="hmac-sha256",
            signature_value="tampered-signature-value",
            signed_by="dev-1",
        )

        self.assertFalse(verifier.verify(package_digest, signature))

    def test_missing_secret_rejects(self) -> None:
        """密钥未配置时拒绝校验（A120）。"""
        verifier = SignatureVerifier(algorithm="hmac-sha256", secret="")

        package_digest = hashlib.sha256(b"test content").hexdigest()
        signature = SignatureInfo(
            algorithm="hmac-sha256",
            signature_value="some-signature",
            signed_by="dev-1",
        )

        self.assertFalse(verifier.verify(package_digest, signature))

    def test_sign_and_verify_roundtrip(self) -> None:
        """签名与验签往返一致。"""
        verifier = SignatureVerifier(algorithm="hmac-sha256", secret="test-secret")
        package_digest = hashlib.sha256(b"content").hexdigest()
        signature_value = verifier.sign(package_digest)

        signature = SignatureInfo(
            algorithm="hmac-sha256",
            signature_value=signature_value,
            signed_by="dev-1",
        )
        self.assertTrue(verifier.verify(package_digest, signature))


# =============================================================================
# 静态安全扫描测试（A118, A120）
# =============================================================================

class TestStaticSecurityScanner(unittest.TestCase):
    """静态安全扫描器测试。"""

    def setUp(self) -> None:
        self.scanner = StaticSecurityScanner()
        self._tmpdir = tempfile.mkdtemp()

    def test_clean_code_passes(self) -> None:
        """无危险调用的代码扫描通过（A118）。"""
        pkg_dir = os.path.join(self._tmpdir, "clean_pkg")
        os.makedirs(pkg_dir, exist_ok=True)
        with open(os.path.join(pkg_dir, "node.py"), "w", encoding="utf-8") as f:
            f.write("def execute(inputs):\n    return {'result': inputs.get('query', '')}\n")

        manifest = _make_manifest()
        report = self.scanner.scan(pkg_dir, manifest)
        self.assertTrue(report.passed)

    def test_dangerous_eval_detected(self) -> None:
        """eval() 调用被检测（A118）。"""
        pkg_dir = os.path.join(self._tmpdir, "eval_pkg")
        os.makedirs(pkg_dir, exist_ok=True)
        with open(os.path.join(pkg_dir, "node.py"), "w", encoding="utf-8") as f:
            f.write("def execute(inputs):\n    return eval(inputs.get('code', ''))\n")

        manifest = _make_manifest()
        report = self.scanner.scan(pkg_dir, manifest)
        self.assertFalse(report.passed)
        self.assertTrue(any(f.rule_id == "dangerous_call" for f in report.findings))

    def test_undeclared_network_detected(self) -> None:
        """未声明的网络外联被检测（A118, spec 5.3.1.3）。"""
        pkg_dir = os.path.join(self._tmpdir, "network_pkg")
        os.makedirs(pkg_dir, exist_ok=True)
        with open(os.path.join(pkg_dir, "node.py"), "w", encoding="utf-8") as f:
            f.write(
                "import socket\n"
                "def execute(inputs):\n"
                "    s = socket.socket()\n"
                "    return {}\n"
            )

        manifest = _make_manifest()  # declared_network_targets 为空
        report = self.scanner.scan(pkg_dir, manifest)
        self.assertFalse(report.passed)
        self.assertTrue(any(f.rule_id == "undeclared_network" for f in report.findings))

    def test_declared_network_allowed(self) -> None:
        """已声明的网络外联不报错。"""
        pkg_dir = os.path.join(self._tmpdir, "declared_network_pkg")
        os.makedirs(pkg_dir, exist_ok=True)
        with open(os.path.join(pkg_dir, "node.py"), "w", encoding="utf-8") as f:
            f.write(
                "import socket\n"
                "def execute(inputs):\n"
                "    s = socket.socket()\n"
                "    return {}\n"
            )

        manifest = PluginManifest(
            node_name="test-node",
            version="1.0.0",
            declared_file_paths=["./data/"],
            declared_network_targets=["tcp://example.com:443"],
        )
        report = self.scanner.scan(pkg_dir, manifest)
        # socket.socket 仍在 _NETWORK_CALLS 中，但 declared_network_targets 非空
        # 所以不会触发 undeclared_network 规则
        undeclared_findings = [f for f in report.findings if f.rule_id == "undeclared_network"]
        self.assertEqual(len(undeclared_findings), 0)

    def test_package_not_found(self) -> None:
        """包路径不存在返回失败报告。"""
        manifest = _make_manifest()
        report = self.scanner.scan("/nonexistent/path", manifest)
        self.assertFalse(report.passed)
        self.assertIsNotNone(report.error)


# =============================================================================
# 贡献模型测试
# =============================================================================

class TestContributionModels(unittest.TestCase):
    """贡献领域模型测试。"""

    def test_node_contribution_defaults(self) -> None:
        """NodeContribution 默认值正确。"""
        contribution = NodeContribution(
            contribution_id="c-1",
            node_name="node-1",
            version="1.0.0",
            contributor_id="dev-1",
        )
        self.assertEqual(contribution.state, ContributionState.DRAFT)
        self.assertEqual(contribution.contributor_type, ContributorType.THIRD_PARTY)
        self.assertEqual(contribution.scan_reports, [])
        self.assertEqual(contribution.state_history, [])

    def test_signature_info_creation(self) -> None:
        """SignatureInfo 可正确创建。"""
        sig = SignatureInfo(
            algorithm="hmac-sha256",
            signature_value="abc123",
            signed_by="dev-1",
        )
        self.assertEqual(sig.algorithm, "hmac-sha256")
        self.assertEqual(sig.signature_value, "abc123")

    def test_state_transition_record_creation(self) -> None:
        """StateTransitionRecord 可正确创建。"""
        record = StateTransitionRecord(
            from_state=ContributionState.DRAFT,
            to_state=ContributionState.SUBMITTED,
            operator_id="dev-1",
            reason="提交",
        )
        self.assertEqual(record.from_state, ContributionState.DRAFT)
        self.assertEqual(record.to_state, ContributionState.SUBMITTED)


# =============================================================================
# 兼容性测试（A120, B105）
# =============================================================================

class TestCompatibilityRegression(unittest.TestCase):
    """兼容性回归测试。"""

    def test_contribution_disabled_by_default(self) -> None:
        """node_contribution_enabled 默认关闭（A120）。"""
        from orchestration_platform.configuration.settings import get_settings
        settings = get_settings()
        self.assertFalse(settings.node_contribution_enabled)

    def test_plugin_sdk_disabled_by_default(self) -> None:
        """plugin_sdk_enabled 默认关闭（A120）。"""
        from orchestration_platform.configuration.settings import get_settings
        settings = get_settings()
        self.assertFalse(settings.plugin_sdk_enabled)

    def test_sandbox_disabled_by_default(self) -> None:
        """sandbox_enabled 默认关闭（A120）。"""
        from orchestration_platform.configuration.settings import get_settings
        settings = get_settings()
        self.assertFalse(settings.sandbox_enabled)

    def test_existing_node_types_unchanged(self) -> None:
        """既有 8 类节点类型不变（B105）。"""
        from orchestration_platform.domain.models.enums import NodeType
        expected = {
            "agent", "tool", "data_source", "trigger",
            "control_flow", "subworkflow", "dynamic_injector", "data_transform",
        }
        all_values = {m.value for m in NodeType}
        self.assertTrue(expected.issubset(all_values))
        self.assertIn("plugin", all_values)


if __name__ == "__main__":
    unittest.main()
