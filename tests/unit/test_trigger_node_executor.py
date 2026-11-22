# =============================================================================
# 触发器节点执行器单元测试（tests/unit/test_trigger_node_executor.py）
#
# 覆盖 trigger_input_template_resolution 批次 2（任务 2.1 / CHG-08）：
#   T-01 空模板     —— 输出入口标记（trigger_type），success=True
#   T-02 非空模板+全命中 —— 输出经模板收敛的映射结果
#   T-03 非空模板+字段缺失 —— 明确失败并返回专属错误码
#
# 设计依据：spec FR-02 / FR-03，design DC-01；红线——不得直接 import langgraph。
#
# 沙箱限制：本机沙箱 asyncio 事件循环不可用（WinError 10051 / socket pair）。
#   TriggerNodeExecutor.execute 为纯同步逻辑（内部无真实 await 挂起点），
#   故用手动驱动协程（coro.send(None)）逐条驱动，环境无关。
# =============================================================================

from orchestration_platform.domain.errors import TRIGGER_PAYLOAD_MAPPING_FAILED
from orchestration_platform.domain.models.enums import NodeType, TriggerType
from orchestration_platform.domain.models.node_configurations import (
    TriggerNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes.base import NodeExecutionContext
from orchestration_platform.engine.nodes.trigger_node_executor import (
    TriggerNodeExecutor,
)


def _run(coro):
    """手动驱动协程至完成（沙箱事件循环不可用时的 fallback）。

    触发器执行器内部无真实 await 挂起点，send(None) 一次即驱动至
    StopIteration 并返回结果；若出现挂起点则说明实现意外引入异步等待。
    """
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value
    else:
        coro.close()
        raise RuntimeError("触发器执行器不应存在挂起点")


def _make_context(input_template, node_outputs=None):
    """构造触发器执行上下文（触发器为唯一入口，输入来自实例入参注入的上下文）。"""
    node = NodeDefinition(
        node_id="trigger_1",
        node_type=NodeType.TRIGGER,
        configuration=TriggerNodeConfiguration(
            trigger_type=TriggerType.MANUAL,
            input_template=input_template,
        ),
        output_name="trigger_out",
    )
    return NodeExecutionContext(
        node_definition=node,
        shared_state=SharedState(
            instance_id="inst-test",
            node_outputs=node_outputs or {},
        ),
        instance_id="inst-test",
        trace_id="trace-test",
    )


class TestTriggerNodeExecutor:
    """触发器执行器三态行为验证。"""

    def setup_method(self):
        self.executor = TriggerNodeExecutor()

    def test_t01_empty_template_returns_entry_marker(self):
        """T-01 空模板：成功并返回入口标记 output_value。"""
        result = _run(self.executor.execute(_make_context({})))

        assert result.success is True
        assert result.output_value == {"trigger_type": TriggerType.MANUAL.value}
        assert result.output_name == "trigger_out"
        assert result.error_code is None

    def test_t02_template_all_hit_maps_context(self):
        """T-02 非空模板 + 字段全命中：输出经模板收敛的映射结果。"""
        context = _make_context(
            {"topic": "topic", "score": "target_score"},
            {"topic": "demo", "target_score": 0.8},
        )

        result = _run(self.executor.execute(context))

        assert result.success is True
        assert result.output_value == {"topic": "demo", "score": 0.8}
        assert result.output_name == "trigger_out"
        assert result.error_code is None

    def test_t03_template_field_missing_fails(self):
        """T-03 非空模板 + 引用字段缺失：明确失败 + 专属错误码。"""
        context = _make_context({"topic": "topic"}, {})

        result = _run(self.executor.execute(context))

        assert result.success is False
        assert result.error_code == TRIGGER_PAYLOAD_MAPPING_FAILED
        assert "topic" in (result.error_message or "")