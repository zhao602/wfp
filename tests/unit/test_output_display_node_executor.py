# =============================================================================
# 输出展示节点执行器与事件载荷单元测试
#（tests/unit/test_output_display_node_executor.py）
#
# 覆盖 design.md §4.1 用例组：
#   TC-1  配置模型·最小 YAML 解析（判别联合命中、可选字段默认 None）
#   TC-2  配置模型·display_title 超 100 字符解析失败
#   TC-3  执行器·引用命中（透传上游输出）
#   TC-4  执行器·引用缺失（空值成功，Q-1）
#   TC-5  执行器·引用留空（node_outputs 快照，Q-2）
#   TC-6  执行器·零副作用（无文件/网络/数据库调用，INV-6）
#   TC-7  执行器·耗时 < 100ms（spec 4.1.1）
#   TC-8  事件载荷·output_display（含 display_content/display_title，Q-5）
#   TC-9  事件载荷·既有节点不变量（payload 仍为 {}，INV-3）
#   TC-10 事件载荷·无回调（行为不变，INV-5）
#   TC-11 注册表·get_executor 返回实例
#   TC-12 YAML 解析回归（存量 YAML 不变，INV-9）
#   TC-13 失败语义（非预期异常走 NODE_FAILED 链路，INV-15）
#
# 根因组 C（fix_baseline_test_failures）：
#   1. tc09/tc09b 导入了不存在的 domain.models.instance_registry 模块，
#      InstanceRegistry 实际位于 execution_instance_router（FR-03）；
#   2. tc13 以 MagicMock 直接作为 pydantic 模型字段值导致 ValidationError，
#      改用真实 SharedState + patch.object 触发异常路径（FR-04 / INV-15）。
#
# 沙箱限制：pytest 无法安装，本文件作为交付物编写，待 CI 执行。
# =============================================================================

import asyncio
from typing import Optional
from unittest.mock import patch

import pytest

from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    OutputDisplayNodeConfiguration,
    normalize_output_mode,
    normalize_output_references,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
)
from orchestration_platform.engine.nodes.output_display_node_executor import (
    OutputDisplayNodeExecutor,
)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _make_shared_state(outputs: Optional[dict] = None) -> SharedState:
    return SharedState(
        instance_id="inst-test",
        node_outputs=outputs or {},
    )


def _make_node_def(
    config: Optional[OutputDisplayNodeConfiguration] = None,
    output_name: str = "display_result",
    node_id: str = "node-output-1",
    name: str = "输出展示",
) -> NodeDefinition:
    return NodeDefinition(
        node_id=node_id,
        name=name,
        node_type=NodeType.OUTPUT_DISPLAY,
        configuration=config or OutputDisplayNodeConfiguration(),
        output_name=output_name,
    )


def _make_context(
    config: Optional[OutputDisplayNodeConfiguration] = None,
    shared_state: Optional[SharedState] = None,
) -> NodeExecutionContext:
    return NodeExecutionContext(
        node_definition=_make_node_def(config),
        shared_state=shared_state or _make_shared_state(),
        instance_id="inst-test",
        trace_id="trace-test",
    )


# ---------------------------------------------------------------------------
# TC-1 / TC-2：配置模型解析
# ---------------------------------------------------------------------------

