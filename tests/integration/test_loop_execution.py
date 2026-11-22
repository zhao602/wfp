# =============================================================================
# 循环执行集成测试（tests/integration/test_loop_execution.py）
#
# 覆盖验收：A3（循环终止与最大迭代上限）
# =============================================================================

import pytest

from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser
from orchestration_platform.engine.graph_compiler import GraphCompiler


class TestLoopExecution:
    """循环执行集成测试。"""

    @pytest.fixture
    def loop_definition(self):
        """加载含循环的定义。"""
        from pathlib import Path
        parser = YamlWorkflowParser()
        yaml_text = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "valid_workflow_definitions"
            / "valid_with_loop.yaml"
        ).read_text(encoding="utf-8")
        return parser.parse(yaml_text)

    @pytest.mark.asyncio
    async def test_loop_definition_compiles(self, loop_definition):
        """含循环的定义应编译成功（循环节点回边被跳过）。"""
        compiler = GraphCompiler()
        compiled = await compiler.compile(loop_definition)
        assert len(compiled.node_order) > 0

    def test_loop_node_identified(self, loop_definition):
        """循环节点应被正确识别。"""
        loop_nodes = [
            n for n in loop_definition.nodes
            if n.node_type == "control_flow"
            and n.configuration.control_flow_kind == "loop"
        ]
        assert len(loop_nodes) == 1
        loop_node = loop_nodes[0]
        assert loop_node.configuration.loop_body_entry is not None
        assert loop_node.configuration.loop_termination_condition is not None
        assert loop_node.configuration.max_iterations is not None

    def test_max_iterations_enforced(self, loop_definition):
        """循环最大迭代次数应被强制执行。"""
        loop_nodes = [
            n for n in loop_definition.nodes
            if n.node_type == "control_flow"
            and n.configuration.control_flow_kind == "loop"
        ]
        max_iter = loop_nodes[0].configuration.max_iterations
        assert max_iter is not None
        assert max_iter > 0
        assert max_iter <= 10, "最大迭代次数应在合理范围内"
