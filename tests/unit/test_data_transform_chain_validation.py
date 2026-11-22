# =============================================================================
# 数据转换操作链合法性校验单元测试
# （tests/unit/test_data_transform_chain_validation.py）
#
# 覆盖验收：expand_data_transform_ops T7.3（FR-04 / AC-03 / DC-02 / CHG-B16）
# 覆盖不变式：INV-01（接口签名不变）、INV-09（既有引用校验 8 规则口径不变）
# 覆盖面：
#   - 步数上限（DATA_TRANSFORM_CHAIN_TOO_LONG）+ 节点定位 + 原值保留；
#   - 未知操作（DATA_TRANSFORM_UNKNOWN_OPERATION）+ 步骤定位；
#   - 必填参数缺失 / 取值越界（DATA_TRANSFORM_INVALID_STEP_PARAMS）
#     逐操作镜像运行期口径 + 步骤定位 + 原值保留；
#   - 正则模式安全（DATA_TRANSFORM_REGEX_UNSAFE_PATTERN）；
#   - 链内 join 步 right_dataset_reference 引用提取（T7.2：
#     新增提取位置、既有单 operation 分支语义不变、规则 2/6 适用）；
#   - 合法链零误报（含既有单操作配置零破坏）。
# =============================================================================

from typing import Any, Dict, List, Optional, Tuple

from orchestration_platform.domain.errors import WF_VALIDATION_FAILED
from orchestration_platform.domain.models.common import InputParameter
from orchestration_platform.domain.models.enums import (
    DataTransformOperation,
    NodeType,
    Severity,
)
from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
    collect_input_references,
)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str,
    node_type: NodeType,
    config: dict,
    output_name: Optional[str] = None,
) -> NodeDefinition:
    """构造测试用节点（configuration 用 dict，由 NodeDefinition 判别联合解析）。"""
    return NodeDefinition(
        node_id=node_id,
        node_type=node_type,
        configuration=config,
        output_name=output_name,
    )


def _make_definition(
    nodes: list,
    edge_pairs: Optional[List[Tuple[str, str]]] = None,
    input_names: Optional[List[str]] = None,
) -> WorkflowDefinition:
    """构造测试用工作流定义（input_names 为定义级输入参数声明，规则 2 豁免域）。"""
    edges = [
        EdgeDefinition(edge_id=f"e{i}", source_node_id=s, target_node_id=t)
        for i, (s, t) in enumerate(edge_pairs or [])
    ]
    return WorkflowDefinition(
        definition_id="wf-dt-chain-validation",
        name="链校验测试",
        spec_version="1.0",
        nodes=list(nodes),
        edges=edges,
        inputs=[InputParameter(name=name) for name in (input_names or ["raw_data"])],
    )


def _dt_node(
    operations: List[Dict[str, Any]],
    node_id: str = "dt",
    operation: str = "filter",
    input_dataset_reference: str = "raw_data",
    params: Optional[Dict[str, Any]] = None,
) -> NodeDefinition:
    """构造操作链形态的数据转换节点（顶层 operation 为必填兼容字段）。"""
    config: Dict[str, Any] = {
        "operation": operation,
        "input_dataset_reference": input_dataset_reference,
        "operations": operations,
    }
    if params is not None:
        config["params"] = params
    return _make_node(node_id, NodeType.DATA_TRANSFORM, config)


def _validate(
    node: NodeDefinition,
    edge_pairs: Optional[List[Tuple[str, str]]] = None,
    input_names: Optional[List[str]] = None,
) -> List[Any]:
    definition = _make_definition([node], edge_pairs, input_names)
    return NodeConfigurationValidator().validate(definition)


def _validate_step(step: Dict[str, Any]) -> Tuple[List[Any], NodeDefinition]:
    """单步链便捷入口：返回（问题清单, 节点）供定位与原值断言。"""
    node = _dt_node([step])
    return _validate(node), node


# ---------------------------------------------------------------------------
# 步数上限
# ---------------------------------------------------------------------------


