# =============================================================================
# 执行器注册接线测试（llm_code_nodes_v2 任务 6.4：T2）
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_executor_registry_wiring.py -v
#
# 覆盖（CHG-03 / DC-07 / DC-08 / FR-02 / AC-03）：
#   T2-1 生产装配后注册表含 12 类执行器（10 既有 + llm + code），既有
#       9 类无条件执行器零缺失；Llm/Code 执行器类型正确；
#   T2-2 llm_client 单次构造且 Agent/LLM 共享同一实例（构造次数较基线
#       不增加，6.2 验收补充）；
#   T2-3 工厂失败 → LLM 执行器跳过注册 + 告警日志、其余执行器不受影响、
#       注册表构造不失败（不拖垮启动，FR-02 异常场景 1）；
#   T2-4 注册与开关解耦：开关 True/False 两种状态下注册结果一致
#       （注册过程零开关读取，DC-08）；
#   T2-5 执行期语义：开关关闭时调度取到执行器并返回 *_NODE_DISABLED，
#       而非 NODE_TOOL_NOT_FOUND（始终注册 + execute 内判开关）。
# N 级登记：无（本文件全部为注册装配与执行期开关语义，可同步验证）。
# =============================================================================

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from orchestration_platform.domain.errors import (
    CODE_NODE_DISABLED,
    LLM_NODE_DISABLED,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    CodeNodeConfiguration,
    LlmNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes.base import NodeExecutionContext
from orchestration_platform.engine.nodes.code_node_executor import CodeNodeExecutor
from orchestration_platform.engine.nodes.llm_node_executor import LlmNodeExecutor


def run_coro(coro):
    """手动驱动协程至完成（沙箱事件循环不可用时的 fallback）。

    开关关闭分支无 await 挂起点，以 send(None) 循环驱动至 StopIteration，
    环境无关（参照 test_llm_node_executor_v2.py 同款范式）。
    """
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


def _full_llm_settings(
    llm_node_enabled: bool = False,
    code_node_enabled: bool = False,
) -> MagicMock:
    """完整主模型配置 mock（工厂可构造客户端）+ 双开关字段。"""
    settings = MagicMock()
    settings.llm_primary_base_url = "http://localhost:8080"
    settings.llm_primary_api_key = "sk-key"
    settings.llm_primary_model = "gpt-4"
    settings.llm_primary_timeout_seconds = 60
    settings.llm_fallback_base_url = ""
    settings.llm_fallback_api_key = ""
    settings.llm_fallback_model = ""
    settings.llm_fallback_timeout_seconds = 60
    settings.llm_node_enabled = llm_node_enabled
    settings.code_node_enabled = code_node_enabled
    return settings


def _make_context(node_type: NodeType, configuration, node_id: str, output_name: str) -> NodeExecutionContext:
    """构造最小执行上下文（开关关闭分支只需定义与共享状态骨架）。"""
    node_def = NodeDefinition(
        node_id=node_id,
        name=f"{node_id} node",
        node_type=node_type,
        configuration=configuration,
        output_name=output_name,
    )
    shared = SharedState(instance_id="inst-1")
    return NodeExecutionContext(
        node_definition=node_def,
        shared_state=shared,
        instance_id="inst-1",
        trace_id="trace-1",
        timeout_seconds=30,
    )


class TestRegistryWiring:
    """T2-1：生产装配后注册表含 12 类执行器（CHG-03/DC-07/AC-03）。"""

    def test_registry_contains_twelve_executors(self):
        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=_full_llm_settings(),
        ):
            from orchestration_platform.api.routers.execution_instance_router import (
                _build_executor_registry,
            )

            registry = _build_executor_registry()

        assert registry is not None
        # 12 类 = 10 既有 + llm + code（既有 10 项顺序与内容零变更，仅追加）
        assert len(registry.registered_types()) == 12
        assert registry.has_executor(NodeType.LLM)
        assert registry.has_executor(NodeType.CODE)
        assert isinstance(registry.get_executor(NodeType.LLM), LlmNodeExecutor)
        assert isinstance(registry.get_executor(NodeType.CODE), CodeNodeExecutor)
        # 既有 9 类无条件执行器零缺失（Agent 为条件注册，单独断言）
        for node_type in (
            NodeType.TRIGGER,
            NodeType.CONTROL_FLOW,
            NodeType.DATA_SOURCE,
            NodeType.DATA_TRANSFORM,
            NodeType.TOOL,
            NodeType.DYNAMIC_INJECTOR,
            NodeType.SUBWORKFLOW,
            NodeType.OUTPUT_DISPLAY,
            NodeType.VECTOR_KNOWLEDGE_BASE,
        ):
            assert registry.has_executor(node_type), f"{node_type} 执行器缺失"
        # 完整 mock 配置下 Agent 与 LLM 均注册成功
        assert registry.has_executor(NodeType.AGENT)


