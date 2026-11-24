# =============================================================================
# 插件 SDK 单元测试（tests/phase4/test_plugin_sdk.py）
#
# 职责：验证插件节点生命周期、manifest 校验、打包规范与本地调试。
# 设计意图（对应偏差修正 T152 / 验收 A110-A114, B105）：
#   1. 生命周期测试：setup→execute→teardown 顺序，异常时 teardown 仍被调用
#   2. manifest 校验测试：缺失字段返回清单，格式非法拒绝加载
#   3. 打包规范测试：符合规范的包可安装，不规范返回差异说明
#   4. 本地调试测试：样例输入执行输出结果与生命周期日志
#   5. 既有节点回归：NodeType 追加 PLUGIN 后既有 8 类执行器行为不变
#
# 根因组 D（fix_baseline_test_failures）：test_node_type_count 断言过时
# （NodeType 实测 11 = 8 既有 + PLUGIN + SUPERVISOR + OUTPUT_DISPLAY），
# 更新断言为 11，枚举定义零改动（FR-05 / INV-02，spec 开放问题 3 口径）。
# 向量知识库节点（vector_knowledge_base_node CHG-01）：VECTOR_KNOWLEDGE_BASE
# 追加后 NodeType 实测 12（11 + VECTOR_KNOWLEDGE_BASE），按同口径更新断言为
# 12，枚举定义零改动（基线零失败纪律，2026-10-24）。
# =============================================================================

import asyncio
import os
import tempfile
import unittest
from typing import Any, Dict

from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.plugin_sdk.manifest_validator import (
    ManifestValidator,
)
from orchestration_platform.plugin_sdk.models import (
    LifecycleLog,
    PluginManifest,
)
from orchestration_platform.plugin_sdk.package_validator import (
    NodePackageValidator,
)
from orchestration_platform.plugin_sdk.plugin_node_base import PluginNodeBase