class TestChainLength:
    def test_chain_within_limit_passes(self):
        node = _dt_node(
            [
                {"operation": "sort", "params": {"sort_keys": ["id"]}},
                {"operation": "distinct", "params": {"distinct_keys": ["id"]}},
                {"operation": "filter", "params": {"filter_condition": "True"}},
            ]
        )

        issues = _validate(node)

        assert issues == []

    def test_chain_too_long_reported_with_node_positioning(self):
        steps = [
            {"operation": "filter", "params": {"filter_condition": "True"}}
            for _ in range(21)
        ]
        node = _dt_node(steps)

        issues = _validate(node)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "dt"
        assert "DATA_TRANSFORM_CHAIN_TOO_LONG" in issue.message
        assert "操作链步数 21 超上限 20" in issue.message
        # 原值保留：校验不静默改写用户配置
        config = node.configuration
        assert len(config.operations) == 21

    def test_legacy_single_operation_no_chain_issues(self):
        node = _make_node(
            "dt",
            NodeType.DATA_TRANSFORM,
            {
                "operation": "filter",
                "input_dataset_reference": "raw_data",
                "params": {"filter_condition": "True"},
            },
        )

        issues = _validate(node)

        assert issues == []


# ---------------------------------------------------------------------------
# 未知操作（枚举合法但未注册处理器）
# ---------------------------------------------------------------------------


class TestUnknownOperation:
    def test_unregistered_operation_reported_with_step_positioning(self):
        from orchestration_platform.engine.nodes.data_transform_ops import (
            OPERATION_REGISTRY,
        )

        node = _dt_node(
            [
                {"operation": "filter", "params": {"filter_condition": "True"}},
                {"operation": "sampling", "params": {"sample_n": 2}},
            ]
        )

        original = OPERATION_REGISTRY.pop(DataTransformOperation.SAMPLING)
        try:
            issues = _validate(node)
        finally:
            OPERATION_REGISTRY[DataTransformOperation.SAMPLING] = original

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "dt"
        assert "步骤 #1（sampling）" in issue.message
        assert "DATA_TRANSFORM_UNKNOWN_OPERATION" in issue.message
        # 原值保留
        config = node.configuration
        assert len(config.operations) == 2
        assert config.operations[1].params.sample_n == 2

    def test_registered_operations_not_flagged_unknown(self):
        node = _dt_node(
            [
                {"operation": "filter", "params": {"filter_condition": "True"}},
                {"operation": "sort", "params": {"sort_keys": ["id"]}},
            ]
        )

        issues = _validate(node)

        assert issues == []


# ---------------------------------------------------------------------------
# 必填参数缺失（逐操作镜像运行期口径）
# ---------------------------------------------------------------------------


