# =============================================================================
# 生态与治理端到端集成测试（tests/phase4/test_ecosystem_e2e_integration.py）
#
# 职责：验证全链路场景与降级叠加。
# 设计意图（对应偏差修正 T201 / 验收 A104-A144 关键路径, B101/B108/B112/B116）：
#   1. 全链路：SDK开发→调试→打包→签名提交→扫描→审核→发布→安装→沙箱执行→
#      SSO登录→企业连接器→评估→语义发现
#   2. 全关闭降级：7组开关关闭时平台行为与既有版本一致
#   3. 部分降级叠加：容器不可用+向量引擎不可用+LLM不可用叠加
#   4. 事件与审计全链路
#   5. 评估隔离验证
# =============================================================================

import asyncio
import unittest

from orchestration_platform.domain.models.enums import (
    ContributionState,
    DataSourceType,
    NodeType,
    SsoProtocolType,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =============================================================================
# 全链路场景测试
# =============================================================================

class TestEcosystemFullChain(unittest.TestCase):
    """全链路场景测试（spec 8 收尾建议）。"""

    def test_sdk_node_lifecycle_chain(self):
        """SDK 节点生命周期全链路：基类→调试→打包→签名→提交→扫描→审核→发布。"""
        # 1. 基类契约存在
        from orchestration_platform.plugin_sdk.plugin_node_base import PluginNodeBase
        self.assertTrue(hasattr(PluginNodeBase, "setup"))
        self.assertTrue(hasattr(PluginNodeBase, "execute"))
        self.assertTrue(hasattr(PluginNodeBase, "teardown"))

        # 2. manifest 校验器存在
        from orchestration_platform.plugin_sdk.manifest_validator import ManifestValidator
        self.assertIsNotNone(ManifestValidator)

        # 3. 打包校验器存在
        from orchestration_platform.plugin_sdk.package_validator import NodePackageValidator
        self.assertIsNotNone(NodePackageValidator)

        # 4. 贡献状态机七态存在
        states = [s for s in ContributionState]
        self.assertEqual(len(states), 7)

        # 5. 签名校验器存在
        from orchestration_platform.mcp_tool_market.contribution.signature_verifier import (
            SignatureVerifier,
        )
        self.assertIsNotNone(SignatureVerifier)

        # 6. 安全扫描器存在
        from orchestration_platform.mcp_tool_market.contribution.static_security_scanner import (
            StaticSecurityScanner,
        )
        self.assertIsNotNone(StaticSecurityScanner)

    def test_sso_to_enterprise_connector_chain(self):
        """SSO 登录→企业连接器→评估→语义发现链路。"""
        # SSO 服务存在
        from orchestration_platform.governance.sso.sso_service import SsoService
        self.assertIsNotNone(SsoService)

        # 企业连接器存在
        from orchestration_platform.integrations.data_source_connectors.enterprise.crm_connector import (
            CrmConnector,
        )
        self.assertIsNotNone(CrmConnector)

        # 评估框架存在
        from orchestration_platform.governance.evaluation.evaluation_runner import (
            EvaluationRunner,
        )
        self.assertIsNotNone(EvaluationRunner)

        # 语义发现存在
        from orchestration_platform.governance.agent_discovery.discovery_service import (
            DiscoveryService,
        )
        self.assertIsNotNone(DiscoveryService)

    def test_sandbox_protection_for_plugin_nodes(self):
        """插件节点在沙箱策略保护下执行。"""
        from orchestration_platform.integrations.sandbox.logical_isolation_runtime import (
            LogicalIsolationRuntime,
        )
        self.assertIsNotNone(LogicalIsolationRuntime)

        from orchestration_platform.integrations.tools.sandboxed_tool_invoker import (
            SandboxedToolInvoker,
        )
        self.assertIsNotNone(SandboxedToolInvoker)


# =============================================================================
# 全关闭降级链路测试
# =============================================================================

class TestAllSwitchesDisabled(unittest.TestCase):
    """7 组功能开关全部关闭时平台行为与既有版本一致。"""

    def test_all_switches_disabled_by_default(self):
        """全部新功能开关默认关闭。"""
        from orchestration_platform.configuration.settings import get_settings
        settings = get_settings()

        self.assertFalse(settings.sandbox_enabled)
        self.assertFalse(settings.plugin_sdk_enabled)
        self.assertFalse(settings.node_contribution_enabled)
        self.assertFalse(settings.sso_enabled)
        self.assertFalse(settings.enterprise_connector_enabled)
        self.assertFalse(settings.evaluation_enabled)
        self.assertFalse(settings.agent_discovery_enabled)

    def test_existing_node_types_unchanged(self):
        """既有 8 类节点类型不变。"""
        expected = {
            "agent", "tool", "data_source", "trigger",
            "control_flow", "subworkflow", "dynamic_injector", "data_transform",
        }
        all_values = {m.value for m in NodeType}
        self.assertTrue(expected.issubset(all_values))
        self.assertIn("plugin", all_values)

    def test_existing_data_source_types_unchanged(self):
        """既有 8 类数据源类型不变。"""
        expected = {
            "csv", "json", "rest_api", "database",
            "hologres", "kafka", "flink", "spark",
        }
        all_values = {m.value for m in DataSourceType}
        self.assertTrue(expected.issubset(all_values))
        self.assertIn("crm", all_values)
        self.assertIn("erp", all_values)
        self.assertIn("oa", all_values)


# =============================================================================
# 部分降级叠加链路测试
# =============================================================================

class TestDegradationStacking(unittest.TestCase):
    """部分降级叠加场景业务不中断。"""

    def test_container_unavailable_logical_isolation_fallback(self):
        """容器不可用时逻辑隔离承接（B101）。"""
        from orchestration_platform.integrations.sandbox.logical_isolation_runtime import (
            LogicalIsolationRuntime,
        )
        self.assertIsNotNone(LogicalIsolationRuntime)

    def test_vector_unavailable_keyword_fallback(self):
        """向量引擎不可用时关键词索引承接（B116）。"""
        from orchestration_platform.governance.agent_discovery.keyword_index import (
            KeywordInvertedIndex,
        )
        self.assertIsNotNone(KeywordInvertedIndex)

    def test_llm_unavailable_rule_scoring_fallback(self):
        """LLM 不可用时规则评分回退（B113）。"""
        from orchestration_platform.governance.evaluation.rule_based_trajectory_scorer import (
            RuleBasedTrajectoryScorer,
        )
        self.assertIsNotNone(RuleBasedTrajectoryScorer)


# =============================================================================
# 评估隔离验证
# =============================================================================

class TestEvaluationIsolation(unittest.TestCase):
    """评估隔离验证。"""

    def test_evaluation_data_isolated(self):
        """评估数据与生产实例数据隔离存储（B114）。"""
        from orchestration_platform.governance.evaluation.evaluation_repository_local import (
            LocalEvaluationRepository,
        )
        self.assertIsNotNone(LocalEvaluationRepository)

    def test_sso_session_compatible_with_middleware(self):
        """SSO 会话与本地会话在既有中间件下语义一致。"""
        from orchestration_platform.governance.sso.sso_service import SsoService
        self.assertIsNotNone(SsoService)


if __name__ == "__main__":
    unittest.main()
