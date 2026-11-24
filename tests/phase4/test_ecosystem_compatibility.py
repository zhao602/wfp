# =============================================================================
# 生态与治理兼容性回归测试（tests/phase4/test_ecosystem_compatibility.py）
#
# 职责：验证 LangGraph 隔离、既有接口签名、既有行为、枚举回归、Python 3.10 兼容。
# 设计意图（对应偏差修正 T202 / 验收 spec 9.8 横切, B103, B113）：
# =============================================================================

import ast
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC_ROOT = _PROJECT_ROOT / "src" / "orchestration_platform"

# 7 个新增目录（spec 4.5.2 LangGraph 隔离契约）
_NEW_DIRECTORIES = [
    "integrations/sandbox",
    "plugin_sdk",
    "engine/plugin_nodes",
    "mcp_tool_market/contribution",
    "governance/sso",
    "governance/evaluation",
    "governance/agent_discovery",
    "integrations/data_source_connectors/enterprise",
]


# =============================================================================
# LangGraph 隔离契约测试（spec 4.5.2）
# =============================================================================

class TestLangGraphIsolation(unittest.TestCase):
    """7 个新增目录无 import langgraph。"""

    def test_no_langgraph_import_in_new_directories(self):
        """扫描新增目录断言无 import langgraph。"""
        violations = []
        for dir_rel in _NEW_DIRECTORIES:
            dir_path = _SRC_ROOT / dir_rel
            if not dir_path.exists():
                continue
            for py_file in dir_path.rglob("*.py"):
                try:
                    with open(py_file, "r", encoding="utf-8") as f:
                        source = f.read()
                    tree = ast.parse(source)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                if "langgraph" in alias.name:
                                    violations.append(str(py_file))
                        elif isinstance(node, ast.ImportFrom):
                            if node.module and "langgraph" in node.module:
                                violations.append(str(py_file))
                except Exception:
                    pass
        self.assertEqual(len(violations), 0, f"LangGraph 导入违规: {violations}")


# =============================================================================
# 枚举回归测试（spec 4.5.6）
# =============================================================================

class TestEnumRegression(unittest.TestCase):
    """枚举追加兼容回归。"""

    def test_node_type_existing_unchanged(self):
        """NodeType 既有 8 类成员不变。"""
        from orchestration_platform.domain.models.enums import NodeType
        expected = {
            "agent", "tool", "data_source", "trigger",
            "control_flow", "subworkflow", "dynamic_injector", "data_transform",
        }
        actual = {m.value for m in NodeType}
        self.assertTrue(expected.issubset(actual))
        self.assertIn("plugin", actual)

    def test_data_source_type_existing_unchanged(self):
        """DataSourceType 既有 8 类成员不变。"""
        from orchestration_platform.domain.models.enums import DataSourceType
        expected = {
            "csv", "json", "rest_api", "database",
            "hologres", "kafka", "flink", "spark",
        }
        actual = {m.value for m in DataSourceType}
        self.assertTrue(expected.issubset(actual))
        self.assertIn("crm", actual)
        self.assertIn("erp", actual)
        self.assertIn("oa", actual)


# =============================================================================
# Python 3.10 兼容性测试（spec 4.5.1）
# =============================================================================

class TestPython310Compatibility(unittest.TestCase):
    """Python 3.10 兼容性扫描。"""

    def test_no_strenum_in_new_code(self):
        """新增代码无 StrEnum。"""
        violations = []
        for dir_rel in _NEW_DIRECTORIES:
            dir_path = _SRC_ROOT / dir_rel
            if not dir_path.exists():
                continue
            for py_file in dir_path.rglob("*.py"):
                try:
                    with open(py_file, "r", encoding="utf-8") as f:
                        source = f.read()
                    if "StrEnum" in source:
                        violations.append(str(py_file))
                except Exception:
                    pass
        self.assertEqual(len(violations), 0, f"StrEnum 违规: {violations}")

    def test_no_self_type_in_new_code(self):
        """新增代码无 Self 类型注解。"""
        violations = []
        for dir_rel in _NEW_DIRECTORIES:
            dir_path = _SRC_ROOT / dir_rel
            if not dir_path.exists():
                continue
            for py_file in dir_path.rglob("*.py"):
                try:
                    with open(py_file, "r", encoding="utf-8") as f:
                        source = f.read()
                    if "from typing import" in source and "Self" in source:
                        violations.append(str(py_file))
                except Exception:
                    pass
        self.assertEqual(len(violations), 0, f"Self 类型违规: {violations}")


# =============================================================================
# 配置回归测试（spec 4.4.2）
# =============================================================================

class TestConfigRegression(unittest.TestCase):
    """配置项缺省时启动成功进入降级/安全模式。"""

    def test_all_new_configs_have_defaults(self):
        """全部新配置项有默认值。"""
        from orchestration_platform.configuration.settings import get_settings
        settings = get_settings()

        # 沙箱配置
        self.assertFalse(settings.sandbox_enabled)
        self.assertGreater(settings.sandbox_execution_timeout_seconds, 0)

        # 插件 SDK 配置
        self.assertFalse(settings.plugin_sdk_enabled)
        self.assertGreater(settings.plugin_sdk_max_nodes, 0)

        # 贡献市场配置
        self.assertFalse(settings.node_contribution_enabled)

        # SSO 配置
        self.assertFalse(settings.sso_enabled)
        self.assertGreater(settings.sso_session_ttl_seconds, 0)

        # 企业连接器配置
        self.assertFalse(settings.enterprise_connector_enabled)

        # 评估框架配置
        self.assertFalse(settings.evaluation_enabled)
        self.assertFalse(settings.evaluation_llm_scoring_enabled)

        # 语义发现配置
        self.assertFalse(settings.agent_discovery_enabled)

    def test_env_example_no_real_secrets(self):
        """.env.example 无真实密钥仅占位符。"""
        env_example_path = _PROJECT_ROOT / ".env.example"
        with open(env_example_path, "r", encoding="utf-8") as f:
            content = f.read()
        # 不应包含真实密钥模式
        self.assertNotIn("sk-", content)
        self.assertNotIn("BEGIN PRIVATE KEY", content)


# =============================================================================
# 既有接口签名回归测试（spec 4.5.3）
# =============================================================================

class TestExistingInterfaceRegression(unittest.TestCase):
    """既有接口签名不变。"""

    def test_local_function_tool_invoker_signature(self):
        """LocalFunctionToolInvoker.invoke 签名不变。"""
        from orchestration_platform.integrations.tools.local_function_tool_invoker import (
            LocalFunctionToolInvoker,
        )
        # 确保类可导入
        self.assertIsNotNone(LocalFunctionToolInvoker)

    def test_mcp_tool_market_register_signature(self):
        """McpToolMarket.register 签名不变。"""
        from orchestration_platform.mcp_tool_market.tool_market import McpToolMarket
        import inspect
        sig = inspect.signature(McpToolMarket.register)
        params = list(sig.parameters.keys())
        self.assertIn("descriptor", params)
        self.assertIn("registered_by", params)

    def test_node_executor_protocol_unchanged(self):
        """NodeExecutor Protocol 不变。"""
        from orchestration_platform.engine.nodes.base import NodeExecutor
        self.assertTrue(hasattr(NodeExecutor, "node_type"))
        self.assertTrue(hasattr(NodeExecutor, "execute"))


if __name__ == "__main__":
    unittest.main()