class TestRequiredParams:
    def test_select_columns_empty_rejected(self):
        issues, node = _validate_step(
            {"operation": "select_columns", "params": {"select_columns": []}}
        )

        assert len(issues) == 1
        assert "步骤 #0（select_columns）" in issues[0].message
        assert "DATA_TRANSFORM_INVALID_STEP_PARAMS" in issues[0].message
        assert "select_columns 为空" in issues[0].message
        assert node.configuration.operations[0].params.select_columns == []

    def test_cast_missing_field_rejected(self):
        issues, _ = _validate_step(
            {"operation": "cast", "params": {"cast_target": "number"}}
        )

        assert len(issues) == 1
        assert "步骤 #0（cast）" in issues[0].message
        assert "缺 cast_field" in issues[0].message

    def test_cast_date_requires_explicit_format(self):
        issues, _ = _validate_step(
            {"operation": "cast", "params": {"cast_field": "d", "cast_target": "date"}}
        )

        assert len(issues) == 1
        assert "步骤 #0（cast）" in issues[0].message
        assert "缺 date_format" in issues[0].message

    def test_explode_missing_field_rejected(self):
        issues, _ = _validate_step({"operation": "explode", "params": {}})

        assert len(issues) == 1
        assert "步骤 #0（explode）" in issues[0].message
        assert "缺 explode_field" in issues[0].message

    def test_regex_extract_missing_params_rejected(self):
        issues, _ = _validate_step(
            {"operation": "regex_extract", "params": {"regex_field": "name"}}
        )

        assert len(issues) == 1
        assert "步骤 #0（regex_extract）" in issues[0].message
        assert "缺必填参数" in issues[0].message

    def test_regex_replace_missing_params_rejected(self):
        issues, _ = _validate_step(
            {
                "operation": "regex_replace",
                "params": {"regex_field": "name", "pattern": "a+"},
            }
        )

        assert len(issues) == 1
        assert "步骤 #0（regex_replace）" in issues[0].message
        assert "缺必填参数" in issues[0].message

    def test_validate_missing_params_rejected(self):
        issues, _ = _validate_step(
            {"operation": "validate", "params": {"validate_rule": "email"}}
        )

        assert len(issues) == 1
        assert "步骤 #0（validate）" in issues[0].message
        assert "缺必填参数" in issues[0].message

    def test_json_path_extract_missing_params_rejected(self):
        issues, _ = _validate_step(
            {"operation": "json_path_extract", "params": {"path": "$.a"}}
        )

        assert len(issues) == 1
        assert "步骤 #0（json_path_extract）" in issues[0].message
        assert "缺必填参数" in issues[0].message

    def test_join_multi_key_missing_right_reference_rejected(self):
        issues, _ = _validate_step(
            {
                "operation": "join",
                "params": {"join_keys": ["a", "b"]},
            }
        )

        assert len(issues) == 1
        assert "步骤 #0（join）" in issues[0].message
        assert "缺 right_dataset_reference" in issues[0].message

    def test_join_multi_key_length_mismatch_rejected(self):
        issues, _ = _validate_step(
            {
                "operation": "join",
                "params": {
                    "join_keys": ["a", "b"],
                    "right_join_keys": ["x"],
                    "right_dataset_reference": "right_ds",
                },
            }
        )
        # right_ds 未声明为输入参数/命名输出 → 另有规则 2 存在性问题，须并存
        join_issue = next(
            i for i in issues if "长度不符" in i.message
        )
        assert "步骤 #0（join）" in join_issue.message
        assert "DATA_TRANSFORM_INVALID_STEP_PARAMS" in join_issue.message

    def test_sampling_missing_scale_rejected(self):
        issues, _ = _validate_step(
            {"operation": "sampling", "params": {"sample_mode": "head"}}
        )

        assert len(issues) == 1
        assert "步骤 #0（sampling）" in issues[0].message
        assert "缺抽样规模" in issues[0].message

    def test_handle_nulls_fill_constant_missing_value_rejected(self):
        issues, _ = _validate_step(
            {"operation": "handle_nulls", "params": {"strategy": "fill_constant"}}
        )

        assert len(issues) == 1
        assert "步骤 #0（handle_nulls）" in issues[0].message
        assert "缺 fill_value" in issues[0].message

    def test_masking_unknown_builtin_rule_rejected(self):
        issues, _ = _validate_step(
            {
                "operation": "masking",
                "params": {"mask_fields": ["email"], "mask_rules": ["nonexistent"]},
            }
        )

        assert len(issues) == 1
        assert "步骤 #0（masking）" in issues[0].message
        assert "内置规则不存在：nonexistent" in issues[0].message


# ---------------------------------------------------------------------------
# 取值越界 / 语法 / 正则安全
# ---------------------------------------------------------------------------


