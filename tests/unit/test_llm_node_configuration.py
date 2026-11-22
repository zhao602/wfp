# =============================================================================
# 大模型节点配置与领域登记单元测试（llm_and_code_nodes 任务 2.1/2.3/2.5/2.6/2.7/2.8/3.1）
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_llm_node_configuration.py -q
#
# 覆盖：
#   - 枚举仅追加快照（CHG-01 / INV-02 / G-01：14 成员，前 12 序不变）；
#   - LlmNodeConfiguration 字段白名单 / 必备项 / 未配置采样全 None /
#     exclude_none 省略 / 未知字段忽略 / 禁止字段不声明（任务 2.3）；
#   - 采样值对象六字段 Optional=None（D-01 / CHG-02）；
#   - 判别联合 13 成员 + NodeDefinition 注入解析 + 既有 agent dump 等价（任务 2.5）；
#   - 输出类别登记 LLM/CODE 均可引用（llm_code_nodes_v2 修订 DC-13-4/DC-13-5，
#     取代 CHG-05 / 任务 2.7 的 CODE 不可引用 v1 拟定）；
#   - 9 个语义错误码逐字符 + 两 SCRIPT_NOT_EXECUTABLE 可区分（CHG-04 / 任务 2.6）；
#   - 加载期校验接入：模型标识 / 提示词上界 / 采样取值域 / 输出结构值域，
#     语义码前缀 LLM_PARAM_INVALID、issue.code 统一 WF_VALIDATION_FAILED
#     （任务 3.1 / CHG-06 / D-04 / FR-04 / FR-10 / FR-13 / AC-04 / AC-15）。
# =============================================================================

# 沙箱限制：本机 asyncio 事件循环不可用（socketpair 被网络策略拦截，
# WinError 10051 / _ssock AttributeError），run_coro 采用手动驱动协程
# 方式（send(None) 循环），不创建事件循环，故无需 import asyncio。
# 本轮尚无异步用例，按任务 2.8 要求保持文件结构一致，供后续批次复用。

from typing import get_args

import pytest
from pydantic import ValidationError

from orchestration_platform.domain.errors import (
    CODE_NODE_DISABLED,
    CODE_SCRIPT_INVALID,
    CODE_SCRIPT_NOT_EXECUTABLE,
    CODE_SCRIPT_TOO_LARGE,
    DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE,
    LLM_INFERENCE_FAILED,
    LLM_NODE_DISABLED,
    LLM_OUTPUT_INVALID,
    LLM_PARAM_INVALID,
    LLM_PARAM_UNSUPPORTED,
    WF_VALIDATION_FAILED,
)
from orchestration_platform.domain.models.enums import NodeType, Severity
from orchestration_platform.domain.models.node_configurations import (
    AgentNodeConfiguration,
    CodeNodeConfiguration,
    LlmNodeConfiguration,
    LlmSamplingParameters,
    NodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
)
from orchestration_platform.domain.values.node_output_categories import (
    NODE_OUTPUT_CATEGORIES,
    OutputCategoryInfo,
    resolve_output_category,
)


def run_coro(coro):
    """手动驱动协程至完成（沙箱事件循环不可用时的 fallback）。

    execute 的 await 链均为 AsyncMock 或纯同步逻辑协程，无真实挂起点，
    故以 send(None) 循环驱动至 StopIteration，环境无关。
    """
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


# 既有 12 成员取值序（G-01 判据冻结基线，任务 1.1 采集）
BASELINE_NODE_TYPE_VALUES = [
    "agent",
    "tool",
    "data_source",
    "trigger",
    "control_flow",
    "subworkflow",
    "dynamic_injector",
    "data_transform",
    "plugin",
    "supervisor",
    "output_display",
    "vector_knowledge_base",
]

# LlmNodeConfiguration 字段白名单（design §2.3.2(1) / spec §6.1）
LLM_FIELD_NAMES = {
    "node_type",
    "model_identifier",
    "system_prompt",
    "user_prompt",
    "inputs",
    "output_schema",
    "temperature",
    "top_p",
    "top_k",
    "max_tokens",
    "stop",
    "seed",
}


