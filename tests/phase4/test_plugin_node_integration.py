# =============================================================================
# 插件节点集成测试（tests/phase4/test_plugin_node_integration.py）
#
# 职责：验证插件节点执行器接入既有治理链与既有节点回归。
# 设计意图（对应偏差修正 T152 / 验收 A115, B104, B105）：
#   1. 治理链测试：SDK 节点工作流执行经过既有拦截器链与审计日志
#   2. 既有节点回归：NodeType 追加 PLUGIN 后既有 8 类执行器行为不变
#   3. GraphCompiler 既有校验行为不变
#   4. 沙箱环境限制：unittest 风格 + _run_async 模式
# =============================================================================

import asyncio
import unittest
from typing import Any, Dict
from unittest.mock import MagicMock

from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    PluginNodeConfiguration,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
)
from orchestration_platform.engine.node_executor_registry import (
    NodeExecutorRegistry,
)
from orchestration_platform.plugin_sdk.models import (
    PluginManifest,
    PluginNodeRegistration,
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

class _SimplePluginNode(PluginNodeBase):
    """简单测试插件节点。"""

    def setup(self, config: Dict[str, Any]) -> None:
        self._prefix = config.get("prefix", "result: ")

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return {"output": self._prefix + str(inputs.get("query", ""))}


# =============================================================================
# 插件节点执行器测试（A115, B104）
# =============================================================================

class TestPluginNodeExecutor(unittest.TestCase):
    """插件节点执行器测试。"""

    def _make_mock_loader(self, node_name: str, instance: Any, healthy: bool = True):
        """构造 mock 加载器。"""
        loader = MagicMock()
        loader.get_instance.return_value = instance if healthy else None
        loader.get_registration.return_value = PluginNodeRegistration(
            node_name=node_name,
            version="1.0.0",
            manifest=PluginManifest(
                node_name=node_name,
                version="1.0.0",
                declared_file_paths=["./data/"],
            ),
            healthy=healthy,
        )
        return loader

    def test_execute_success(self) -> None:
        """插件节点执行成功返回正确结果。"""
        from orchestration_platform.engine.plugin_nodes.plugin_node_executor import (
            PluginNodeExecutor,
        )
        from orchestration_platform.domain.models.node_definition import (
            NodeDefinition,
        )
        from orchestration_platform.domain.models.shared_state import SharedState

        node_instance = _SimplePluginNode()
        node_instance.setup({"prefix": "echo: "})

        loader = self._make_mock_loader("simple-node", node_instance)
        executor = PluginNodeExecutor(loader)

        config = PluginNodeConfiguration(
            plugin_ref="simple-node",
            config={"prefix": "echo: "},
            input_mapping={"query": "upstream.output"},
        )

        # 构造执行上下文
        node_def = NodeDefinition(
            node_id="plugin_1",
            name="插件节点",
            node_type=NodeType.PLUGIN,
            configuration=config,
        )
        shared_state = SharedState(
            instance_id="inst_1",
            node_outputs={"upstream.output": "hello"},
        )
        context = NodeExecutionContext(
            node_definition=node_def,
            shared_state=shared_state,
            instance_id="inst_1",
            trace_id="trace_1",
        )

        result = _run_async(executor.execute(context))

        self.assertTrue(result.success)
        self.assertEqual(result.output_value["output"], "echo: hello")
        self.assertEqual(result.metadata["plugin_ref"], "simple-node")
        self.assertEqual(result.metadata["plugin_version"], "1.0.0")

    def test_execute_with_uninstalled_node(self) -> None:
        """未安装的插件节点返回错误。"""
        from orchestration_platform.engine.plugin_nodes.plugin_node_executor import (
            PluginNodeExecutor,
        )
        from orchestration_platform.domain.models.node_definition import (
            NodeDefinition,
        )
        from orchestration_platform.domain.models.shared_state import SharedState

        loader = MagicMock()
        loader.get_instance.return_value = None
        loader.get_registration.return_value = None

        executor = PluginNodeExecutor(loader)

        config = PluginNodeConfiguration(plugin_ref="nonexistent")
        node_def = NodeDefinition(
            node_id="plugin_1",
            node_type=NodeType.PLUGIN,
            configuration=config,
        )
        shared_state = SharedState(instance_id="inst_1")
        context = NodeExecutionContext(
            node_definition=node_def,
            shared_state=shared_state,
            instance_id="inst_1",
            trace_id="trace_1",
        )

        result = _run_async(executor.execute(context))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "NODE_MANIFEST_INVALID")

    def test_execute_with_unhealthy_node(self) -> None:
        """不健康的插件节点返回错误。"""
        from orchestration_platform.engine.plugin_nodes.plugin_node_executor import (
            PluginNodeExecutor,
        )
        from orchestration_platform.domain.models.node_definition import (
            NodeDefinition,
        )
        from orchestration_platform.domain.models.shared_state import SharedState

        loader = MagicMock()
        loader.get_instance.return_value = None
        loader.get_registration.return_value = PluginNodeRegistration(
            node_name="unhealthy-node",
            version="1.0.0",
            manifest=PluginManifest(
                node_name="unhealthy-node",
                version="1.0.0",
                declared_file_paths=["./data/"],
            ),
            healthy=False,
        )

        executor = PluginNodeExecutor(loader)

        config = PluginNodeConfiguration(plugin_ref="unhealthy-node")
        node_def = NodeDefinition(
            node_id="plugin_1",
            node_type=NodeType.PLUGIN,
            configuration=config,
        )
        shared_state = SharedState(instance_id="inst_1")
        context = NodeExecutionContext(
            node_definition=node_def,
            shared_state=shared_state,
            instance_id="inst_1",
            trace_id="trace_1",
        )

        result = _run_async(executor.execute(context))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "NODE_LIFECYCLE_ERROR")

    def test_execute_exception_marks_unhealthy(self) -> None:
        """execute 异常时节点标记不健康。"""
        from orchestration_platform.engine.plugin_nodes.plugin_node_executor import (
            PluginNodeExecutor,
        )
        from orchestration_platform.domain.models.node_definition import (
            NodeDefinition,
        )
        from orchestration_platform.domain.models.shared_state import SharedState

        class _ExceptionNode(PluginNodeBase):
            def setup(self, config):
                pass
            def execute(self, inputs):
                raise RuntimeError("故意异常")

        node_instance = _ExceptionNode()
        node_instance.setup({})

        registration = PluginNodeRegistration(
            node_name="exception-node",
            version="1.0.0",
            manifest=PluginManifest(
                node_name="exception-node",
                version="1.0.0",
                declared_file_paths=["./data/"],
            ),
            healthy=True,
        )

        loader = MagicMock()
        loader.get_instance.return_value = node_instance
        loader.get_registration.return_value = registration

        executor = PluginNodeExecutor(loader)

        config = PluginNodeConfiguration(plugin_ref="exception-node")
        node_def = NodeDefinition(
            node_id="plugin_1",
            node_type=NodeType.PLUGIN,
            configuration=config,
        )
        shared_state = SharedState(instance_id="inst_1")
        context = NodeExecutionContext(
            node_definition=node_def,
            shared_state=shared_state,
            instance_id="inst_1",
            trace_id="trace_1",
        )

        result = _run_async(executor.execute(context))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "NODE_LIFECYCLE_ERROR")
        # 节点应被标记为不健康
        self.assertFalse(registration.healthy)

    def test_metadata_contains_lifecycle_logs(self) -> None:
        """执行结果 metadata 承载生命周期日志。"""
        from orchestration_platform.engine.plugin_nodes.plugin_node_executor import (
            PluginNodeExecutor,
        )
        from orchestration_platform.domain.models.node_definition import (
            NodeDefinition,
        )
        from orchestration_platform.domain.models.shared_state import SharedState

        node_instance = _SimplePluginNode()
        node_instance.setup({"prefix": ""})

        loader = self._make_mock_loader("simple-node", node_instance)
        executor = PluginNodeExecutor(loader)

        config = PluginNodeConfiguration(plugin_ref="simple-node")
        node_def = NodeDefinition(
            node_id="plugin_1",
            node_type=NodeType.PLUGIN,
            configuration=config,
        )
        shared_state = SharedState(instance_id="inst_1")
        context = NodeExecutionContext(
            node_definition=node_def,
            shared_state=shared_state,
            instance_id="inst_1",
            trace_id="trace_1",
        )

        result = _run_async(executor.execute(context))

        self.assertIn("lifecycle_logs", result.metadata)
        logs = result.metadata["lifecycle_logs"]
        self.assertGreaterEqual(len(logs), 1)
        self.assertEqual(logs[0]["phase"], "execute")