class TestValueRange:
    def test_sampling_percent_out_of_range_rejected(self):
        issues, _ = _validate_step(
            {"operation": "sampling", "params": {"sample_percent": 150}}
        )

        assert len(issues) == 1
        assert "步骤 #0（sampling）" in issues[0].message
        assert "0<比例≤100" in issues[0].message

    def test_sampling_negative_n_rejected(self):
        issues, _ = _validate_step(
            {"operation": "sampling", "params": {"sample_n": -1}}
        )

        assert len(issues) == 1
        assert "非负整数" in issues[0].message

    def test_sampling_invalid_mode_rejected(self):
        issues, _ = _validate_step(
            {"operation": "sampling", "params": {"sample_mode": "bogus", "sample_n": 1}}
        )

        assert len(issues) == 1
        assert "sample_mode 取值不支持：bogus" in issues[0].message

    def test_flatten_max_depth_invalid_rejected(self):
        issues, _ = _validate_step(
            {"operation": "flatten", "params": {"flatten_field": "u", "max_depth": 0}}
        )

        assert len(issues) == 1
        assert "max_depth 须 ≥ 1" in issues[0].message

    def test_explode_max_rows_invalid_rejected(self):
        issues, _ = _validate_step(
            {"operation": "explode", "params": {"explode_field": "tags", "max_rows": 0}}
        )

        assert len(issues) == 1
        assert "max_rows 须 ≥ 1" in issues[0].message

    def test_regex_extract_invalid_mode_rejected(self):
        issues, _ = _validate_step(
            {
                "operation": "regex_extract",
                "params": {
                    "regex_field": "name",
                    "pattern": "a+",
                    "target_field": "out",
                    "extract_mode": "bogus",
                },
            }
        )

        assert len(issues) == 1
        assert "extract_mode 取值不支持：bogus" in issues[0].message

    def test_validate_invalid_rule_rejected(self):
        issues, _ = _validate_step(
            {
                "operation": "validate",
                "params": {"validate_field": "f", "validate_rule": "phone"},
            }
        )

        assert len(issues) == 1
        assert "validate_rule 取值不支持：phone" in issues[0].message

    def test_validate_invalid_on_invalid_rejected(self):
        issues, _ = _validate_step(
            {
                "operation": "validate",
                "params": {
                    "validate_field": "f",
                    "validate_rule": "email",
                    "on_invalid": "bogus",
                },
            }
        )

        assert len(issues) == 1
        assert "on_invalid 取值不支持：bogus" in issues[0].message

    def test_handle_nulls_invalid_strategy_rejected(self):
        issues, _ = _validate_step(
            {"operation": "handle_nulls", "params": {"strategy": "bogus"}}
        )

        assert len(issues) == 1
        assert "strategy 取值不支持：bogus" in issues[0].message

    def test_distinct_invalid_keep_rejected(self):
        issues, _ = _validate_step(
            {"operation": "distinct", "params": {"keep": "last"}}
        )

        assert len(issues) == 1
        assert "keep 取值不支持：last" in issues[0].message

    def test_custom_code_unsupported_language_rejected(self):
        issues, _ = _validate_step(
            {"operation": "custom_code", "params": {"language": "javascript"}}
        )

        assert len(issues) == 1
        assert "步骤 #0（custom_code）" in issues[0].message
        assert "language 取值不支持：javascript" in issues[0].message

    def test_json_path_syntax_error_rejected(self):
        issues, _ = _validate_step(
            {
                "operation": "json_path_extract",
                "params": {
                    "source_field": "payload",
                    "path": "$.a]x",
                    "target_field": "out",
                },
            }
        )

        assert len(issues) == 1
        assert "步骤 #0（json_path_extract）" in issues[0].message
        assert "路径语法非法" in issues[0].message

    def test_regex_unsafe_pattern_reported_with_safety_code(self):
        issues, _ = _validate_step(
            {
                "operation": "regex_extract",
                "params": {
                    "regex_field": "name",
                    "pattern": "(a+)+",
                    "target_field": "out",
                },
            }
        )

        assert len(issues) == 1
        assert "DATA_TRANSFORM_REGEX_UNSAFE_PATTERN" in issues[0].message
        assert "步骤 #0（regex_extract）" in issues[0].message

    def test_regex_pattern_too_long_reported(self):
        issues, _ = _validate_step(
            {
                "operation": "regex_extract",
                "params": {
                    "regex_field": "name",
                    "pattern": "a" * 513,
                    "target_field": "out",
                },
            }
        )

        assert len(issues) == 1
        assert "DATA_TRANSFORM_REGEX_UNSAFE_PATTERN" in issues[0].message
        assert "513" in issues[0].message


# ---------------------------------------------------------------------------
# 合法链零误报
# ---------------------------------------------------------------------------


class TestValidChainPasses:
    def test_full_valid_chain_no_issues(self):
        node = _dt_node(
            [
                {"operation": "sort", "params": {"sort_keys": ["id"], "sort_orders": ["asc"]}},
                {"operation": "distinct", "params": {"distinct_keys": ["id"]}},
                {
                    "operation": "select_columns",
                    "params": {"select_columns": ["id", "name", "email", "payload"]},
                },
                {"operation": "cast", "params": {"cast_field": "id", "cast_target": "number"}},
                {
                    "operation": "handle_nulls",
                    "params": {"strategy": "fill_constant", "fill_value": "", "null_fields": ["name"]},
                },
                {
                    "operation": "regex_extract",
                    "params": {"regex_field": "name", "pattern": "[A-Za-z]+", "target_field": "name_clean"},
                },
                {"operation": "validate", "params": {"validate_field": "email", "validate_rule": "email"}},
                {"operation": "masking", "params": {"mask_fields": ["email"], "mask_rules": ["email"]}},
                {
                    "operation": "json_path_extract",
                    "params": {
                        "source_field": "payload",
                        "path": "$.data.items[0].title",
                        "target_field": "title",
                    },
                },
                {"operation": "sampling", "params": {"sample_mode": "head", "sample_n": 100}},
            ]
        )

        issues = _validate(node)

        assert issues == []