class TestSharedLlmClientConstruction:
    """T2-2：llm_client 单次构造，Agent/LLM 共享同一实例（6.2 验收补充）。"""

    def test_llm_client_built_once_and_shared(self):
        sentinel = object()
        with patch(
            "orchestration_platform.api.routers.execution_instance_router._build_llm_client",
            return_value=sentinel,
        ) as factory:
            from orchestration_platform.api.routers.execution_instance_router import (
                _build_executor_registry,
            )

            registry = _build_executor_registry()

        # 构造次数较基线不增加：单次构造（基线为 Agent 分支内一次）
        assert factory.call_count == 1
        agent_executor = registry.get_executor(NodeType.AGENT)
        llm_executor = registry.get_executor(NodeType.LLM)
        assert agent_executor._llm_client is sentinel
        assert llm_executor._llm_client is sentinel


class TestFactoryFailureSkip:
    """T2-3：工厂失败 → LLM 跳过注册 + 告警，不拖垮启动（FR-02）。"""

    def test_llm_skipped_with_warning_others_unaffected(self, caplog):
        with patch(
            "orchestration_platform.api.routers.execution_instance_router._build_llm_client",
            return_value=None,
        ):
            from orchestration_platform.api.routers.execution_instance_router import (
                _build_executor_registry,
            )

            with caplog.at_level("WARNING"):
                registry = _build_executor_registry()

        # 不拖垮启动：注册表构造成功且其余执行器不受影响
        assert registry is not None
        assert not registry.has_executor(NodeType.LLM)
        # Agent 与 LLM 同源共享工厂结果：None 时两者一致跳过
        assert not registry.has_executor(NodeType.AGENT)
        assert registry.has_executor(NodeType.CODE)
        assert registry.has_executor(NodeType.TRIGGER)
        assert registry.has_executor(NodeType.VECTOR_KNOWLEDGE_BASE)
        # 跳过告警日志（与 Agent 分支既有告警格式一致）
        assert "LLM 执行器注册跳过" in caplog.text


class TestSwitchDecoupling:
    """T2-4：注册与开关解耦（DC-08：注册过程零开关读取）。"""

    def test_registration_same_for_switch_true_and_false(self):
        from orchestration_platform.api.routers.execution_instance_router import (
            _build_executor_registry,
        )

        for enabled in (False, True):
            llm_settings = _full_llm_settings(
                llm_node_enabled=enabled,
                code_node_enabled=enabled,
            )
            with patch(
                "orchestration_platform.configuration.settings.get_settings",
                return_value=llm_settings,
            ), patch(
                "orchestration_platform.engine.nodes.llm_node_executor.get_settings",
                return_value=llm_settings,
            ), patch(
                "orchestration_platform.engine.nodes.code_node_executor.get_settings",
                return_value=llm_settings,
            ):
                registry = _build_executor_registry()

            # 两种开关状态下注册结果一致（has 断言恒真）
            assert registry.has_executor(NodeType.LLM), (
                f"llm_node_enabled={enabled} 时 LLM 执行器应已注册（注册不依赖开关）"
            )
            assert registry.has_executor(NodeType.CODE), (
                f"code_node_enabled={enabled} 时 Code 执行器应已注册（注册不依赖开关）"
            )


class TestExecutionSemantics:
    """T2-5：开关关闭 → 调度取到执行器并返回 *_NODE_DISABLED（非 NODE_TOOL_NOT_FOUND）。"""

    def test_disabled_returns_disabled_code_not_tool_not_found(self):
        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=_full_llm_settings(),
        ):
            from orchestration_platform.api.routers.execution_instance_router import (
                _build_executor_registry,
            )

            registry = _build_executor_registry()

        # 调度取到执行器：get_executor 不抛（注册表内已登记，非 NODE_TOOL_NOT_FOUND）
        llm_executor = registry.get_executor(NodeType.LLM)
        code_executor = registry.get_executor(NodeType.CODE)

        # LLM 开关关闭 → LLM_NODE_DISABLED（DC-08：始终注册 + execute 内判开关）
        llm_executor._settings = SimpleNamespace(llm_node_enabled=False)
        result = run_coro(
            llm_executor.execute(
                _make_context(
                    NodeType.LLM,
                    LlmNodeConfiguration(model_identifier="primary-model"),
                    "llm-1",
                    "llm_result",
                )
            )
        )
        assert result.success is False
        assert result.error_code == LLM_NODE_DISABLED

        # Code 开关关闭 → CODE_NODE_DISABLED（同上）
        code_executor._settings = SimpleNamespace(code_node_enabled=False)
        result = run_coro(
            code_executor.execute(
                _make_context(
                    NodeType.CODE,
                    CodeNodeConfiguration(code="result = 1"),
                    "code-1",
                    "code_result",
                )
            )
        )
        assert result.success is False
        assert result.error_code == CODE_NODE_DISABLED