# =============================================================================
# 枚举仅追加（CHG-01 / INV-02 / G-01）
# =============================================================================


class TestNodeTypeEnumAppendOnly:
    """枚举快照：既有 12 成员零变更，末尾追加 llm/code。"""

    def test_enum_values_append_only_snapshot(self):
        values = [member.value for member in NodeType]
        assert len(values) == 14
        assert values[:12] == BASELINE_NODE_TYPE_VALUES
        assert values[12:] == ["llm", "code"]

    def test_unknown_value_rejected_by_enum(self):
        with pytest.raises(ValueError, match="llm_v2"):
            NodeType("llm_v2")

    def test_unknown_value_rejected_by_node_definition(self):
        with pytest.raises(ValidationError) as excinfo:
            NodeDefinition(
                node_id="bad-1",
                name="bad",
                node_type="llm_v2",
                configuration={"model_identifier": "primary-model"},
            )
        assert "llm_v2" in str(excinfo.value)


# =============================================================================
# LlmNodeConfiguration 模型（任务 2.3 / CHG-02 / DC-03）
# =============================================================================


class TestLlmNodeConfigurationModel:
    """大模型节点配置：字段白名单 / 必备项 / 缺省 / 未知字段策略。"""

    def test_field_whitelist(self):
        assert set(LlmNodeConfiguration.model_fields.keys()) == LLM_FIELD_NAMES

    @pytest.mark.parametrize(
        "forbidden",
        ["api_key", "base_url", "timeout_seconds", "available_tools", "max_tool_rounds"],
    )
    def test_forbidden_fields_not_declared(self, forbidden):
        # spec §6.1-13：禁止字段建模时不声明即不可配置（G-08 红线）
        assert forbidden not in LlmNodeConfiguration.model_fields

    def test_model_identifier_required(self):
        with pytest.raises(ValidationError) as excinfo:
            LlmNodeConfiguration()
        assert excinfo.value.errors()[0]["type"] == "missing"
        assert excinfo.value.errors()[0]["loc"] == ("model_identifier",)

    def test_sampling_fields_default_none(self):
        config = LlmNodeConfiguration(model_identifier="primary-model")
        assert config.temperature is None
        assert config.top_p is None
        assert config.top_k is None
        assert config.max_tokens is None
        assert config.stop is None
        assert config.seed is None

    def test_model_dump_exclude_none_without_sampling(self):
        config = LlmNodeConfiguration(model_identifier="primary-model")
        assert config.model_dump(exclude_none=True) == {
            "node_type": NodeType.LLM,
            "model_identifier": "primary-model",
            "system_prompt": "",
            "user_prompt": "",
        }

    def test_explicit_sampling_roundtrip(self):
        config = LlmNodeConfiguration(
            model_identifier="primary-model",
            temperature=0.3,
            top_p=0.9,
            top_k=20,
            max_tokens=512,
            stop=["END", "DONE"],
            seed=7,
        )
        assert config.model_dump() == {
            "node_type": NodeType.LLM,
            "model_identifier": "primary-model",
            "system_prompt": "",
            "user_prompt": "",
            "inputs": None,
            "output_schema": None,
            "temperature": 0.3,
            "top_p": 0.9,
            "top_k": 20,
            "max_tokens": 512,
            "stop": ["END", "DONE"],
            "seed": 7,
        }

    def test_unknown_fields_ignored(self):
        # 沿用既有「未知字段忽略」策略（design §2.3.2(1)：禁止 extra="forbid"）
        config = LlmNodeConfiguration.model_validate(
            {"model_identifier": "primary-model", "api_key": "x", "base_url": "x"}
        )
        assert config.model_identifier == "primary-model"
        assert "api_key" not in config.model_dump()

    def test_inputs_annotation_matches_agent(self):
        assert (
            LlmNodeConfiguration.model_fields["inputs"].annotation
            == AgentNodeConfiguration.model_fields["inputs"].annotation
        )

    def test_inputs_entries_coerced(self):
        config = LlmNodeConfiguration(
            model_identifier="primary-model",
            inputs=[{"reference": "upstream.result", "alias": "ctx"}],
        )
        assert config.inputs is not None
        assert config.inputs[0].reference == "upstream.result"
        assert config.inputs[0].alias == "ctx"