# ---------------------------------------------------------------------------
# 链内 join 步副引用提取（T7.2，INV-09：既有分支语义不变）
# ---------------------------------------------------------------------------


class TestChainJoinReferenceExtraction:
    def test_chain_join_right_reference_extracted(self):
        node = _dt_node(
            [
                {"operation": "filter", "params": {"filter_condition": "True"}},
                {
                    "operation": "join",
                    "params": {"join_key": "id", "right_dataset_reference": "right_ds"},
                },
            ],
            input_dataset_reference="left_ds",
        )

        refs = collect_input_references(node)

        assert refs == [
            ("input_dataset_reference", "left_ds"),
            ("operations[1].params.right_dataset_reference", "right_ds"),
        ]

    def test_chain_without_join_steps_extracts_only_main_reference(self):
        node = _dt_node(
            [
                {"operation": "filter", "params": {"filter_condition": "True"}},
                {"operation": "sort", "params": {"sort_keys": ["id"]}},
            ],
            input_dataset_reference="left_ds",
        )

        refs = collect_input_references(node)

        assert refs == [("input_dataset_reference", "left_ds")]

    def test_chain_config_ignores_top_level_join_params(self):
        """operations 存在时运行期仅用链步：顶层 join 副引用不再提取（避免误报）。"""
        node = _dt_node(
            [
                {
                    "operation": "join",
                    "params": {"join_key": "id", "right_dataset_reference": "chain_right"},
                },
            ],
            operation="join",
            input_dataset_reference="left_ds",
            params={"join_key": "id", "right_dataset_reference": "legacy_right"},
        )

        refs = collect_input_references(node)

        assert refs == [
            ("input_dataset_reference", "left_ds"),
            ("operations[0].params.right_dataset_reference", "chain_right"),
        ]

    def test_legacy_join_extraction_unchanged(self):
        node = _make_node(
            "dt",
            NodeType.DATA_TRANSFORM,
            {
                "operation": "join",
                "input_dataset_reference": "left_ds",
                "params": {"join_key": "id", "right_dataset_reference": "right_ds"},
            },
        )

        refs = collect_input_references(node)

        assert refs == [
            ("input_dataset_reference", "left_ds"),
            ("params.right_dataset_reference", "right_ds"),
        ]

    def test_chain_join_reference_nonexistent_target_reported(self):
        node = _dt_node(
            [
                {
                    "operation": "join",
                    "params": {"join_key": "id", "right_dataset_reference": "ghost"},
                },
            ]
        )

        issues = _validate(node)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "dt"
        assert "operations[0].params.right_dataset_reference" in issue.message
        assert "引用目标不存在" in issue.message
        assert "ghost" in issue.message

    def test_chain_join_reference_category_mismatch_reported(self):
        producer = _make_node(
            "producer",
            NodeType.AGENT,
            {"model_identifier": "m"},
            output_name="agent_out",
        )
        node = _dt_node(
            [
                {
                    "operation": "join",
                    "params": {"join_key": "id", "right_dataset_reference": "agent_out"},
                },
            ]
        )
        definition = _make_definition(
            [producer, node], [("producer", "dt")], input_names=["raw_data"]
        )

        issues = NodeConfigurationValidator().validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "dt"
        assert "operations[0].params.right_dataset_reference" in issue.message
        assert "非数据集类" in issue.message
        assert "object" in issue.message


# ---------------------------------------------------------------------------
# 原值保留（不静默改写）
# ---------------------------------------------------------------------------


class TestOriginalValuePreserved:
    def test_validation_does_not_rewrite_config(self):
        node = _dt_node(
            [
                {"operation": "sort", "params": {"sort_keys": ["id"]}},
                {"operation": "cast", "params": {"cast_target": "date"}},
                {"operation": "sampling", "params": {"sample_percent": 150}},
            ]
        )
        config = node.configuration

        issues = _validate(node)

        assert len(issues) == 3  # cast 缺 field、cast date 缺 date_format、sampling 越界
        # 原值逐字保留：步数、操作序、参数值均未被改写
        assert len(config.operations) == 3
        assert config.operations[0].operation == DataTransformOperation.SORT
        assert config.operations[0].params.sort_keys == ["id"]
        assert config.operations[1].params.cast_target == "date"
        assert config.operations[2].params.sample_percent == 150
        assert config.input_dataset_reference == "raw_data"
