# =============================================================================
# vkb_write_mode 校验器单测（tests/unit/test_vkb_write_mode_validator.py）
#
# 覆盖 design §2.6.1 第⑨类：引用登记与模式化校验
# （FR-07/FR-08，AC-04/AC-05/AC-06/AC-15/AC-16/AC-18）。
# 驱动：同步（collect_input_references + NodeConfigurationValidator.validate）。
#
# 沙箱边界：本文件为交付物；运行命令见 tasks.md 附录 B。
# =============================================================================

from __future__ import annotations

from orchestration_platform.domain.errors import WF_VALIDATION_FAILED
from orchestration_platform.domain.models.enums import NodeType, Severity
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import (
    WorkflowDefinition,
)
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
    collect_input_references,
)


def _vkb_node(node_id: str, **overrides) -> NodeDefinition:
    config: dict = {"node_type": "vector_knowledge_base", "knowledge_base_id": "kb1"}
    config.update(overrides)
    return NodeDefinition(
        node_id=node_id,
        node_type=NodeType.VECTOR_KNOWLEDGE_BASE,
        output_name="report",
        configuration=config,
    )


def _trigger_node(node_id: str = "trig") -> NodeDefinition:
    return NodeDefinition(
        node_id=node_id,
        node_type=NodeType.TRIGGER,
        configuration={"trigger_type": "manual"},
    )


def _wf(nodes: list) -> WorkflowDefinition:
    return WorkflowDefinition(
        definition_id="wf_vkb_write_mode",
        name="vkb write mode",
        spec_version="1.0",
        nodes=list(nodes),
        edges=[],
    )


def _errors(issues: list) -> list:
    return [issue for issue in issues if issue.severity == Severity.ERROR]


class TestWriteReferenceRegistration:
    """引用登记（AC-15/AC-16/AC-18）：仅 write+reference+非空登记。"""

    def test_write_reference_registered_in_write_reference_mode(self) -> None:
        node = _vkb_node(
            "n1",
            mode="write",
            write_source_mode="reference",
            write_reference="up_x",
        )
        assert ("write_reference", "up_x") in collect_input_references(node)

    def test_retrieve_mode_registers_nothing_for_write(self) -> None:
        node = _vkb_node(
            "n1", write_source_mode="reference", write_reference="up_x"
        )
        assert collect_input_references(node) == []

    def test_write_manual_mode_registers_nothing(self) -> None:
        node = _vkb_node(
            "n1",
            mode="write",
            write_source_mode="manual",
            write_reference="up_x",
            write_text="t",
        )
        assert collect_input_references(node) == []

    def test_retrieve_query_reference_still_registered(self) -> None:
        node = _vkb_node("n1", query_mode="reference", query_reference="q_out")
        assert ("query_reference", "q_out") in collect_input_references(node)

    def test_blank_write_reference_not_registered(self) -> None:
        node = _vkb_node(
            "n1", mode="write", write_source_mode="reference", write_reference=""
        )
        assert collect_input_references(node) == []


class TestModeAwareValidation:
    """模式化加载期校验（AC-04/AC-05/AC-06）。"""

    def test_write_mode_without_query_is_ok(self) -> None:
        node = _vkb_node(
            "n1", mode="write", write_source_mode="manual", write_text="hello"
        )
        issues = _errors(NodeConfigurationValidator().validate(_wf([_trigger_node(), node])))
        assert issues == []

    def test_write_manual_blank_text_reports_error(self) -> None:
        node = _vkb_node(
            "n1", mode="write", write_source_mode="manual", write_text="   "
        )
        issues = _errors(NodeConfigurationValidator().validate(_wf([_trigger_node(), node])))
        assert len(issues) == 1
        assert issues[0].code == WF_VALIDATION_FAILED
        assert "write_text" in issues[0].message

    def test_write_reference_blank_reports_error(self) -> None:
        node = _vkb_node("n1", mode="write", write_source_mode="reference")
        issues = _errors(NodeConfigurationValidator().validate(_wf([_trigger_node(), node])))
        assert len(issues) == 1
        assert "write_reference" in issues[0].message

    def test_retrieve_mode_ignores_write_fields(self) -> None:
        node = _vkb_node("n1", query_mode="manual", query_value="q")
        issues = _errors(NodeConfigurationValidator().validate(_wf([_trigger_node(), node])))
        assert issues == []

    def test_knowledge_base_id_required_in_write_mode(self) -> None:
        node = _vkb_node(
            "n1",
            knowledge_base_id="   ",
            mode="write",
            write_source_mode="manual",
            write_text="t",
        )
        issues = _errors(NodeConfigurationValidator().validate(_wf([_trigger_node(), node])))
        assert any("knowledge_base_id" in issue.message for issue in issues)

    def test_retrieve_mode_query_still_required(self) -> None:
        # 检索模式既有校验逐字不变
        node = _vkb_node("n1", query_mode="manual", query_value="   ")
        issues = _errors(NodeConfigurationValidator().validate(_wf([_trigger_node(), node])))
        assert any("query_value" in issue.message for issue in issues)