# =============================================================================
# 采样值对象（CHG-02 / D-01）
# =============================================================================


class TestLlmSamplingParametersValueObject:
    """采样值对象：六字段全部 Optional=None，不设具体默认值（D-01）。"""

    def test_six_fields_all_default_none(self):
        assert set(LlmSamplingParameters.model_fields.keys()) == {
            "temperature",
            "top_p",
            "top_k",
            "max_tokens",
            "stop",
            "seed",
        }
        params = LlmSamplingParameters()
        assert params.temperature is None
        assert params.top_p is None
        assert params.top_k is None
        assert params.max_tokens is None
        assert params.stop is None
        assert params.seed is None

    def test_explicit_values_roundtrip(self):
        params = LlmSamplingParameters(
            temperature=0.1,
            top_p=0.5,
            top_k=5,
            max_tokens=64,
            stop=["s"],
            seed=3,
        )
        assert params.model_dump() == {
            "temperature": 0.1,
            "top_p": 0.5,
            "top_k": 5,
            "max_tokens": 64,
            "stop": ["s"],
            "seed": 3,
        }


# =============================================================================
# 判别联合（任务 2.5 / CHG-02）
# =============================================================================


class TestDiscriminatedUnion:
    """判别联合追加两成员：成员数 11→13，SUPERVISOR 仍不在内。"""

    def test_union_member_count_and_new_members(self):
        union_members = get_args(get_args(NodeConfiguration)[0])
        assert len(union_members) == 13
        assert LlmNodeConfiguration in union_members
        assert CodeNodeConfiguration in union_members

    def test_supervisor_still_not_in_union(self):
        union_members = get_args(get_args(NodeConfiguration)[0])
        member_names = {member.__name__ for member in union_members}
        assert "SupervisorNodeConfiguration" not in member_names

    def test_llm_configuration_parsed_via_node_definition(self):
        definition = NodeDefinition(
            node_id="llm-1",
            name="summarize",
            node_type=NodeType.LLM,
            configuration={"model_identifier": "primary-model", "user_prompt": "总结以下内容"},
        )
        assert isinstance(definition.configuration, LlmNodeConfiguration)
        assert definition.configuration.model_identifier == "primary-model"
        assert definition.configuration.user_prompt == "总结以下内容"

    def test_agent_definition_dump_equivalent_to_baseline(self):
        definition = NodeDefinition(
            node_id="agent-1",
            name="analyze",
            node_type=NodeType.AGENT,
            configuration={
                "model_identifier": "primary-model",
                "available_tools": ["t1", "t2", "t1"],
            },
            output_name="agent_result",
        )
        assert definition.configuration.model_dump() == {
            "node_type": NodeType.AGENT,
            "model_identifier": "primary-model",
            "system_prompt": "",
            "available_tools": ["t1", "t2"],
            "output_schema": None,
            "max_tool_rounds": None,
            "output_max_retries": None,
            "sandbox_policy_id": None,
            "inputs": None,
            "context_strategy": None,
        }


# =============================================================================
# 输出类别登记（任务 2.7 / CHG-05 / DC-14；llm_code_nodes_v2 修订：CODE referable
# False→True，DC-13-4/DC-13-5——v1 拟定因「不执行」前提失效）
# =============================================================================