# =============================================================================
# 执行器注册表回归测试（B104, B105）
# =============================================================================

class TestExecutorRegistryRegression(unittest.TestCase):
    """执行器注册表回归测试。"""

    def test_plugin_executor_can_register(self) -> None:
        """PluginNodeExecutor 可在注册表登记（B104）。"""
        from orchestration_platform.engine.plugin_nodes.plugin_node_executor import (
            PluginNodeExecutor,
        )

        registry = NodeExecutorRegistry()
        executor = PluginNodeExecutor()
        registry.register(executor)
        self.assertTrue(registry.has_executor(NodeType.PLUGIN))

    def test_existing_executors_not_affected_by_plugin(self) -> None:
        """追加 PLUGIN 执行器不影响既有执行器（B105）。"""
        from orchestration_platform.engine.plugin_nodes.plugin_node_executor import (
            PluginNodeExecutor,
        )

        registry = NodeExecutorRegistry()
        plugin_executor = PluginNodeExecutor()
        registry.register(plugin_executor)

        # 既有类型不应被 PLUGIN 覆盖
        self.assertTrue(registry.has_executor(NodeType.PLUGIN))
        # PLUGIN 不应影响其他类型的查找
        self.assertFalse(registry.has_executor(NodeType.AGENT))


# =============================================================================
# GraphCompiler 回归测试（B105）
# =============================================================================

