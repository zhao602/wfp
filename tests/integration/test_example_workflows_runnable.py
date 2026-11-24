# =============================================================================
# 范例工作流可运行性集成测试（tests/integration/test_example_workflows_runnable.py）
#
# 覆盖验收：A13 的自动化前置（全部预置范例均可解析与编译成功）
# 范围：12 个范例主定义（不含子定义 team_lead_subworkflow.yaml——
#   子定义经主定义 subworkflow 节点引用，运行期由定义库解析）
# =============================================================================

import pytest

from pathlib import Path

from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.domain.validation.graph_topology_validator import (
    GraphTopologyValidator,
)


EXAMPLES_ROOT = Path(__file__).resolve().parent.parent.parent / "examples"

# 全部注册范例主定义（与 example_library_service._EXAMPLE_REGISTRY 一一对应）
EXAMPLE_FILES = [
    "sequential_pipeline/sequential_pipeline.yaml",
    "supervisor_routing/supervisor_routing.yaml",
    "conditional_loop_pipeline/conditional_loop_pipeline.yaml",
    "feature_engineering/feature_engineering.yaml",
    "drift_detection/drift_detection.yaml",
    "parallel_execution/parallel_execution.yaml",
    "hierarchical_delegation/hierarchical_delegation.yaml",
    "reflection_iteration/reflection_iteration.yaml",
    "data_engineering/data_engineering_agent.yaml",
    "data_transform_pipeline/data_transform_pipeline.yaml",
    "subworkflow_nesting/subworkflow_nesting.yaml",
    "realtime_subscription/realtime_subscription_workflow.yaml",
]


class TestExampleWorkflowsRunnable:
    """全部预置范例可运行性测试。"""

    @pytest.fixture
    def parser(self):
        return YamlWorkflowParser()

    @pytest.fixture
    def compiler(self):
        return GraphCompiler()

    @pytest.mark.parametrize("example_file", EXAMPLE_FILES)
    def test_example_parses_successfully(self, parser, example_file):
        """每个范例 YAML 应解析成功。"""
        yaml_path = EXAMPLES_ROOT / example_file
        assert yaml_path.exists(), f"范例文件不存在: {yaml_path}"
        yaml_text = yaml_path.read_text(encoding="utf-8")
        definition = parser.parse(yaml_text)
        # spec_version 1.0 为基础口径，1.1 为数据科学范例（G4）引入的扩展口径
        assert definition.spec_version in ("1.0", "1.1")
        assert definition.nodes

    @pytest.mark.asyncio
    @pytest.mark.parametrize("example_file", EXAMPLE_FILES)
    async def test_example_compiles_successfully(self, parser, compiler, example_file):
        """每个范例应编译成功。"""
        yaml_path = EXAMPLES_ROOT / example_file
        yaml_text = yaml_path.read_text(encoding="utf-8")
        definition = parser.parse(yaml_text)
        compiled = await compiler.compile(definition)
        assert len(compiled.node_order) > 0

    @pytest.mark.parametrize("example_file", EXAMPLE_FILES)
    def test_example_topology_valid(self, parser, example_file):
        """每个范例拓扑校验应通过。"""
        yaml_path = EXAMPLES_ROOT / example_file
        yaml_text = yaml_path.read_text(encoding="utf-8")
        definition = parser.parse(yaml_text)

        validator = GraphTopologyValidator()
        issues = validator.validate(definition)
        errors = [i for i in issues if i.severity.value == "error"]
        assert len(errors) == 0, f"范例 {example_file} 拓扑校验有错误: {errors}"

    def test_examples_cover_core_node_types(self, parser):
        """全部范例合计应覆盖核心四类节点。

        fix_agent_null_length OP-02/03/04（P4）：范例库 tool 节点已全部移除
        （conditional_loop_pipeline 的 finalize 等 14 实例，纯删除），
        覆盖断言由五类（含 tool）调整为四类；tool 能力由
        merge_tool_into_agent_node 并入 Agent 节点演示。
        """
        all_node_types = set()
        for example_file in EXAMPLE_FILES:
            yaml_path = EXAMPLES_ROOT / example_file
            yaml_text = yaml_path.read_text(encoding="utf-8")
            definition = parser.parse(yaml_text)
            for node in definition.nodes:
                all_node_types.add(node.node_type)

        assert "trigger" in all_node_types
        assert "agent" in all_node_types
        assert "data_source" in all_node_types
        assert "control_flow" in all_node_types