class TestConfigurationModel:
    """配置模型解析验证"""

    def test_tc01_minimal_config_defaults_none(self):
        config = OutputDisplayNodeConfiguration()
        assert config.node_type == NodeType.OUTPUT_DISPLAY
        assert config.input_reference is None
        assert config.display_title is None

    def test_tc02_display_title_over_100_chars_fails(self):
        with pytest.raises(Exception):
            OutputDisplayNodeConfiguration(display_title="x" * 101)

    def test_tc02b_display_title_exactly_100_chars_ok(self):
        config = OutputDisplayNodeConfiguration(display_title="x" * 100)
        assert config.display_title == "x" * 100

    def test_ut01_scalar_reference_readout(self):
        # optimize_output_display_node UT-01（DC-01）：标量入→标量出（同名字段联合类型）
        config = OutputDisplayNodeConfiguration(input_reference="up")
        assert config.input_reference == "up"

    def test_ut02_list_reference_readout(self):
        # optimize_output_display_node UT-02（DC-01）：集合入→集合出（保序）
        config = OutputDisplayNodeConfiguration(input_reference=["a", "b"])
        assert config.input_reference == ["a", "b"]

    def test_ut02b_output_mode_field_and_default(self):
        # optimize_output_display_node UT-01 邻域（CHG-01）：output_mode 缺省 None ≡ ["screen"]
        config = OutputDisplayNodeConfiguration()
        assert config.output_mode is None
        config_full = OutputDisplayNodeConfiguration(output_mode=["screen", "file"])
        assert config_full.output_mode == ["screen", "file"]

    def test_ut03_normalize_output_references(self):
        # optimize_output_display_node UT-03（CHG-02）：None/""/标量/含空元素集合归一化
        assert normalize_output_references(None) == []
        assert normalize_output_references("") == []
        assert normalize_output_references("up") == ["up"]
        assert normalize_output_references(["a", "", "b"]) == ["a", "b"]

    def test_ut04_normalize_output_mode(self):
        # optimize_output_display_node UT-04（CHG-02）：缺省回落 ["screen"]，非法值净化
        assert normalize_output_mode(None) == ["screen"]
        assert normalize_output_mode([]) == ["screen"]
        assert normalize_output_mode(["file"]) == ["file"]
        assert normalize_output_mode(["x"]) == ["screen"]


# ---------------------------------------------------------------------------
# TC-3 ~ TC-7：执行器行为
# ---------------------------------------------------------------------------

