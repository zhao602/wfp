# =============================================================================
# 输出展示扇出测试（2026-11-24）
#
# 背景：调度器确定性单游标——普通节点多条出边只走第一条，两个输出节点
# 并联时只有第一个执行（用户实测「两个输出节点只显示一个」）。
#
# 修复：出边全部指向「终端输出展示节点」时依次全量执行（_display_fanout_targets）；
# 其余多出边场景保持既有语义并由校验器给 WARNING 可辨识提示。
# =============================================================================

from typing import Any, Dict, List

import pytest

from orchestration_platform.domain.models.enums import Severity
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
)
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.output_display_node_executor import (
    OutputDisplayNodeExecutor,
)
from orchestration_platform.engine.nodes.trigger_node_executor import (
    TriggerNodeExecutor,
)
from orchestration_platform.engine.scheduler import Scheduler, NodeEventCallback
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser


DUAL_DISPLAY_YAML = """
spec_version: "1.0"
definition_id: dual_display
name: 双终端输出
nodes:
  - node_id: trigger_start
    node_type: trigger
    configuration:
      trigger_type: manual
  - node_id: display_a
    node_type: output_display
    configuration:
      input_reference:
        - trigger_start
      display_title: 输出A
  - node_id: display_b
    node_type: output_display
    configuration:
      input_reference:
        - trigger_start
      display_title: 输出B
edges:
  - {edge_id: e1, source_node_id: trigger_start, target_node_id: display_a}
  - {edge_id: e2, source_node_id: trigger_start, target_node_id: display_b}
inputs: []
outputs: []
"""

MIXED_OUTEDGE_YAML = """
spec_version: "1.0"
definition_id: mixed_outedge
name: 混合多出边
nodes:
  - node_id: trigger_start
    node_type: trigger
    configuration:
      trigger_type: manual
  - node_id: display_a
    node_type: output_display
    configuration:
      display_title: 输出A
  - node_id: display_mid
    node_type: output_display
    configuration:
      display_title: 中间展示
  - node_id: display_tail
    node_type: output_display
    configuration:
      display_title: 尾展示
edges:
  - {edge_id: e1, source_node_id: trigger_start, target_node_id: display_a}
  - {edge_id: e2, source_node_id: trigger_start, target_node_id: display_mid}
  - {edge_id: e3, source_node_id: display_mid, target_node_id: display_tail}
inputs: []
outputs: []
"""


class _RecordingEventCallback(NodeEventCallback):
    """记录 display 完成载荷的事件回调。"""

    def __init__(self) -> None:
        self.completed: List[Dict[str, Any]] = []

    async def on_node_started(self, instance_id, node_id, trace_id=""):
        return None

    async def on_node_completed(self, instance_id, node_id, trace_id="", payload=None):
        self.completed.append({"node_id": node_id, "payload": payload or {}})

    async def on_node_failed(self, instance_id, node_id, payload, trace_id=""):
        return None


async def _run_yaml(yaml_text: str, callback=None):
    definition = YamlWorkflowParser().parse(yaml_text)
    compiled = await GraphCompiler().compile(definition)
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())
    registry.register(OutputDisplayNodeExecutor())
    scheduler = Scheduler(registry, event_callback=callback)
    return await scheduler.execute(
        compiled,
        SharedState(instance_id="inst-fanout"),
        "inst-fanout",
        "trace-fanout",
    )


class TestDisplayFanout:
    """终端输出展示节点扇出：全部执行、全部发事件。"""

    @pytest.mark.asyncio
    async def test_dual_terminal_display_all_executed(self):
        callback = _RecordingEventCallback()
        await _run_yaml(DUAL_DISPLAY_YAML, callback)

        display_events = [
            c for c in callback.completed if "display_content" in c["payload"]
        ]
        assert [c["node_id"] for c in display_events] == [
            "display_a",
            "display_b",
        ]
        assert [c["payload"]["display_title"] for c in display_events] == [
            "输出A",
            "输出B",
        ]

    @pytest.mark.asyncio
    async def test_mixed_outedge_keeps_single_cursor_semantics(self):
        """混合多出边：仍走第一条（display_a → 其无出边 → 终态），不扇出。"""
        callback = _RecordingEventCallback()
        await _run_yaml(MIXED_OUTEDGE_YAML, callback)

        completed_ids = [c["node_id"] for c in callback.completed]
        assert "display_a" in completed_ids
        assert "display_mid" not in completed_ids
        assert "display_tail" not in completed_ids

    def test_fanout_targets_dual_terminal_display(self):
        scheduler = Scheduler(NodeExecutorRegistry())
        import asyncio

        definition = YamlWorkflowParser().parse(DUAL_DISPLAY_YAML)
        compiled = asyncio.run(GraphCompiler().compile(definition))
        # trigger 双终端出边 → 扇出两目标（保持声明顺序）
        targets = scheduler._display_fanout_targets(
            compiled, compiled.get_node("trigger_start")
        )
        assert targets == ["display_a", "display_b"]
        # display_a 无出边（出边数 < 2）→ None，走既有单游标语义
        assert scheduler._display_fanout_targets(
            compiled, compiled.get_node("display_a")
        ) is None


class TestMultiOutedgeValidation:
    """多出边可辨识校验：终端全展示豁免，其余 WARNING（不阻断）。"""

    def test_dual_terminal_display_no_warning(self):
        definition = YamlWorkflowParser().parse(DUAL_DISPLAY_YAML)
        issues = NodeConfigurationValidator().validate(definition)
        assert issues == []

    def test_mixed_outedge_warns_skipped_targets(self):
        definition = YamlWorkflowParser().parse(MIXED_OUTEDGE_YAML)
        issues = NodeConfigurationValidator().validate(definition)
        warnings = [i for i in issues if i.severity == Severity.WARNING]
        assert len(warnings) == 1
        assert warnings[0].node_id == "trigger_start"
        assert "只执行第一条" in warnings[0].message
        assert "display_mid" in warnings[0].message
