# =============================================================================
# Agent 配置空值契约实证（tests/unit/test_agent_config_null_contract.py）
#
# fix_agent_null_length CHG-22 / DC-02 候选 B（E2-d 实验固化）：
#   1. pydantic 对 available_tools=null 的拦截行为（ValidationError → 422 形态）；
#   2. available_tools 键缺失时 default_factory=list 归一为 []；
#   3. 候选 B 结论仅作「当前被拦截」的事实记录，不作「永不发生」豁免依据（R7 口径）；
#   4. R9 回退点前置探测：default_branch_target / loop_body_entry / conditions
#      缺省时 ControlFlowNodeConfiguration 校验通过（悬空引用连带清理的 schema 依据）。
#
# 执行：PYTHONPATH=src python -m pytest tests/unit/test_agent_config_null_contract.py -v
# =============================================================================

import pytest
from pydantic import ValidationError

from orchestration_platform.domain.models.node_configurations import (
    AgentNodeConfiguration,
    ControlFlowNodeConfiguration,
)


class TestAgentAvailableToolsNullContract:
    """候选 B（范例库 API 链 pydantic 拦截）行为实证。"""

    def test_available_tools_null_rejected(self) -> None:
        """available_tools=null → ValidationError（非 Optional，List[str] 拦截）。"""
        with pytest.raises(ValidationError):
            AgentNodeConfiguration(
                node_type="agent",
                model_identifier="m-1",
                available_tools=None,
            )

    def test_available_tools_missing_defaults_to_empty_list(self) -> None:
        """键缺失 → default_factory=list 归一为 []（E-03 范例缺键形态经 API 链不崩的依据）。"""
        config = AgentNodeConfiguration(node_type="agent", model_identifier="m-1")
        assert config.available_tools == []

    def test_available_tools_dedupe_preserves_order(self) -> None:
        """field_validator 去重保序（node_configurations.py:82-96 既有行为回归锚点）。"""
        config = AgentNodeConfiguration(
            node_type="agent",
            model_identifier="m-1",
            available_tools=["a", "a", "b"],
        )
        assert config.available_tools == ["a", "b"]


class TestControlFlowOptionalFieldsContract:
    """R9 回退点前置探测：悬空引用连带清理所依赖的 Optional 字段缺省合法性。"""

    def test_default_branch_target_missing_ok(self) -> None:
        """default_branch_target 缺省 → 校验通过（Optional[str]，drift_detection 字段删除依据）。"""
        config = ControlFlowNodeConfiguration(control_flow_kind="conditional_branch")
        assert config.default_branch_target is None
        assert config.conditions == []

    def test_loop_body_entry_missing_ok(self) -> None:
        """loop_body_entry 缺省 → 校验通过（Optional[str]）。"""
        config = ControlFlowNodeConfiguration(control_flow_kind="loop")
        assert config.loop_body_entry is None

    def test_conditions_missing_defaults_to_empty_list(self) -> None:
        """conditions 缺省 → default_factory=list（conditional_loop_pipeline 条目删除依据）。"""
        config = ControlFlowNodeConfiguration(control_flow_kind="conditional_branch")
        assert config.conditions == []
