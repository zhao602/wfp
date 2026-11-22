# =============================================================================
# 注册范例真实文件后端加载定稿测试（tests/integration/test_registered_example_fixtures.py）
# fix_agent_null_length CHG-22 / tasks 5.4（E2-d 定稿 + R9/PZ-01 判定留痕）：
#   1. 12 个注册范例（含 conditional_loop_pipeline / feature_engineering /
#      drift_detection 三个经 P4 tool 节点移除的文件，及 T16.5 补齐登记的
#      7 个新范例）经真实加载链 YamlWorkflowParser（语法 → 版本 → 五要素
#      结构校验 → pydantic 判别联合）全部解析通过；
#   2. 移除后零 tool 节点（AC-08）；三个移除文件的 outputs 为空列表
#      （悬空声明条目删除、顶层键保留——结构校验器 REQUIRED_TOP_LEVEL_FIELDS
#      要求 outputs 键存在，definition_structure_validator.py:25）；
#   3. drift_detection 的 default_branch_target（R9 → T16.5 修正留痕）：
#      schema Optional 可缺省（node_configurations.py:214，加载期无引用完整性
#      强校验，PZ-01 未触发）；范例文件现为"基线未建立"路径显式声明默认分支
#      （无匹配且无默认分支会报错终止，见下方专项断言）；
#   4. E2-d 定稿（R7 事实记录口径）：AgentNodeConfiguration.available_tools=None
#      → pydantic ValidationError（候选 B「当前被拦截」，仅事实记录不作豁免依据）；
#      键缺失 → default_factory=list 归一 []。
# 执行：pytest tests/integration/test_registered_example_fixtures.py -v
# =============================================================================

from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    AgentNodeConfiguration,
    ControlFlowNodeConfiguration,
)
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

# 范例根目录：本文件位于 tests/integration/ → tests → 项目根 → examples/
EXAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples"

# 12 个注册范例（按 example_library_service._EXAMPLE_REGISTRY 声明顺序）
REGISTERED_EXAMPLES = [
    ("sequential_pipeline", "sequential_pipeline.yaml"),
    ("supervisor_routing", "supervisor_routing.yaml"),
    ("conditional_loop_pipeline", "conditional_loop_pipeline.yaml"),
    ("feature_engineering", "feature_engineering.yaml"),
    ("drift_detection", "drift_detection.yaml"),
    ("parallel_execution", "parallel_execution.yaml"),
    ("hierarchical_delegation", "hierarchical_delegation.yaml"),
    ("reflection_iteration", "reflection_iteration.yaml"),
    ("data_engineering", "data_engineering_agent.yaml"),
    ("data_transform_pipeline", "data_transform_pipeline.yaml"),
    ("subworkflow_nesting", "subworkflow_nesting.yaml"),
    ("realtime_subscription", "realtime_subscription_workflow.yaml"),
]

# P4 §5.1 移除过 tool 节点的注册文件
TOOL_REMOVED_REGISTERED = {"conditional_loop_pipeline", "feature_engineering", "drift_detection"}


def _read_example(example_dir: str, yaml_name: str) -> str:
    return (EXAMPLES_ROOT / example_dir / yaml_name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "example_dir,yaml_name",
    REGISTERED_EXAMPLES,
    ids=[entry[0] for entry in REGISTERED_EXAMPLES],
)
def test_registered_example_parses_via_backend_loader(example_dir: str, yaml_name: str) -> None:
    """注册范例经真实后端加载链（结构校验 + pydantic）解析通过，移除后零 tool 节点。"""
    parser = YamlWorkflowParser()
    definition = parser.parse(_read_example(example_dir, yaml_name))

    assert definition is not None
    assert len(definition.nodes) > 0
    # AC-08：移除后零 tool 节点（sequential_pipeline / supervisor_routing 本就无 tool）
    assert all(node.node_type != NodeType.TOOL for node in definition.nodes)
    # 全部边引用闭合（后端模型构造成功即隐式通过；此处显式断言锚定）
    node_ids = {node.node_id for node in definition.nodes}
    for edge in definition.edges:
        assert edge.source_node_id in node_ids
        assert edge.target_node_id in node_ids
    if example_dir in TOOL_REMOVED_REGISTERED:
        # 悬空输出声明条目删除，顶层 outputs 键保留为空列表（结构校验器必备要素）
        assert definition.outputs == []


def test_drift_detection_default_branch_target_restored_for_missing_baseline() -> None:
    """default_branch_target 语义留痕（R9 → T16.5 修正）。

    - R9 裁决事实（不变）：字段 schema Optional 可缺省，缺省加载不报错
      （加载期无引用完整性强校验，PZ-01 未触发）；
    - T16.5 修正：范例文件显式声明 default_branch_target=baseline_missing_agent。
      条件分支"无匹配且无默认分支"会直接报错终止，基线缺失场景必须由
      默认分支承接"不误报"结论出口——缺省 None 反而是逻辑错误。
    """
    parser = YamlWorkflowParser()
    definition = parser.parse(_read_example("drift_detection", "drift_detection.yaml"))

    baseline_check = next(node for node in definition.nodes if node.node_id == "baseline_check")
    assert baseline_check.node_type == NodeType.CONTROL_FLOW
    configuration = baseline_check.configuration
    assert isinstance(configuration, ControlFlowNodeConfiguration)
    # 显式默认分支承接"基线未建立"路径（T16.5 修正后的正确语义）
    assert configuration.default_branch_target == "baseline_missing_agent"
    # 默认分支目标与全部条件分支目标均指向存在节点
    node_ids = {node.node_id for node in definition.nodes}
    assert configuration.default_branch_target in node_ids
    assert len(configuration.conditions) > 0
    for condition in configuration.conditions:
        assert condition.target_node_id in node_ids


def test_agent_available_tools_null_rejected_by_pydantic() -> None:
    """E2-d 定稿（R7 事实记录）：available_tools=None → ValidationError（候选 B 当前被拦截）。"""
    with pytest.raises(ValidationError):
        AgentNodeConfiguration(
            model_identifier="m-1",
            system_prompt="p",
            available_tools=None,
        )


def test_agent_available_tools_missing_defaults_to_empty_list() -> None:
    """E2-d 定稿：available_tools 键缺失 → default_factory=list 归一 []。"""
    configuration = AgentNodeConfiguration(model_identifier="m-1", system_prompt="p")
    assert configuration.available_tools == []