class TestOutputCategories:
    """LLM/CODE 均可引用（object，llm_code_nodes_v2 DC-13-4/DC-13-5）；既有 8 条零变更。"""

    def test_llm_code_resolve_non_none(self):
        llm_info = resolve_output_category(NodeType.LLM)
        code_info = resolve_output_category(NodeType.CODE)
        assert llm_info is not None
        assert llm_info.referable is True
        assert llm_info.value_kind == "object"
        assert code_info is not None
        assert code_info.referable is True
        assert code_info.value_kind == "object"

    def test_registry_entries_exact(self):
        assert NODE_OUTPUT_CATEGORIES[NodeType.LLM] == OutputCategoryInfo(
            referable=True, value_kind="object"
        )
        assert NODE_OUTPUT_CATEGORIES[NodeType.CODE] == OutputCategoryInfo(
            referable=True, value_kind="object"
        )

    def test_existing_eight_entries_unchanged(self):
        expected = {
            NodeType.AGENT: OutputCategoryInfo(referable=True, value_kind="object"),
            NodeType.TOOL: OutputCategoryInfo(referable=True, value_kind="object"),
            NodeType.DATA_SOURCE: OutputCategoryInfo(referable=True, value_kind="dataset"),
            NodeType.TRIGGER: OutputCategoryInfo(referable=True, value_kind="keyed"),
            NodeType.CONTROL_FLOW: OutputCategoryInfo(referable=False, value_kind="signal"),
            NodeType.SUBWORKFLOW: OutputCategoryInfo(referable=True, value_kind="keyed"),
            NodeType.DYNAMIC_INJECTOR: OutputCategoryInfo(referable=True, value_kind="object"),
            NodeType.DATA_TRANSFORM: OutputCategoryInfo(referable=True, value_kind="dataset"),
        }
        for node_type, info in expected.items():
            assert NODE_OUTPUT_CATEGORIES[node_type] == info

    def test_registry_size_ten(self):
        assert len(NODE_OUTPUT_CATEGORIES) == 10


# =============================================================================
# 语义错误码（任务 2.6 / CHG-04）
# =============================================================================


class TestErrorCodes:
    """9 个语义错误码与 spec §6.5 逐字符一致；两 SCRIPT_NOT_EXECUTABLE 可区分。"""

    def test_nine_codes_exact_values(self):
        assert LLM_NODE_DISABLED == "LLM_NODE_DISABLED"
        assert LLM_PARAM_INVALID == "LLM_PARAM_INVALID"
        assert LLM_PARAM_UNSUPPORTED == "LLM_PARAM_UNSUPPORTED"
        assert LLM_INFERENCE_FAILED == "LLM_INFERENCE_FAILED"
        assert LLM_OUTPUT_INVALID == "LLM_OUTPUT_INVALID"
        assert CODE_NODE_DISABLED == "CODE_NODE_DISABLED"
        assert CODE_SCRIPT_INVALID == "CODE_SCRIPT_INVALID"
        assert CODE_SCRIPT_TOO_LARGE == "CODE_SCRIPT_TOO_LARGE"
        assert CODE_SCRIPT_NOT_EXECUTABLE == "CODE_SCRIPT_NOT_EXECUTABLE"

    def test_codes_pairwise_distinct(self):
        codes = {
            LLM_NODE_DISABLED,
            LLM_PARAM_INVALID,
            LLM_PARAM_UNSUPPORTED,
            LLM_INFERENCE_FAILED,
            LLM_OUTPUT_INVALID,
            CODE_NODE_DISABLED,
            CODE_SCRIPT_INVALID,
            CODE_SCRIPT_TOO_LARGE,
            CODE_SCRIPT_NOT_EXECUTABLE,
        }
        assert len(codes) == 9

    def test_script_not_executable_codes_distinguishable(self):
        # OP-02：与 DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE 并列不复用
        assert DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE == "DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE"
        assert CODE_SCRIPT_NOT_EXECUTABLE == "CODE_SCRIPT_NOT_EXECUTABLE"
        assert DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE != CODE_SCRIPT_NOT_EXECUTABLE


