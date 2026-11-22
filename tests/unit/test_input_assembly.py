# =============================================================================
# 引擎输入装配服务单元测试（tests/unit/test_input_assembly.py）
#
# 覆盖验收：unified_node_input T2.4（CHG-T02）
# 覆盖不变式：INV-D01（纯函数）、INV-D02（同快照）、INV-13/INV-D04（错误码集中与覆盖）
# 追加覆盖：optimize_output_display_node CHG-14（assemble_reference_collection 语义：
#           保序 / 逐条容错 / fail-fast 分支 / 键取引用原文）
# =============================================================================

from typing import Any, Dict, List
from unittest.mock import MagicMock

from orchestration_platform.domain.errors import (
    NODE_INPUT_REFERENCE_MISSING,
    NODE_INPUT_TYPE_MISMATCH,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    AgentInputEntry,
    AgentNodeConfiguration,
    ToolNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.input_assembly import (
    AssemblyFailure,
    AssemblySuccess,
    assemble_agent_inputs,
    assemble_keyed_mapping,
    assemble_params,
    assemble_reference_collection,
    assemble_single_reference,
    assemble_user_message,
    resolve_reference,
)
from orchestration_platform.engine.nodes.agent_node_executor import AgentNodeExecutor
from orchestration_platform.engine.nodes.base import NodeExecutionContext


def _make_snapshot() -> Dict[str, Any]:
    """构造覆盖四态与嵌套结构的共享状态快照样本。"""
    return {
        "topic": "数据质量分析",
        "nullable_field": None,
        "empty_rows": [],
        "rows": [{"id": 1}, {"id": 2}],
        "agent_out": {
            "summary": "分析完成",
            "score": 92,
            "nested": {"total": 7},
        },
    }


class TestResolveReferenceFourStates:
    """四态区分：键缺失 fail / 值为 null 正常传递 / 空集合正常传递 / 正常值。"""

    def test_key_missing_returns_reference_missing_failure(self):
        outcome = resolve_reference("nonexistent_output", _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "nonexistent_output" in outcome.error_message

    def test_key_present_with_null_value_returns_success(self):
        outcome = resolve_reference("nullable_field", _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value is None

    def test_key_present_with_empty_list_returns_success(self):
        outcome = resolve_reference("empty_rows", _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value == []

    def test_normal_value_returns_success(self):
        outcome = resolve_reference("topic", _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value == "数据质量分析"


class TestResolveReferencePath:
    """字段路径逐段取值（AC-07 场景 2）。"""

    def test_single_segment_path(self):
        outcome = resolve_reference("agent_out.summary", _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value == "分析完成"

    def test_two_segment_nested_path(self):
        outcome = resolve_reference("agent_out.nested.total", _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value == 7

    def test_path_into_scalar_returns_type_mismatch(self):
        outcome = resolve_reference("topic.summary", _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH
        assert "object" in outcome.error_message
        assert "scalar" in outcome.error_message

    def test_path_segment_missing_in_dict_returns_type_mismatch(self):
        outcome = resolve_reference("agent_out.absent_field", _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH

    def test_path_through_null_value_returns_type_mismatch(self):
        snapshot = {"nullable_field": None}
        outcome = resolve_reference("nullable_field.child", snapshot)

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH

    def test_syntax_invalid_reference_returns_failure(self):
        outcome = resolve_reference("Invalid-Name", _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "Invalid-Name" in outcome.error_message

    def test_syntax_invalid_empty_reference_returns_failure(self):
        outcome = resolve_reference("   ", _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING


class TestAssembleKeyedMapping:
    """键值映射形态装配（trigger/tool/subworkflow）。"""

    def test_multi_entry_independent_assembly(self):
        mapping = {"a": "topic", "b": "rows", "c": "topic"}
        outcome = assemble_keyed_mapping(mapping, _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {
            "a": "数据质量分析",
            "b": [{"id": 1}, {"id": 2}],
            "c": "数据质量分析",
        }

    def test_null_value_entry_passthrough(self):
        outcome = assemble_keyed_mapping({"k": "nullable_field"}, _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {"k": None}

    def test_missing_reference_failure_lists_all_missing_fields(self):
        mapping = {"first": "gone_output", "second": "also_gone"}
        outcome = assemble_keyed_mapping(mapping, _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "缺失字段" in outcome.error_message
        assert "gone_output" in outcome.error_message
        assert "also_gone" in outcome.error_message

    def test_error_code_override_passthrough(self):
        outcome = assemble_keyed_mapping(
            {"p": "gone_output"},
            _make_snapshot(),
            error_code="CUSTOM_LEGACY_CODE",
        )

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == "CUSTOM_LEGACY_CODE"
        assert "gone_output" in outcome.error_message

    def test_path_type_mismatch_keeps_unified_code(self):
        outcome = assemble_keyed_mapping(
            {"k": "topic.child"},
            _make_snapshot(),
            error_code="CUSTOM_LEGACY_CODE",
        )

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH

    def test_empty_mapping_returns_empty_success(self):
        outcome = assemble_keyed_mapping({}, _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {}

    def test_syntax_invalid_reference_collected_as_missing(self):
        outcome = assemble_keyed_mapping({"k": "Not Valid"}, _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "Not Valid" in outcome.error_message


class TestAssembleAgentInputs:
    """Agent 选择集形态装配（别名组织 + fail-fast）。"""

    def test_alias_organization_with_fallback(self):
        entries = [
            AgentInputEntry(reference="agent_out.summary", alias="摘要"),
            AgentInputEntry(reference="topic"),
            AgentInputEntry(reference="agent_out.score", alias=""),
        ]
        outcome = assemble_agent_inputs(entries, _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {
            "摘要": "分析完成",
            "topic": "数据质量分析",
            "agent_out": 92,
        }

    def test_any_entry_missing_fails_fast_with_reference(self):
        entries = [
            AgentInputEntry(reference="topic"),
            AgentInputEntry(reference="gone_output", alias="缺失项"),
        ]
        outcome = assemble_agent_inputs(entries, _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "gone_output" in outcome.error_message

    def test_type_mismatch_entry_fails_with_unified_code(self):
        entries = [AgentInputEntry(reference="topic.child")]
        outcome = assemble_agent_inputs(entries, _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH

    def test_null_value_entry_passthrough(self):
        entries = [AgentInputEntry(reference="nullable_field")]
        outcome = assemble_agent_inputs(entries, _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {"nullable_field": None}

    def test_empty_entries_returns_empty_success(self):
        outcome = assemble_agent_inputs([], _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {}


class TestAssembleSingleReference:
    """单一引用形态装配（data_transform / output_display）。"""

    def test_none_reference_returns_success_none(self):
        outcome = assemble_single_reference(None, _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value is None

    def test_default_missing_fails_with_unified_code(self):
        outcome = assemble_single_reference("gone_output", _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING

    def test_missing_is_none_tolerates_missing_reference(self):
        outcome = assemble_single_reference(
            "gone_output", _make_snapshot(), missing_is_none=True
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value is None

    def test_missing_is_none_tolerates_type_mismatch(self):
        outcome = assemble_single_reference(
            "topic.child", _make_snapshot(), missing_is_none=True
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value is None

    def test_normal_value_passthrough(self):
        outcome = assemble_single_reference("topic", _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value == "数据质量分析"

    def test_null_value_passthrough(self):
        outcome = assemble_single_reference("nullable_field", _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value is None

    def test_expected_kind_dataset_accepts_list(self):
        outcome = assemble_single_reference(
            "rows", _make_snapshot(), expected_kind="dataset"
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value == [{"id": 1}, {"id": 2}]

    def test_expected_kind_dataset_accepts_empty_list(self):
        outcome = assemble_single_reference(
            "empty_rows", _make_snapshot(), expected_kind="dataset"
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value == []

    def test_expected_kind_dataset_rejects_scalar(self):
        outcome = assemble_single_reference(
            "topic", _make_snapshot(), expected_kind="dataset"
        )

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH
        assert "dataset" in outcome.error_message
        assert "scalar" in outcome.error_message

    def test_expected_kind_dataset_rejects_null_value(self):
        outcome = assemble_single_reference(
            "nullable_field", _make_snapshot(), expected_kind="dataset"
        )

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH


class TestPurityAndSnapshot:
    """纯函数性质与同快照一致性（INV-D01 / INV-D02）。"""

    def test_same_input_returns_equal_result(self):
        snapshot = _make_snapshot()
        first = resolve_reference("agent_out.nested.total", snapshot)
        second = resolve_reference("agent_out.nested.total", snapshot)

        assert isinstance(first, AssemblySuccess)
        assert isinstance(second, AssemblySuccess)
        assert first == second

    def test_keyed_mapping_does_not_mutate_snapshot(self):
        snapshot = _make_snapshot()
        before = dict(snapshot)
        assemble_keyed_mapping({"a": "topic", "b": "agent_out.score"}, snapshot)

        assert snapshot == before

    def test_resolve_reference_does_not_mutate_snapshot(self):
        snapshot = _make_snapshot()
        before = dict(snapshot)
        resolve_reference("agent_out.nested.total", snapshot)

        assert snapshot == before
        assert snapshot["agent_out"]["nested"] == {"total": 7}

    def test_multiple_references_resolved_against_same_snapshot(self):
        """同一节点多条引用基于同一快照对象解析（FR-07 / INV-D02）。"""
        snapshot = _make_snapshot()
        outcome = assemble_keyed_mapping(
            {"a": "topic", "b": "rows", "c": "agent_out.summary"}, snapshot
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values["a"] == snapshot["topic"]
        assert outcome.values["b"] == snapshot["rows"]
        assert outcome.values["c"] == snapshot["agent_out"]["summary"]


class TestAssembleReferenceCollection:
    """多引用集合装配语义（optimize_output_display_node CHG-14 / DC-06 / DC-07）。

    键取引用表达式原文（含字段路径形态）、保序；
    missing_is_none=True 逐条容错（None 占位并继续）；
    missing_is_none=False 首条失败即整体失败（透传该条 error_code）。
    """

    def test_ordered_mapping_with_reference_keys(self):
        snapshot = _make_snapshot()
        outcome = assemble_reference_collection(["rows", "topic"], snapshot)

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {"rows": snapshot["rows"], "topic": snapshot["topic"]}
        assert list(outcome.values.keys()) == ["rows", "topic"]

    def test_field_path_reference_as_key(self):
        snapshot = _make_snapshot()
        outcome = assemble_reference_collection(["agent_out.score"], snapshot)

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {"agent_out.score": 92}

    def test_null_value_reference_passes_through(self):
        snapshot = _make_snapshot()
        outcome = assemble_reference_collection(["nullable_field"], snapshot)

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {"nullable_field": None}

    def test_missing_is_none_tolerates_all_failure_kinds(self):
        snapshot = _make_snapshot()
        references: List[str] = [
            "rows",
            "missing_output",
            "agent_out.score.deeper",
            "!!!illegal syntax",
            "topic",
        ]
        outcome = assemble_reference_collection(
            references, snapshot, missing_is_none=True
        )

        assert isinstance(outcome, AssemblySuccess)
        assert list(outcome.values.keys()) == references
        assert outcome.values["rows"] == snapshot["rows"]
        assert outcome.values["missing_output"] is None
        assert outcome.values["agent_out.score.deeper"] is None
        assert outcome.values["!!!illegal syntax"] is None
        assert outcome.values["topic"] == snapshot["topic"]

    def test_fail_fast_without_missing_is_none(self):
        snapshot = _make_snapshot()
        outcome = assemble_reference_collection(
            ["rows", "missing_output", "topic"], snapshot
        )

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING

    def test_fail_fast_passthrough_type_mismatch(self):
        snapshot = _make_snapshot()
        outcome = assemble_reference_collection(
            ["rows", "agent_out.score.deeper"], snapshot
        )

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH

    def test_empty_collection_returns_empty_success(self):
        # 防御语义：调用方保证非空（executor 三分支先行判断），空入参返回空集成功
        outcome = assemble_reference_collection([], _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {}

    def test_does_not_mutate_inputs(self):
        snapshot = _make_snapshot()
        snapshot_before = dict(snapshot)
        references = ["rows", "missing_output", "topic"]
        references_before = list(references)

        assemble_reference_collection(references, snapshot, missing_is_none=True)

        assert snapshot == snapshot_before
        assert references == references_before


# =============================================================================
# assemble_user_message（llm_and_code_nodes 任务 5.1：CHG-09 / DC-07 / FR-07~FR-09 /
# FR-43 / AC-10~AC-12 / AC-46 / INV-10）——追加不改既有函数
# =============================================================================


class TestAssembleUserMessage:
    """三态上下文 + 固定顺序「上下文 + \\n + 提示词」＋占位符原样＋失败透传。"""

    def test_none_inputs_flattens_all_then_prompt(self):
        """inputs=None → 全量平铺 + 提示词（顺序固定）。"""
        assert assemble_user_message(None, {"a": 1, "b": "x"}, "请总结") == (
            "a: 1\nb: x\n请总结"
        )

    def test_none_inputs_empty_snapshot_prompt_only(self):
        """空快照 + 提示词 → 仅提示词（不产生前空行）。"""
        assert assemble_user_message(None, {}, "P") == "P"

    def test_empty_list_inputs_prompt_only(self):
        """inputs=[] → 显式空清单：上下文为空。"""
        assert assemble_user_message([], {"a": 1}, "P") == "P"

    def test_empty_list_inputs_and_empty_prompt(self):
        """两者皆空 → 空串。"""
        assert assemble_user_message([], {"a": 1}, "") == ""

    def test_selected_inputs_alias_and_exclusion(self):
        """别名生效（dataset 别名 data → data: …）；清单外输出不进入（FR-09）。"""
        entries = [AgentInputEntry(reference="rows", alias="data")]
        snapshot = {"rows": [1, 2], "excluded": "no"}
        assert assemble_user_message(entries, snapshot, "P") == "data: [1, 2]\nP"

    def test_selected_inputs_default_name(self):
        """无别名 → 按命名输出名平铺。"""
        entries = [AgentInputEntry(reference="topic")]
        assert assemble_user_message(entries, {"topic": "T"}, "P") == "topic: T\nP"

    def test_selected_inputs_keep_entry_order(self):
        """多条目按清单顺序平铺（不按快照键序）。"""
        entries = [AgentInputEntry(reference="b"), AgentInputEntry(reference="a")]
        snapshot = {"a": 1, "b": 2}
        assert assemble_user_message(entries, snapshot, "P") == "b: 2\na: 1\nP"

    def test_placeholder_braces_preserved(self):
        """{{...}} 一律按普通文本原样保留，无替换逻辑（AC-12）。"""
        outcome = assemble_user_message([], {}, "引用 {{dataset}} 原样")
        assert outcome == "引用 {{dataset}} 原样"

    def test_context_only_no_trailing_newline(self):
        """提示词为空 → 仅上下文文本，无尾部换行。"""
        assert assemble_user_message(None, {"a": 1}, "") == "a: 1"

    def test_missing_reference_failure_passthrough(self):
        """缺失引用 → AssemblyFailure 透传（fail-fast，错误码不被覆盖）。"""
        entries = [AgentInputEntry(reference="missing_out")]
        outcome = assemble_user_message(entries, {}, "P")
        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "missing_out" in outcome.error_message

    def test_type_mismatch_failure_passthrough(self):
        """字段路径类型不匹配 → AssemblyFailure 透传（统一码）。"""
        entries = [AgentInputEntry(reference="topic.missing.deep")]
        outcome = assemble_user_message(entries, {"topic": "T"}, "P")
        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH


class TestAssembleUserMessageAgentParityAc46:
    """AC-46 对照断言：同一 inputs 在 agent 与 llm 侧装配结果逐字符一致。"""

    def _agent_message(self, config_inputs, outputs):
        """以 agent 节点执行器构建同参用户消息（上下文部分）。"""
        config = AgentNodeConfiguration(model_identifier="m", inputs=config_inputs)
        node_def = NodeDefinition(
            node_id="n",
            name="",
            node_type=NodeType.AGENT,
            configuration=config,
            output_name=None,
        )
        shared = SharedState(instance_id="i")
        shared.node_outputs.update(outputs)
        context = NodeExecutionContext(
            node_definition=node_def,
            shared_state=shared,
            instance_id="i",
            trace_id="t",
        )
        executor = AgentNodeExecutor(llm_client=MagicMock())
        return executor._build_user_message(context)

    def test_three_state_parity_with_agent(self):
        """三态（None / [] / 非空）上下文文本与 agent 逐字符一致。"""
        cases = [
            (None, {"a": 1, "b": "x"}),
            (None, {}),
            ([], {"a": 1}),
            ([AgentInputEntry(reference="a")], {"a": 1, "b": 2}),
            ([AgentInputEntry(reference="a", alias="首")], {"a": [1, 2]}),
        ]
        for inputs, outputs in cases:
            agent_text = self._agent_message(inputs, outputs)
            llm_text = assemble_user_message(inputs, dict(outputs), "")
            assert llm_text == agent_text, f"不一致：inputs={inputs!r} outputs={outputs!r}"

    def test_failure_parity_with_agent(self):
        """装配失败时错误码与错误消息与 agent 完全一致（fail-fast 同源）。"""
        inputs = [AgentInputEntry(reference="missing_out")]
        agent_outcome = self._agent_message(inputs, {})
        llm_outcome = assemble_user_message(inputs, {}, "")
        assert isinstance(agent_outcome, AssemblyFailure)
        assert isinstance(llm_outcome, AssemblyFailure)
        assert llm_outcome.error_code == agent_outcome.error_code
        assert llm_outcome.error_message == agent_outcome.error_message


# =============================================================================
# assemble_params（llm_code_nodes_v2 任务 3.1：CHG-06 / DC-04-7 / OP-03 /
# FR-14 / AC-15 / ZD-04 / G-15）——追加不改既有函数
# =============================================================================


class TestAssembleParams:
    """params 装配三态：None=全量平铺 / []=空 dict / 非空=选择集（alias 优先）。"""

    def test_none_inputs_flattens_all_outputs(self):
        """inputs=None → 全量平铺（键 = 命名输出名，值原样透传）。"""
        snapshot = _make_snapshot()
        outcome = assemble_params(None, snapshot)

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == snapshot

    def test_none_inputs_returns_new_dict_with_shared_values(self):
        """全量平铺为浅拷贝新 dict（脚本内重赋值不污染快照），值不深拷贝。"""
        snapshot = _make_snapshot()
        outcome = assemble_params(None, snapshot)

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values is not snapshot
        assert outcome.values["agent_out"] is snapshot["agent_out"]
        outcome.values["topic"] = "被脚本改写"
        assert snapshot["topic"] == "数据质量分析"

    def test_empty_list_inputs_returns_empty_params(self):
        """inputs=[] → 显式空清单：params == {}。"""
        outcome = assemble_params([], _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {}

    def test_selected_inputs_alias_priority(self):
        """alias 优先；alias="" 视同未设置（缺省为命名输出名）。"""
        entries = [
            AgentInputEntry(reference="agent_out.summary", alias="摘要"),
            AgentInputEntry(reference="topic"),
            AgentInputEntry(reference="agent_out.score", alias=""),
        ]
        outcome = assemble_params(entries, _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {
            "摘要": "分析完成",
            "topic": "数据质量分析",
            "agent_out": 92,
        }

    def test_default_key_is_output_name_not_reference(self):
        """缺省键 = 被引用命名输出名（agent_out.nested → 键 agent_out，非表达式原文）。"""
        entries = [AgentInputEntry(reference="agent_out.nested")]
        outcome = assemble_params(entries, _make_snapshot())

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {"agent_out": {"total": 7}}

    def test_values_passed_through_without_conversion(self):
        """四态值原样透传：null / 空集合 / 标量 / 嵌套对象。"""
        entries = [
            AgentInputEntry(reference="nullable_field"),
            AgentInputEntry(reference="empty_rows"),
            AgentInputEntry(reference="rows"),
        ]
        snapshot = _make_snapshot()
        outcome = assemble_params(entries, snapshot)

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {
            "nullable_field": None,
            "empty_rows": [],
            "rows": [{"id": 1}, {"id": 2}],
        }

    def test_missing_reference_failure_passthrough(self):
        """引用悬空 → NODE_INPUT_REFERENCE_MISSING 透传（脚本不执行）。"""
        entries = [AgentInputEntry(reference="missing_out")]
        outcome = assemble_params(entries, _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "missing_out" in outcome.error_message

    def test_type_mismatch_failure_passthrough(self):
        """字段路径类型不符 → NODE_INPUT_TYPE_MISMATCH 透传。"""
        entries = [AgentInputEntry(reference="topic.child")]
        outcome = assemble_params(entries, _make_snapshot())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH

    def test_empty_snapshot_none_inputs_returns_empty_params(self):
        """空快照 + inputs=None → 空 dict（防御语义）。"""
        outcome = assemble_params(None, {})

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {}

    def test_does_not_mutate_snapshot(self):
        """纯函数性质：不改动传入快照（INV-D01）。"""
        snapshot = _make_snapshot()
        before = dict(snapshot)
        assemble_params(None, snapshot)
        assemble_params([AgentInputEntry(reference="topic")], snapshot)
        assemble_params([AgentInputEntry(reference="missing_out")], snapshot)

        assert snapshot == before


class TestAssembleParamsAgentParity:
    """对照断言（任务 3.3）：同一 inputs 在 agent 与 code（params）装配键名口径一致。"""

    def test_selected_inputs_key_and_value_parity_with_agent(self):
        """非空选择集：params 与 assemble_agent_inputs 键值逐项一致。"""
        entries = [
            AgentInputEntry(reference="agent_out.summary", alias="摘要"),
            AgentInputEntry(reference="topic"),
            AgentInputEntry(reference="agent_out.score", alias=""),
        ]
        agent_outcome = assemble_agent_inputs(entries, _make_snapshot())
        params_outcome = assemble_params(entries, _make_snapshot())

        assert isinstance(agent_outcome, AssemblySuccess)
        assert isinstance(params_outcome, AssemblySuccess)
        assert params_outcome.values == agent_outcome.values

    def test_failure_parity_with_agent(self):
        """装配失败：错误码与错误消息与 agent 侧完全一致（同源 fail-fast）。"""
        entries = [AgentInputEntry(reference="missing_out")]
        agent_outcome = assemble_agent_inputs(entries, _make_snapshot())
        params_outcome = assemble_params(entries, _make_snapshot())

        assert isinstance(agent_outcome, AssemblyFailure)
        assert isinstance(params_outcome, AssemblyFailure)
        assert params_outcome.error_code == agent_outcome.error_code
        assert params_outcome.error_message == agent_outcome.error_message


# =============================================================================
# 形态 B（上游输入项引用 A::x）装配分支（upstream_input_ref T4.2 / CHG-T02 /
# DC-03 / INV-E02 / INV-E09 / INV-E13）——追加不改既有函数与既有用例
# =============================================================================


def _make_form_b_definitions(binding: str = "b_out") -> Dict[str, NodeDefinition]:
    """构造形态 B 解析用节点定义索引：来源节点 a 的输入位置 x 绑定给定引用。

    节点类型取 TOOL（argument_mapping 登记分支，矩阵 §2.5）——登记经
    extract_input_position_bindings 复用 _extract_input_positions 同一权威
    （OP-08），运行期与静态索引同源。
    """
    config = ToolNodeConfiguration(
        tool_identifier="demo_tool",
        argument_mapping={"x": binding},
    )
    node = NodeDefinition(
        node_id="a",
        name="",
        node_type=NodeType.TOOL,
        configuration=config,
        output_name=None,
    )
    return {"a": node}


class TestResolveInputPositionReference:
    """形态 B 装配分支核心语义（DC-03 状态机 / INV-E13 不递归 / INV-E09 未注入）。"""

    def test_one_shot_passthrough_success_without_path(self):
        """一次透传成功（无字段路径）：a::x 绑定 b_out → 取值 = 快照值。"""
        snapshot = {"b_out": [{"id": 1}, {"id": 2}]}
        outcome = resolve_reference(
            "a::x", snapshot, node_definitions=_make_form_b_definitions()
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value == [{"id": 1}, {"id": 2}]

    def test_one_shot_passthrough_success_with_path(self):
        """一次透传成功（带字段路径）：a::x.score → 在绑定解析值上逐段下钻。"""
        snapshot = {"b_out": {"score": 92, "nested": {"total": 7}}}
        definitions = _make_form_b_definitions()
        first = resolve_reference("a::x.score", snapshot, node_definitions=definitions)
        second = resolve_reference(
            "a::x.nested.total", snapshot, node_definitions=definitions
        )

        assert isinstance(first, AssemblySuccess)
        assert first.value == 92
        assert isinstance(second, AssemblySuccess)
        assert second.value == 7

    def test_null_bound_value_passes_through(self):
        """四态对齐：绑定解析值为 null → 正常传递 null（FR-14）。"""
        outcome = resolve_reference(
            "a::x", {"b_out": None}, node_definitions=_make_form_b_definitions()
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value is None

    def test_scope_node_missing_fails(self):
        """来源节点不在索引 → NODE_INPUT_REFERENCE_MISSING（消息含来源节点定位）。"""
        outcome = resolve_reference(
            "ghost::x", {}, node_definitions=_make_form_b_definitions()
        )

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "ghost" in outcome.error_message

    def test_position_without_binding_fails(self):
        """来源节点存在但输入位置无绑定 → NODE_INPUT_REFERENCE_MISSING。"""
        outcome = resolve_reference(
            "a::absent", {"b_out": 1}, node_definitions=_make_form_b_definitions()
        )

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "absent" in outcome.error_message

    def test_second_level_form_b_no_recursion(self):
        """不递归（INV-E13）：a.x 绑定 d::y（形态 B）→ 失败且消息含「不支持递归穿透」。"""
        definitions = _make_form_b_definitions(binding="d::y")
        outcome = resolve_reference("a::x", {"whatever": 1}, node_definitions=definitions)

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "不支持递归穿透" in outcome.error_message

    def test_without_node_definitions_form_b_fails(self):
        """未注入 node_definitions（缺省 None）→ 形态 B 一律失败（INV-E09）。"""
        outcome = resolve_reference("a::x", {"b_out": 1})

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING

    def test_binding_output_missing_fails_with_chain(self):
        """绑定解析失败（快照缺 b_out）→ 失败且消息含来源绑定定位链。"""
        outcome = resolve_reference("a::x", {}, node_definitions=_make_form_b_definitions())

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "b_out" in outcome.error_message

    def test_path_drill_intermediate_non_dict_type_mismatch(self):
        """下钻中间值非 dict → NODE_INPUT_TYPE_MISMATCH（与既有下钻同口径）。"""
        outcome = resolve_reference(
            "a::x.child", {"b_out": 92}, node_definitions=_make_form_b_definitions()
        )

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH

    def test_form_a_results_unchanged_without_definitions(self):
        """INV-E02：未注入时形态 A 四态结果与扩展前一致（基线抽样对照）。"""
        snapshot = _make_snapshot()
        normal = resolve_reference("topic", snapshot)
        missing = resolve_reference("gone_output", snapshot)
        path = resolve_reference("agent_out.nested.total", snapshot)
        null_value = resolve_reference("nullable_field", snapshot)

        assert isinstance(normal, AssemblySuccess)
        assert normal.value == "数据质量分析"
        assert isinstance(missing, AssemblyFailure)
        assert missing.error_code == NODE_INPUT_REFERENCE_MISSING
        assert isinstance(path, AssemblySuccess)
        assert path.value == 7
        assert isinstance(null_value, AssemblySuccess)
        assert null_value.value is None


class TestAssembleKeyedMappingFormB:
    """键值映射形态的形态 B 透传（trigger/tool/subworkflow 装配路径）。"""

    def test_form_b_entry_resolved_via_definitions(self):
        mapping = {"k": "a::x"}
        outcome = assemble_keyed_mapping(
            mapping, {"b_out": "V"}, node_definitions=_make_form_b_definitions()
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {"k": "V"}

    def test_form_b_scope_missing_collected_as_missing_field(self):
        mapping = {"k": "ghost::x"}
        outcome = assemble_keyed_mapping(
            mapping, {"b_out": "V"}, node_definitions=_make_form_b_definitions()
        )

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "缺失字段" in outcome.error_message
        assert "ghost::x" in outcome.error_message


class TestAssembleAgentInputsFormB:
    """Agent 选择集的形态 B 透传与缺省名回退（CHG-B04）。"""

    def test_default_name_falls_back_to_input_position(self):
        """无 alias → 缺省名 = input_position（而非来源节点标识 output_name）。"""
        entries = [AgentInputEntry(reference="a::x")]
        outcome = assemble_agent_inputs(
            entries, {"b_out": 7}, node_definitions=_make_form_b_definitions()
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {"x": 7}

    def test_alias_takes_priority_over_position_name(self):
        """alias 优先于位置名（FR-09 别名组织语义不变）。"""
        entries = [AgentInputEntry(reference="a::x", alias="别名")]
        outcome = assemble_agent_inputs(
            entries, {"b_out": 7}, node_definitions=_make_form_b_definitions()
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {"别名": 7}


class TestAssembleSingleReferenceFormB:
    """单一引用形态的形态 B 透传（data_transform 主引用 / output_display 单条路径）。"""

    def test_form_b_with_expected_kind_dataset_success(self):
        outcome = assemble_single_reference(
            "a::x",
            {"b_out": [1, 2]},
            expected_kind="dataset",
            node_definitions=_make_form_b_definitions(),
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value == [1, 2]

    def test_form_b_expected_kind_mismatch_fails(self):
        outcome = assemble_single_reference(
            "a::x",
            {"b_out": "标量"},
            expected_kind="dataset",
            node_definitions=_make_form_b_definitions(),
        )

        assert isinstance(outcome, AssemblyFailure)
        assert outcome.error_code == NODE_INPUT_TYPE_MISMATCH

    def test_form_b_missing_is_none_degrades_to_none(self):
        """output_display 容错特例：形态 B 解析失败 → None 占位成功（Q-1）。"""
        outcome = assemble_single_reference(
            "a::absent",
            {"b_out": 1},
            missing_is_none=True,
            node_definitions=_make_form_b_definitions(),
        )

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.value is None


class TestAssembleReferenceCollectionFormBScope:
    """登记口径钉住：assemble_reference_collection 不在 CHG-B04 扩展清单——
    形态 B 经 missing_is_none=True 降为 None 占位（output_display 多引用容错
    路径；单条路径已经 assemble_single_reference 支持形态 B）。
    """

    def test_form_b_in_collection_degrades_to_none_placeholder(self):
        outcome = assemble_reference_collection(["a::x"], {"b_out": 1}, missing_is_none=True)

        assert isinstance(outcome, AssemblySuccess)
        assert outcome.values == {"a::x": None}
