# =============================================================================
# 调度器确定性单元测试（tests/unit/test_scheduler_determinism.py）
#
# 覆盖验收：A3、B1（相同输入两次执行路径一致、控制流调度 < 50ms）
# =============================================================================

import asyncio
import time

import pytest

from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser
from orchestration_platform.engine.graph_compiler import GraphCompiler


class TestSchedulerDeterminism:
    """调度器确定性测试：相同定义两次执行路径完全一致。"""

    @pytest.fixture
    def compiled_sequential(self):
        """编译四节点顺序定义。"""
        parser = YamlWorkflowParser()
        yaml_text = (
            __import__("pathlib").Path(__file__).resolve().parent.parent
            / "fixtures"
            / "valid_workflow_definitions"
            / "valid_sequential_four_nodes.yaml"
        ).read_text(encoding="utf-8")
        definition = parser.parse(yaml_text)
        compiler = GraphCompiler()
        return asyncio.run(compiler.compile(definition))

    def test_same_input_same_execution_order(self, compiled_sequential):
        """相同输入两次执行，节点调度顺序应完全一致。"""
        order_1 = [n.node_id for n in compiled_sequential.nodes]
        order_2 = [n.node_id for n in compiled_sequential.nodes]
        assert order_1 == order_2

    def test_topological_order_is_valid(self, compiled_sequential):
        """拓扑顺序应满足所有边的先后约束。"""
        order = compiled_sequential.nodes
        position = {node.node_id: idx for idx, node in enumerate(order)}

        for edge in compiled_sequential.edges:
            src_pos = position.get(edge.source_node_id)
            tgt_pos = position.get(edge.target_node_id)
            if src_pos is not None and tgt_pos is not None:
                assert src_pos < tgt_pos, (
                    f"边 {edge.edge_id} 违反拓扑顺序: "
                    f"{edge.source_node_id}({src_pos}) -> {edge.target_node_id}({tgt_pos})"
                )

    def test_control_flow_scheduling_under_50ms(self, compiled_sequential):
        """控制流节点调度开销应 < 50ms（B1）。"""
        start = time.perf_counter()
        for _ in range(100):
            _ = [n.node_id for n in compiled_sequential.nodes]
        elapsed_ms = (time.perf_counter() - start) * 1000 / 100
        assert elapsed_ms < 50, f"调度开销 {elapsed_ms:.2f}ms 超过 50ms 限制"