# =============================================================================
# 加载期校验（任务 3.1 / CHG-06 / D-04：语义码前缀 + WF_VALIDATION_FAILED 契约）
# =============================================================================


def _make_llm_definition(*configs: dict) -> WorkflowDefinition:
    """构造仅含大模型节点的测试定义（无连线：引用规则零登记，聚焦节点级校验）。"""
    nodes = [
        NodeDefinition(
            node_id=f"llm-{index}",
            node_type=NodeType.LLM,
            configuration=config,
        )
        for index, config in enumerate(configs)
    ]
    return WorkflowDefinition(
        definition_id="wf-llm-validation",
        name="大模型节点校验测试",
        spec_version="1.0",
        nodes=nodes,
        edges=[],
        inputs=[],
    )


class TestValidateLlmNode:
    """大模型节点加载期校验：模型标识 / 提示词上界 / 采样取值域 / 输出结构。

    语义错误码作消息前缀（LLM_PARAM_INVALID: ...，D-04），issue.code 统一
    WF_VALIDATION_FAILED、severity=ERROR（既有校验契约不变）。
    """

    def setup_method(self):
        self.validator = NodeConfigurationValidator()

    # ------------------------------------------------------------------
    # 必备项：模型标识（FR-04 / AC-04）
    # ------------------------------------------------------------------
    def test_missing_model_identifier_rejected_at_parse(self):
        # 解析层即拒绝「缺失」；加载期校验兜底「空白值」（"" / "   "）
        with pytest.raises(ValidationError, match="model_identifier"):
            _make_llm_definition({"user_prompt": "hi"})

    @pytest.mark.parametrize("model_identifier", ["", "   ", "\t\n "])
    def test_blank_model_identifier_rejected(self, model_identifier):
        definition = _make_llm_definition({"model_identifier": model_identifier})
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "llm-0"
        assert issue.message.startswith(f"{LLM_PARAM_INVALID}: ")
        assert "缺少模型标识" in issue.message
        assert "model_identifier" in issue.message

    def test_validate_node_dispatches_llm(self):
        # 分发接线反证：未接线时 _validate_node 静默返回 []（G-08 红线）
        node = NodeDefinition(
            node_id="llm-0",
            node_type=NodeType.LLM,
            configuration={"model_identifier": " "},
        )
        issues = self.validator._validate_node(node)
        assert issues
        assert issues[0].message.startswith(f"{LLM_PARAM_INVALID}: ")

    def test_valid_minimal_llm_node_passes(self):
        definition = _make_llm_definition({"model_identifier": "primary-model"})
        assert self.validator.validate(definition) == []

    def test_single_node_failure_does_not_block_others(self):
        definition = _make_llm_definition(
            {"model_identifier": " "},
            {"model_identifier": "primary-model"},
        )
        issues = self.validator.validate(definition)
        assert len(issues) == 1
        assert issues[0].node_id == "llm-0"

    # ------------------------------------------------------------------
    # 采样取值域（FR-13 / AC-15 / DC-04 不静默截断；INV-12 单一权威）
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "field, value, violation",
        [
            ("temperature", 2.5, "温度 2.5 超出 [0.0, 2.0]"),
            ("temperature", -0.1, "温度 -0.1 超出 [0.0, 2.0]"),
            ("top_p", 0.0, "Top P 0.0 超出 (0.0, 1.0]"),
            ("top_p", 1.5, "Top P 1.5 超出 (0.0, 1.0]"),
            ("top_k", 101, "Top K 101 超出 [0, 100]"),
            ("max_tokens", 0, "最大生成长度 0 超出 [1, 32768]"),
            ("seed", -1, "随机种子 -1 超出 [0, 2147483647]"),
            ("stop", ["a", "b", "c", "d", "e"], "停止序列条数 5 超出 [0, 4]"),
            ("stop", ["x" * 129], "停止序列第 1 条长度 129 超出 [1, 128]"),
        ],
    )
    def test_sampling_violation_rejected_with_exact_message(self, field, value, violation):
        definition = _make_llm_definition(
            {"model_identifier": "primary-model", field: value}
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "llm-0"
        assert issue.message == f"{LLM_PARAM_INVALID}: {violation}"

    def test_sampling_multiple_violations_all_reported(self):
        # DC-04：多违规逐项全部返回，不静默截断
        definition = _make_llm_definition(
            {
                "model_identifier": "primary-model",
                "temperature": 2.5,
                "top_p": 0.0,
            }
        )
        issues = self.validator.validate(definition)
        assert [issue.message for issue in issues] == [
            f"{LLM_PARAM_INVALID}: 温度 2.5 超出 [0.0, 2.0]",
            f"{LLM_PARAM_INVALID}: Top P 0.0 超出 (0.0, 1.0]",
        ]

    @pytest.mark.parametrize(
        "sampling",
        [
            {"temperature": 0.0, "top_p": 1.0},
            {"temperature": 2.0},
            {"top_k": 0},
            {"top_k": 100},
            {"max_tokens": 1},
            {"max_tokens": 32768},
            {"stop": ["a", "b", "c", "d"]},
            {"seed": 0},
            {"seed": 2147483647},
        ],
    )
    def test_sampling_boundary_values_pass(self, sampling):
        config = {"model_identifier": "primary-model", **sampling}
        assert self.validator.validate(_make_llm_definition(config)) == []

    def test_valid_full_sampling_passes(self):
        definition = _make_llm_definition(
            {
                "model_identifier": "primary-model",
                "temperature": 0.3,
                "top_p": 0.9,
                "top_k": 20,
                "max_tokens": 512,
                "stop": ["END", "DONE"],
                "seed": 7,
            }
        )
        assert self.validator.validate(definition) == []

    # ------------------------------------------------------------------
    # 提示词长度上界（design §2.3.2(1)：≤ 32768 字符，拒绝而非截断）
    # ------------------------------------------------------------------
    def test_prompt_at_upper_bound_passes(self):
        definition = _make_llm_definition(
            {
                "model_identifier": "primary-model",
                "system_prompt": "x" * 32768,
                "user_prompt": "y" * 32768,
            }
        )
        assert self.validator.validate(definition) == []

    @pytest.mark.parametrize(
        "field, label",
        [("system_prompt", "系统提示词"), ("user_prompt", "用户提示词")],
    )
    def test_prompt_over_upper_bound_rejected(self, field, label):
        definition = _make_llm_definition(
            {
                "model_identifier": "primary-model",
                field: "x" * 32769,
            }
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "llm-0"
        assert issue.message == (
            f"{LLM_PARAM_INVALID}: 大模型节点 llm-0 {label}长度 32769 超出上界 32768 字符"
        )

    # ------------------------------------------------------------------
    # 输出结构类型名值域（design §2.3.2(1)：值 ∈ 既有类型映射，空对象=未声明）
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "type_name",
        [
            "string", "String", "str", "integer", "int", "number", "float",
            "boolean", "bool", "object", "dict", "array", "list",
        ],
    )
    def test_output_schema_registered_type_names_pass(self, type_name):
        definition = _make_llm_definition(
            {
                "model_identifier": "primary-model",
                "output_schema": {"result": type_name},
            }
        )
        assert self.validator.validate(definition) == []

    def test_output_schema_none_or_empty_passes(self):
        for output_schema in (None, {}):
            definition = _make_llm_definition(
                {
                    "model_identifier": "primary-model",
                    "output_schema": output_schema,
                }
            )
            assert self.validator.validate(definition) == []

    def test_output_schema_unregistered_type_rejected(self):
        definition = _make_llm_definition(
            {
                "model_identifier": "primary-model",
                "output_schema": {"result": "blob"},
            }
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "llm-0"
        assert issue.message == (
            f"{LLM_PARAM_INVALID}: 大模型节点 llm-0 输出结构字段 'result' "
            "声明的类型 'blob' 未登记（类型名须 ∈ 既有类型映射）"
        )