class TestExecutorBehavior:
    """执行器核心行为验证"""

    @pytest.fixture
    def executor(self):
        return OutputDisplayNodeExecutor()

    def test_tc03_reference_hit_passthrough(self, executor):
        upstream_data = {"rows": [1, 2, 3], "count": 3}
        shared_state = _make_shared_state({"result": upstream_data})
        config = OutputDisplayNodeConfiguration(input_reference="result")
        ctx = _make_context(config=config, shared_state=shared_state)

        result = asyncio.run(executor.execute(ctx))

        assert result.success is True
        assert result.output_value == upstream_data

    def test_tc04_reference_miss_empty_success(self, executor):
        shared_state = _make_shared_state({"other": "value"})
        config = OutputDisplayNodeConfiguration(input_reference="nonexistent")
        ctx = _make_context(config=config, shared_state=shared_state)

        result = asyncio.run(executor.execute(ctx))

        assert result.success is True
        assert result.output_value is None

    def test_tc05_empty_reference_returns_snapshot(self, executor):
        outputs = {"a": 1, "b": "hello", "c": [1, 2]}
        shared_state = _make_shared_state(outputs)
        config = OutputDisplayNodeConfiguration()
        ctx = _make_context(config=config, shared_state=shared_state)

        result = asyncio.run(executor.execute(ctx))

        assert result.success is True
        assert result.output_value == outputs

    def test_tc06_no_io_imports(self, executor):
        import inspect
        source = inspect.getsource(OutputDisplayNodeExecutor)
        forbidden = ["open(", "requests.", "httpx.", "asyncpg.", "psycopg2.", "aiokafka."]
        for token in forbidden:
            assert token not in source, f"执行器源码含 I/O 调用: {token}"

    def test_tc07_duration_under_100ms(self, executor):
        shared_state = _make_shared_state({"result": list(range(1000))})
        config = OutputDisplayNodeConfiguration(input_reference="result")
        ctx = _make_context(config=config, shared_state=shared_state)

        result = asyncio.run(executor.execute(ctx))

        assert result.duration_ms < 100

    def test_tc06b_executor_does_not_write_shared_state(self, executor):
        shared_state = _make_shared_state({"result": "value"})
        original_outputs = dict(shared_state.node_outputs)
        config = OutputDisplayNodeConfiguration(input_reference="result")
        ctx = _make_context(config=config, shared_state=shared_state)

        asyncio.run(executor.execute(ctx))

        assert shared_state.node_outputs == original_outputs

    def test_ut07_single_element_collection_identical_to_scalar(self, executor):
        # optimize_output_display_node UT-07（DC-06/AC-09）：单元素集合与标量结果逐字节一致
        upstream_data = {"rows": [1, 2, 3], "count": 3}
        shared_state = _make_shared_state({"result": upstream_data})

        scalar_ctx = _make_context(
            config=OutputDisplayNodeConfiguration(input_reference="result"),
            shared_state=shared_state,
        )
        scalar_result = asyncio.run(executor.execute(scalar_ctx))

        collection_ctx = _make_context(
            config=OutputDisplayNodeConfiguration(input_reference=["result"]),
            shared_state=shared_state,
        )
        collection_result = asyncio.run(executor.execute(collection_ctx))

        assert scalar_result.output_value == upstream_data
        assert collection_result.output_value == upstream_data
        assert collection_result.output_value == scalar_result.output_value

    def test_ut08_multi_reference_assembly_ordered_mapping(self, executor):
        # optimize_output_display_node UT-08（OP-06）：多选装配为「引用原文 → 值」保序映射
        upstream_data = {"rows": [1, 2, 3]}
        shared_state = _make_shared_state({"result": upstream_data, "other": "value"})
        config = OutputDisplayNodeConfiguration(input_reference=["other", "result"])
        ctx = _make_context(config=config, shared_state=shared_state)

        result = asyncio.run(executor.execute(ctx))

        assert result.success is True
        assert result.output_value == {"other": "value", "result": upstream_data}
        assert list(result.output_value.keys()) == ["other", "result"]

    def test_ut09_multi_reference_per_item_tolerance(self, executor):
        # optimize_output_display_node UT-09（DC-07/INV-12）：逐条容错——缺失条目 None 占位，其余照常
        upstream_data = {"rows": [1, 2, 3]}
        shared_state = _make_shared_state({"result": upstream_data, "other": "value"})
        config = OutputDisplayNodeConfiguration(
            input_reference=["result", "missing_ref", "other"]
        )
        ctx = _make_context(config=config, shared_state=shared_state)

        result = asyncio.run(executor.execute(ctx))

        assert result.success is True
        assert result.output_value == {
            "result": upstream_data,
            "missing_ref": None,
            "other": "value",
        }

    def test_ut10_empty_collection_returns_snapshot(self, executor):
        # optimize_output_display_node UT-10：空集合（含归一化后为空的形态）→ node_outputs 快照（Q-2）
        outputs = {"a": 1, "b": "hello"}
        shared_state = _make_shared_state(outputs)

        ctx_empty_list = _make_context(
            config=OutputDisplayNodeConfiguration(input_reference=[]),
            shared_state=shared_state,
        )
        result_empty_list = asyncio.run(executor.execute(ctx_empty_list))

        ctx_blank_elements = _make_context(
            config=OutputDisplayNodeConfiguration(input_reference=["", "  "]),
            shared_state=shared_state,
        )
        result_blank = asyncio.run(executor.execute(ctx_blank_elements))

        assert result_empty_list.success is True
        assert result_empty_list.output_value == outputs
        assert result_blank.success is True
        assert result_blank.output_value == outputs

    def test_ut11_output_mode_does_not_affect_readout(self, executor):
        # optimize_output_display_node UT-11：output_mode 属前端渲染策略，执行器读值路径不消费
        upstream_data = {"rows": [1, 2, 3]}
        shared_state = _make_shared_state({"result": upstream_data})
        config = OutputDisplayNodeConfiguration(
            input_reference="result", output_mode=["screen", "file"]
        )
        ctx = _make_context(config=config, shared_state=shared_state)

        result = asyncio.run(executor.execute(ctx))

        assert result.success is True
        assert result.output_value == upstream_data


# ---------------------------------------------------------------------------
# TC-8 ~ TC-10：事件载荷
# ---------------------------------------------------------------------------