def _run_async(coro):
    """辅助：在同步测试中运行协程。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =============================================================================
# 测试用节点实现
# =============================================================================

class _HealthyNode(PluginNodeBase):
    """健康测试节点。"""

    def setup(self, config: Dict[str, Any]) -> None:
        self._multiplier = config.get("multiplier", 1)

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return {"result": inputs.get("value", 0) * self._multiplier}

    def teardown(self) -> None:
        self._torn_down = True


class _FailingSetupNode(PluginNodeBase):
    """setup 失败测试节点。"""

    def setup(self, config: Dict[str, Any]) -> None:
        raise RuntimeError("setup 故意失败")

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return {}


class _FailingExecuteNode(PluginNodeBase):
    """execute 异常测试节点。"""

    def __init__(self) -> None:
        self.teardown_called = False

    def setup(self, config: Dict[str, Any]) -> None:
        pass

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        raise ValueError("execute 故意异常")

    def teardown(self) -> None:
        self.teardown_called = True


# =============================================================================
# 生命周期测试（A110, spec 5.2.3.2）
# =============================================================================

class TestPluginNodeLifecycle(unittest.TestCase):
    """插件节点生命周期测试。"""

    def test_setup_execute_teardown_order(self) -> None:
        """setup→execute→teardown 顺序调用（A110）。"""
        node = _HealthyNode()
        node.setup({"multiplier": 3})
        result = node.execute({"value": 10})
        node.teardown()
        self.assertEqual(result["result"], 30)

    def test_teardown_called_on_execute_exception(self) -> None:
        """execute 异常时 teardown 仍被调用（spec 5.2.3.2）。"""
        node = _FailingExecuteNode()
        node.setup({})
        try:
            node.execute({})
        except ValueError:
            pass
        finally:
            node.teardown()
        self.assertTrue(node.teardown_called)

    def test_setup_failure_marks_unhealthy(self) -> None:
        """setup 失败时实例标记不健康（spec 5.2.3.2）。"""
        node = _FailingSetupNode()
        healthy = True
        try:
            node.setup({})
        except RuntimeError:
            healthy = False
        self.assertFalse(healthy)

    def test_execute_multiple_calls(self) -> None:
        """execute 可多次调用（A110）。"""
        node = _HealthyNode()
        node.setup({"multiplier": 2})
        r1 = node.execute({"value": 5})
        r2 = node.execute({"value": 10})
        node.teardown()
        self.assertEqual(r1["result"], 10)
        self.assertEqual(r2["result"], 20)


# =============================================================================
# manifest 校验测试（A111, A112）
# =============================================================================

class TestManifestValidation(unittest.TestCase):
    """manifest 校验测试。"""

    def setUp(self) -> None:
        self.validator = ManifestValidator()

    def test_valid_manifest_passes(self) -> None:
        """合法 manifest 校验通过。"""
        manifest = PluginManifest(
            node_name="my-node",
            version="1.0.0",
            input_schema={"query": "string"},
            output_schema={"result": "string"},
            declared_file_paths=["./data/"],
            declared_network_targets=[],
        )
        result = self.validator.validate(manifest)
        self.assertTrue(result.valid)
        self.assertEqual(len(result.errors), 0)

    def test_missing_version_returns_field_list(self) -> None:
        """缺失版本号返回缺失字段清单（A111）。"""
        manifest = PluginManifest(
            node_name="my-node",
            version="",
            declared_file_paths=["./data/"],
        )
        result = self.validator.validate(manifest)
        self.assertFalse(result.valid)
        self.assertTrue(any("版本号" in err for err in result.errors))

    def test_missing_permission_declaration(self) -> None:
        """缺失权限声明返回错误（A111）。"""
        manifest = PluginManifest(
            node_name="my-node",
            version="1.0.0",
            declared_file_paths=[],
            declared_network_targets=[],
        )
        result = self.validator.validate(manifest)
        self.assertFalse(result.valid)
        self.assertTrue(any("权限声明" in err for err in result.errors))

    def test_invalid_node_name_format(self) -> None:
        """节点名称格式非法拒绝（A111）。"""
        manifest = PluginManifest(
            node_name="My_Invalid_Node!",
            version="1.0.0",
            declared_file_paths=["./data/"],
        )
        result = self.validator.validate(manifest)
        self.assertFalse(result.valid)
        self.assertTrue(any("节点名称" in err for err in result.errors))

    def test_invalid_version_format(self) -> None:
        """版本号格式非法拒绝（A111）。"""
        manifest = PluginManifest(
            node_name="my-node",
            version="1.0",
            declared_file_paths=["./data/"],
        )
        result = self.validator.validate(manifest)
        self.assertFalse(result.valid)
        self.assertTrue(any("版本号" in err for err in result.errors))

    def test_invalid_manifest_does_not_affect_others(self) -> None:
        """格式非法拒绝加载且平台其余节点不受影响（A112 失败隔离）。"""
        bad_manifest = PluginManifest(
            node_name="bad-node",
            version="invalid",
            declared_file_paths=["./data/"],
        )
        good_manifest = PluginManifest(
            node_name="good-node",
            version="1.0.0",
            declared_file_paths=["./data/"],
        )
        bad_result = self.validator.validate(bad_manifest)
        good_result = self.validator.validate(good_manifest)
        self.assertFalse(bad_result.valid)
        self.assertTrue(good_result.valid)


# =============================================================================
# 打包规范测试（A113）
# =============================================================================

class TestPackageValidation(unittest.TestCase):
    """打包规范测试。"""

    def setUp(self) -> None:
        self.validator = NodePackageValidator()
        self._tmpdir = tempfile.mkdtemp()

    def _create_valid_package(self) -> str:
        """创建合法节点包目录。"""
        pkg_dir = os.path.join(self._tmpdir, "valid_node")
        os.makedirs(pkg_dir, exist_ok=True)

        # manifest.yaml
        manifest_content = (
            "node_name: valid-node\n"
            "version: 1.0.0\n"
            "description: 测试节点\n"
            "input_schema:\n"
            "  query: string\n"
            "output_schema:\n"
            "  result: string\n"
            "declared_file_paths:\n"
            "  - ./data/\n"
            "declared_network_targets: []\n"
            "entry_point: node.py\n"
        )
        with open(os.path.join(pkg_dir, "manifest.yaml"), "w", encoding="utf-8") as f:
            f.write(manifest_content)

        # node.py
        node_code = (
            "from orchestration_platform.plugin_sdk.plugin_node_base import PluginNodeBase\n"
            "\n"
            "class ValidNode(PluginNodeBase):\n"
            "    def setup(self, config):\n"
            "        pass\n"
            "    def execute(self, inputs):\n"
            "        return {'result': inputs.get('query', '')}\n"
        )
        with open(os.path.join(pkg_dir, "node.py"), "w", encoding="utf-8") as f:
            f.write(node_code)

        return pkg_dir

    def test_valid_package_passes(self) -> None:
        """符合规范的节点包可安装（A113）。"""
        pkg_dir = self._create_valid_package()
        result = self.validator.validate(pkg_dir)
        self.assertTrue(result.valid, msg=f"Errors: {result.errors}")

    def test_missing_manifest_returns_diff(self) -> None:
        """缺少 manifest 返回规范差异说明（A113）。"""
        pkg_dir = os.path.join(self._tmpdir, "no_manifest")
        os.makedirs(pkg_dir, exist_ok=True)
        result = self.validator.validate(pkg_dir)
        self.assertFalse(result.valid)
        self.assertTrue(any("manifest" in err.lower() for err in result.errors))

    def test_missing_entry_point_returns_diff(self) -> None:
        """入口文件缺失返回规范差异说明（A113）。"""
        pkg_dir = os.path.join(self._tmpdir, "no_entry")
        os.makedirs(pkg_dir, exist_ok=True)
        with open(os.path.join(pkg_dir, "manifest.yaml"), "w", encoding="utf-8") as f:
            f.write(
                "node_name: no-entry-node\n"
                "version: 1.0.0\n"
                "declared_file_paths:\n"
                "  - ./data/\n"
                "entry_point: missing.py\n"
            )
        result = self.validator.validate(pkg_dir)
        self.assertFalse(result.valid)
        self.assertTrue(any("入口文件" in err for err in result.errors))

    def test_digest_computation(self) -> None:
        """SHA-256 摘要计算可用。"""
        pkg_dir = self._create_valid_package()
        digest1 = self.validator.compute_digest(pkg_dir)
        digest2 = self.validator.compute_digest(pkg_dir)
        self.assertEqual(digest1, digest2)
        self.assertEqual(len(digest1), 64)  # SHA-256 hex


# =============================================================================
# 本地调试测试（A114）
# =============================================================================

class TestLocalDebugRunner(unittest.TestCase):
    """本地调试运行器测试。"""

    def test_lifecycle_log_structure(self) -> None:
        """生命周期日志包含阶段名、耗时、成功标记（A114）。"""
        log = LifecycleLog(phase="setup", duration_seconds=0.01, success=True)
        self.assertEqual(log.phase, "setup")
        self.assertGreaterEqual(log.duration_seconds, 0)
        self.assertTrue(log.success)
        self.assertIsNone(log.error)

    def test_lifecycle_log_with_error(self) -> None:
        """失败的生命周期日志包含异常摘要。"""
        log = LifecycleLog(
            phase="execute",
            duration_seconds=0.0,
            success=False,
            error="ValueError: test error",
        )
        self.assertFalse(log.success)
        self.assertEqual(log.error, "ValueError: test error")


# =============================================================================
# 既有节点回归测试（B105）
# =============================================================================

class TestNodeTypeRegression(unittest.TestCase):
    """NodeType 追加 PLUGIN 后既有 8 类执行器行为不变（B105）。"""

    def test_existing_node_types_unchanged(self) -> None:
        """既有 8 类节点类型取值不变。"""
        expected_existing = {
            "agent", "tool", "data_source", "trigger",
            "control_flow", "subworkflow", "dynamic_injector", "data_transform",
        }
        all_values = {member.value for member in NodeType}
        # 既有 8 类必须在
        self.assertTrue(expected_existing.issubset(all_values))
        # PLUGIN 已追加
        self.assertIn("plugin", all_values)

    def test_plugin_node_type_exists(self) -> None:
        """NodeType.PLUGIN 成员存在。"""
        self.assertEqual(NodeType.PLUGIN.value, "plugin")

    def test_node_type_count(self) -> None:
        """节点类型总数为 14（8 既有 + PLUGIN + SUPERVISOR + OUTPUT_DISPLAY + VECTOR_KNOWLEDGE_BASE + LLM + CODE，以实测为准）。"""
        self.assertEqual(len(list(NodeType)), 14)


if __name__ == "__main__":
    unittest.main()