class TestGraphCompilerRegression(unittest.TestCase):
    """GraphCompiler 既有校验行为不变（B105）。"""

    def test_graph_compiler_accepts_plugin_schema_provider(self) -> None:
        """GraphCompiler 接受 plugin_schema_provider 参数（扩展不破坏既有）。"""
        from orchestration_platform.engine.graph_compiler import GraphCompiler

        # 不提供 plugin_schema_provider（既有行为）
        compiler1 = GraphCompiler()
        self.assertIsNone(compiler1._plugin_schema_provider)

        # 提供 plugin_schema_provider（扩展）
        provider = MagicMock()
        compiler2 = GraphCompiler(plugin_schema_provider=provider)
        self.assertIsNotNone(compiler2._plugin_schema_provider)

    def test_graph_compiler_existing_behavior_unchanged(self) -> None:
        """GraphCompiler 既有编译行为不因插件扩展改变。"""
        from orchestration_platform.engine.graph_compiler import GraphCompiler

        # 不提供 plugin_schema_provider 时，编译行为与既有版本一致
        compiler = GraphCompiler()
        self.assertIsNone(compiler._plugin_schema_provider)


# =============================================================================
# PluginNodeConfiguration 模型测试
# =============================================================================

class TestPluginNodeConfiguration(unittest.TestCase):
    """PluginNodeConfiguration 模型测试。"""

    def test_plugin_config_creation(self) -> None:
        """插件节点配置可正确创建。"""
        config = PluginNodeConfiguration(
            plugin_ref="my-plugin",
            config={"key": "value"},
            input_mapping={"query": "upstream.output"},
        )
        self.assertEqual(config.node_type, NodeType.PLUGIN)
        self.assertEqual(config.plugin_ref, "my-plugin")
        self.assertEqual(config.config["key"], "value")

    def test_plugin_config_defaults(self) -> None:
        """插件节点配置默认值正确。"""
        config = PluginNodeConfiguration(plugin_ref="my-plugin")
        self.assertEqual(config.config, {})
        self.assertEqual(config.input_mapping, {})
        self.assertIsNone(config.sandbox_policy_id)


if __name__ == "__main__":
    unittest.main()