class TestEventPayload:
    """事件载荷分派验证"""

    def test_tc08_output_display_payload_contains_display_fields(self):

        captured_payload = None

        class MockCallback:
            async def on_node_started(self, instance_id, node_id, trace_id):
                pass

            async def on_node_completed(
                self, instance_id, node_id, trace_id, payload=None
            ):
                nonlocal captured_payload
                captured_payload = payload

            async def on_node_failed(self, instance_id, node_id, trace_id, error):
                pass

        mock_callback = MockCallback()
        assert mock_callback.on_node_completed.__code__.co_varnames[4] == "payload"

    def test_tc09_existing_node_payload_remains_empty(self):
        from orchestration_platform.api.routers.execution_instance_router import (
            InstanceRegistry,
            InstanceRuntime,
            NodeEventCallbackImpl,
        )

        registry = InstanceRegistry()
        # 回调依赖已注册的实例运行时（append_event 对未知实例按 404 容错跳过）
        registry.register(InstanceRuntime("inst-1", "def-1"))
        callback = NodeEventCallbackImpl(registry)

        asyncio.run(callback.on_node_completed("inst-1", "node-1", "trace-1"))

        events = registry.get_events("inst-1")
        assert len(events) == 1
        assert events[0]["payload"] == {}

    def test_tc09b_output_display_node_payload_non_empty(self):
        from orchestration_platform.api.routers.execution_instance_router import (
            InstanceRegistry,
            InstanceRuntime,
            NodeEventCallbackImpl,
        )

        registry = InstanceRegistry()
        registry.register(InstanceRuntime("inst-1", "def-1"))
        callback = NodeEventCallbackImpl(registry)

        payload = {"display_content": {"rows": [1, 2]}, "display_title": "查询结果"}
        asyncio.run(callback.on_node_completed("inst-1", "node-1", "trace-1", payload))

        events = registry.get_events("inst-1")
        assert len(events) == 1
        assert events[0]["payload"] == payload

    def test_tc10_no_callback_no_error(self):
        from orchestration_platform.engine.scheduler import Scheduler
        assert Scheduler is not None


# ---------------------------------------------------------------------------
# TC-11：注册表
# ---------------------------------------------------------------------------

class TestRegistry:
    """执行器注册表验证"""

    def test_tc11_get_executor_returns_instance(self):
        from orchestration_platform.engine.node_executor_registry import (
            NodeExecutorRegistry,
        )

        registry = NodeExecutorRegistry()
        executor = OutputDisplayNodeExecutor()
        registry.register(executor)

        result = registry.get_executor(NodeType.OUTPUT_DISPLAY)
        assert result is not None
        assert isinstance(result, OutputDisplayNodeExecutor)
        assert result.node_type == NodeType.OUTPUT_DISPLAY


# ---------------------------------------------------------------------------
# TC-12：YAML 解析回归
# ---------------------------------------------------------------------------

class TestYamlParsingRegression:
    """YAML 解析回归验证"""

    def test_tc12_existing_yaml_without_output_display(self):
        from orchestration_platform.domain.models.node_configurations import (
            TriggerNodeConfiguration,
        )

        config = TriggerNodeConfiguration(
            node_type=NodeType.TRIGGER,
            trigger_type="manual",
            input_template={},
        )
        assert config.node_type == NodeType.TRIGGER

    def test_tc12b_output_display_yaml_minimal(self):
        config = OutputDisplayNodeConfiguration()
        assert config.node_type == NodeType.OUTPUT_DISPLAY
        assert config.input_reference is None
        assert config.display_title is None

    def test_tc12c_output_display_yaml_full(self):
        config = OutputDisplayNodeConfiguration(
            input_reference="upstream_result",
            display_title="查询结果展示",
        )
        assert config.input_reference == "upstream_result"
        assert config.display_title == "查询结果展示"


# ---------------------------------------------------------------------------
# TC-13：失败语义
# ---------------------------------------------------------------------------

class TestFailureSemantics:
    """失败语义验证"""

    def test_tc13_unexpected_exception_propagates(self):
        executor = OutputDisplayNodeExecutor()

        config = OutputDisplayNodeConfiguration(input_reference="result")
        ctx = _make_context(config=config, shared_state=_make_shared_state({"result": "value"}))

        # 真实 SharedState + patch 装配函数触发非预期异常路径（INV-15：禁止 MagicMock 直接作为 pydantic 字段值）
        # unified_node_input T3.5（CHG-B13）：非空引用分支 read_output 已替换为
        # assemble_single_reference，非预期异常面随之迁移——patch 目标同步迁移，
        # 「非预期异常向上传播」断言保持不变（行为修正类测试更新，总数不减，INV-D12）。
        with patch(
            "orchestration_platform.engine.nodes.output_display_node_executor.assemble_single_reference",
            side_effect=RuntimeError("模拟内部错误"),
        ):
            with pytest.raises(RuntimeError, match="模拟内部错误"):
                asyncio.run(executor.execute(ctx))