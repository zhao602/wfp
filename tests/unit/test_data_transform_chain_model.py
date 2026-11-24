# =============================================================================
# 数据转换链路模型与归一化单元测试（tests/unit/test_data_transform_chain_model.py）
#
# 覆盖验收：expand_data_transform_ops T1.7（CHG-B01/B02/B12/B13/B14/B17）
# 覆盖不变式：INV-01（可选字段追加）、INV-02（枚举仅追加）、INV-04（3.10 兼容）、
#             INV-05（旧 YAML 等价链长 1）、INV-12（开关默认 False）、INV-15（上限常量）
# =============================================================================

from typing import Any, Dict, List

import pytest

from orchestration_platform.configuration.settings import Settings
from orchestration_platform.domain.errors import (
    DATA_TRANSFORM_CHAIN_TOO_LONG,
    DATA_TRANSFORM_INVALID_STEP_PARAMS,
    DATA_TRANSFORM_REGEX_INPUT_TOO_LONG,
    DATA_TRANSFORM_REGEX_UNSAFE_PATTERN,
    DATA_TRANSFORM_ROW_LIMIT_EXCEEDED,
    DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE,
    DATA_TRANSFORM_STEP_FAILED,
    DATA_TRANSFORM_UNKNOWN_OPERATION,
)
from orchestration_platform.domain.models.enums import (
    AggregateFunction,
    DataTransformOperation,
    DataTransformOutputFormat,
    JoinType,
    NodeType,
)
from orchestration_platform.domain.models.node_configurations import (
    DataTransformNodeConfiguration,
    DataTransformStep,
    MaskingCustomPattern,
    normalize_operations,
)
from orchestration_platform.engine.nodes.data_transform_ops import (
    DEFAULT_FLATTEN_MAX_DEPTH,
    MAX_CHAIN_STEPS,
    MAX_EXPLODE_ROWS,
    MAX_JSON_TEXT_LENGTH,
    MAX_REGEX_INPUT_LENGTH,
    MAX_REGEX_PATTERN_LENGTH,
    OPERATION_REGISTRY,
    StepContext,
    StepOutcome,
    get_handler,
    register,
    registered_operations,
)


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _legacy_config(**overrides: Any) -> DataTransformNodeConfiguration:
    """旧式单操作配置（无任何新字段，等价旧 YAML 形态）。"""
    payload: Dict[str, Any] = {
        "operation": DataTransformOperation.FILTER,
        "input_dataset_reference": "raw_data",
        "params": {"filter_condition": "value >= 10"},
    }
    payload.update(overrides)
    return DataTransformNodeConfiguration(**payload)


def _chain_config(steps: List[Dict[str, Any]], **overrides: Any) -> DataTransformNodeConfiguration:
    """新式链配置：顶层 operation 必填保持，链经 operations 声明。"""
    payload: Dict[str, Any] = {
        "operation": DataTransformOperation.MAP,
        "input_dataset_reference": "raw_data",
        "operations": steps,
    }
    payload.update(overrides)
    return DataTransformNodeConfiguration(**payload)


# ---------------------------------------------------------------------------
# 模型可选字段追加（T1.1 / INV-01 / INV-05）
# ---------------------------------------------------------------------------


class TestModelOptionalFields:
    def test_legacy_config_parses_with_new_fields_absent(self):
        config = _legacy_config()

        assert config.operation == DataTransformOperation.FILTER
        assert config.input_dataset_reference == "raw_data"
        assert config.params.filter_condition == "value >= 10"
        assert config.operations is None
        assert config.output_format is None
        assert config.output_format_params is None

    def test_legacy_config_ignores_undeclared_fields(self):
        config = DataTransformNodeConfiguration.model_validate(
            {
                "operation": "filter",
                "input_dataset_reference": "raw_data",
                "params": {"filter_condition": "value >= 10"},
                "unknown_future_field": {"any": "shape"},
            }
        )

        assert config.operation == DataTransformOperation.FILTER
        assert config.operations is None

    def test_chain_fields_parse_with_types(self):
        config = _chain_config(
            [
                {"operation": "sort", "params": {"sort_keys": ["score"], "sort_orders": ["desc"]}},
                {"operation": "handle_nulls", "params": {"null_fields": ["score"], "strategy": "fill_mean"}},
            ],
            output_format=DataTransformOutputFormat.TEXT,
            output_format_params={"text_field": "summary", "text_separator": "\n"},
        )

        assert config.operations is not None and len(config.operations) == 2
        assert isinstance(config.operations[0], DataTransformStep)
        assert config.operations[0].operation == DataTransformOperation.SORT
        assert config.operations[0].params.sort_keys == ["score"]
        assert config.operations[1].params.strategy == "fill_mean"
        assert config.output_format == DataTransformOutputFormat.TEXT
        assert config.output_format_params is not None
        assert config.output_format_params.text_field == "summary"

    def test_new_params_roundtrip_via_model_dump(self):
        config = _chain_config(
            [
                {
                    "operation": "masking",
                    "params": {
                        "mask_fields": ["phone"],
                        "mask_rules": ["phone", "email"],
                        "custom_patterns": [
                            {"pattern": r"(\d{3})\d{4}(\d{4})", "mask_template": "{1}****{2}"}
                        ],
                    },
                }
            ]
        )

        dump = config.model_dump(mode="json", exclude_none=True)
        step_params = dump["operations"][0]["params"]

        assert step_params["mask_fields"] == ["phone"]
        assert step_params["custom_patterns"][0]["mask_template"] == "{1}****{2}"

        restored = DataTransformNodeConfiguration.model_validate(dump)
        assert restored.operations is not None
        assert restored.operations[0].params.custom_patterns is not None
        assert restored.operations[0].params.custom_patterns[0] == MaskingCustomPattern(
            pattern=r"(\d{3})\d{4}(\d{4})", mask_template="{1}****{2}"
        )

    def test_legacy_dump_keeps_flat_shape(self):
        """旧式配置序列化不引入新键（导出形态保真，CHG-B06 兜底）。"""
        config = _legacy_config()
        dump = config.model_dump(mode="json", exclude_none=True)

        assert dump["operation"] == "filter"
        assert "operations" not in dump
        assert "output_format" not in dump
        assert "output_format_params" not in dump


# ---------------------------------------------------------------------------
# 归一化纯函数（T1.2 / DC-01 / INV-05）
# ---------------------------------------------------------------------------


class TestNormalizeOperations:
    def test_missing_operations_falls_back_to_single_step(self):
        config = _legacy_config()
        steps = normalize_operations(config)

        assert len(steps) == 1
        assert steps[0].operation == DataTransformOperation.FILTER
        assert steps[0].params is config.params

    def test_empty_operations_falls_back_to_single_step(self):
        config = _chain_config([])
        steps = normalize_operations(config)

        assert len(steps) == 1
        assert steps[0].operation == DataTransformOperation.MAP

    def test_single_step_chain_equivalent_to_top_level(self):
        config = _chain_config(
            [{"operation": "filter", "params": {"filter_condition": "value >= 10"}}]
        )
        steps = normalize_operations(config)

        assert len(steps) == 1
        assert steps[0].operation == DataTransformOperation.FILTER
        assert steps[0].params.filter_condition == "value >= 10"

    def test_multi_step_chain_returned_as_is(self):
        config = _chain_config(
            [
                {"operation": "sort", "params": {"sort_keys": ["a"]}},
                {"operation": "distinct", "params": {"distinct_keys": ["a"]}},
                {"operation": "handle_nulls", "params": {"null_fields": ["a"], "strategy": "drop_row"}},
            ]
        )
        steps = normalize_operations(config)

        assert [s.operation for s in steps] == [
            DataTransformOperation.SORT,
            DataTransformOperation.DISTINCT,
            DataTransformOperation.HANDLE_NULLS,
        ]
        assert steps is not config.operations  # 过滤后新列表，原字段不被改写
        assert config.operations is not None and len(config.operations) == 3

    def test_pure_function_deterministic(self):
        config = _chain_config(
            [
                {"operation": "sort", "params": {"sort_keys": ["a"]}},
                {"operation": "drop_columns", "params": {"drop_columns": ["b"]}},
            ]
        )

        first = normalize_operations(config)
        second = normalize_operations(config)

        assert first == second
        assert [id(s) for s in first] != []  # 列表内容稳定可比

    def test_fallback_does_not_mutate_config(self):
        config = _legacy_config()

        normalize_operations(config)
        normalize_operations(config)

        assert config.operations is None  # 不回填（INV-05③ 加载路径不覆盖/回填）


# ---------------------------------------------------------------------------
# 枚举仅追加（T1.3 / INV-02 / 守护⑨）
# ---------------------------------------------------------------------------


class TestEnumsAppendOnly:
    def test_existing_operation_members_unchanged(self):
        assert DataTransformOperation.MAP.value == "map"
        assert DataTransformOperation.FILTER.value == "filter"
        assert DataTransformOperation.AGGREGATE.value == "aggregate"
        assert DataTransformOperation.JOIN.value == "join"

    def test_new_operation_members_appended(self):
        expected = {
            "sort", "distinct", "rename", "select_columns", "drop_columns", "cast",
            "row_number", "flatten", "explode", "handle_nulls", "regex_extract",
            "regex_replace", "validate", "masking", "json_path_extract", "sampling",
            "custom_code",
        }
        values = {member.value for member in DataTransformOperation}

        assert expected <= values
        assert {m.value for m in DataTransformOperation} - expected == {"map", "filter", "aggregate", "join"}

    def test_output_format_enum_values(self):
        assert DataTransformOutputFormat.DATASET.value == "dataset"
        assert DataTransformOutputFormat.SINGLE_OBJECT.value == "single_object"
        assert DataTransformOutputFormat.TEXT.value == "text"
        assert DataTransformOutputFormat.KV.value == "kv"

    def test_node_type_has_no_new_members(self):
        """本任务不新增节点类型（守护⑨：NodeType 枚举 diff 为空）。"""
        data_transform_like = [
            member.value
            for member in NodeType
            if "transform" in member.value or "TRANSFORM" in member.name
        ]
        assert data_transform_like == ["data_transform"]


# ---------------------------------------------------------------------------
# 错误码仅追加（T1.4 / INV-01）
# ---------------------------------------------------------------------------


class TestErrorCodesAppendOnly:
    def test_new_error_codes_defined(self):
        assert DATA_TRANSFORM_UNKNOWN_OPERATION == "DATA_TRANSFORM_UNKNOWN_OPERATION"
        assert DATA_TRANSFORM_INVALID_STEP_PARAMS == "DATA_TRANSFORM_INVALID_STEP_PARAMS"
        assert DATA_TRANSFORM_STEP_FAILED == "DATA_TRANSFORM_STEP_FAILED"
        assert DATA_TRANSFORM_CHAIN_TOO_LONG == "DATA_TRANSFORM_CHAIN_TOO_LONG"
        assert DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE == "DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE"
        assert DATA_TRANSFORM_REGEX_UNSAFE_PATTERN == "DATA_TRANSFORM_REGEX_UNSAFE_PATTERN"
        assert DATA_TRANSFORM_REGEX_INPUT_TOO_LONG == "DATA_TRANSFORM_REGEX_INPUT_TOO_LONG"
        assert DATA_TRANSFORM_ROW_LIMIT_EXCEEDED == "DATA_TRANSFORM_ROW_LIMIT_EXCEEDED"


# ---------------------------------------------------------------------------
# 脚本能力开关（T1.5 / INV-12 / OP-10）
# ---------------------------------------------------------------------------


class TestCustomCodeSwitchDefaultOff:
    def test_setting_default_false(self):
        assert Settings.model_fields["data_transform_custom_code_enabled"].default is False

    def test_settings_instance_default_false(self):
        settings = Settings()

        assert settings.data_transform_custom_code_enabled is False


# ---------------------------------------------------------------------------
# 操作协议与注册表（T1.6 / DC-02）
# ---------------------------------------------------------------------------


class _UpperCaseHandler:
    """测试用处理器：为每行追加 uppercase 标记字段（无状态纯函数）。

    挂靠 SAMPLING（B5 前未注册；注册前先摘除原处理器并事后恢复，
    对批次注册进度不敏感）。
    """

    operation = DataTransformOperation.SAMPLING
    degradable = False

    def apply(self, ctx: StepContext) -> StepOutcome:
        rows = [{**row, "touched": True} for row in ctx.left_dataset]
        return StepOutcome(value=rows)


class TestRegistry:
    def test_get_handler_unregistered_returns_none(self):
        """摘除后查询须返回 None（对注册进度不敏感）。"""
        original = OPERATION_REGISTRY.pop(DataTransformOperation.SAMPLING, None)
        try:
            assert get_handler(DataTransformOperation.SAMPLING) is None
        finally:
            if original is not None:
                OPERATION_REGISTRY[DataTransformOperation.SAMPLING] = original

    def test_register_and_get_handler_roundtrip(self):
        original = OPERATION_REGISTRY.pop(DataTransformOperation.SAMPLING, None)
        handler = _UpperCaseHandler()
        register(handler)

        try:
            assert get_handler(DataTransformOperation.SAMPLING) is handler
            assert DataTransformOperation.SAMPLING in registered_operations()
        finally:
            OPERATION_REGISTRY.pop(DataTransformOperation.SAMPLING, None)
            if original is not None:
                OPERATION_REGISTRY[DataTransformOperation.SAMPLING] = original

    def test_register_same_instance_twice_is_idempotent(self):
        original = OPERATION_REGISTRY.pop(DataTransformOperation.SAMPLING, None)
        handler = _UpperCaseHandler()
        register(handler)

        try:
            register(handler)  # 同实例重复注册幂等
            assert OPERATION_REGISTRY[DataTransformOperation.SAMPLING] is handler
        finally:
            OPERATION_REGISTRY.pop(DataTransformOperation.SAMPLING, None)
            if original is not None:
                OPERATION_REGISTRY[DataTransformOperation.SAMPLING] = original

    def test_register_conflicting_handler_raises(self):
        original = OPERATION_REGISTRY.pop(DataTransformOperation.SAMPLING, None)
        first = _UpperCaseHandler()
        second = _UpperCaseHandler()
        register(first)

        try:
            with pytest.raises(RuntimeError):
                register(second)
        finally:
            OPERATION_REGISTRY.pop(DataTransformOperation.SAMPLING, None)
            if original is not None:
                OPERATION_REGISTRY[DataTransformOperation.SAMPLING] = original

    def test_step_outcome_failed_factory(self):
        outcome = StepOutcome.failed(
            DATA_TRANSFORM_UNKNOWN_OPERATION, "步骤 #0（sort）：操作未注册"
        )

        assert outcome.failure_code == DATA_TRANSFORM_UNKNOWN_OPERATION
        assert outcome.failure_message is not None
        assert outcome.value == []
        assert outcome.degraded is False
        assert outcome.warnings == []

    def test_step_outcome_degraded_defaults(self):
        outcome = StepOutcome(value=[], degraded=True, warnings=["缺口 3 行"])

        assert outcome.failure_code is None
        assert outcome.degraded is True
        assert outcome.warnings == ["缺口 3 行"]


# ---------------------------------------------------------------------------
# 资源上限常量（T1 集中定义 / INV-15）
# ---------------------------------------------------------------------------


class TestResourceLimits:
    def test_limit_constants_match_design(self):
        assert MAX_CHAIN_STEPS == 20
        assert MAX_REGEX_PATTERN_LENGTH == 512
        assert MAX_REGEX_INPUT_LENGTH == 8192
        assert MAX_EXPLODE_ROWS == 100000
        assert DEFAULT_FLATTEN_MAX_DEPTH == 3
        assert MAX_JSON_TEXT_LENGTH == 256 * 1024


# ---------------------------------------------------------------------------
# 旧 YAML 等价性（T1.1 验收 / INV-05）
# ---------------------------------------------------------------------------


class TestLegacyYamlEquivalence:
    def test_legacy_yaml_payload_parses_and_normalizes_to_chain_len_one(self):
        """旧 YAML configuration 片段（仅 operation/params）加载后归一化为链长 1。"""
        legacy_payload: Dict[str, Any] = {
            "operation": "aggregate",
            "input_dataset_reference": "filtered_data",
            "params": {
                "group_by": ["category"],
                "aggregate_function": "sum",
                "aggregate_field": "value",
            },
        }
        config = DataTransformNodeConfiguration.model_validate(legacy_payload)
        steps = normalize_operations(config)

        assert config.operation == DataTransformOperation.AGGREGATE
        assert config.params.aggregate_function == AggregateFunction.SUM
        assert len(steps) == 1
        assert steps[0].operation == DataTransformOperation.AGGREGATE
        assert steps[0].params.group_by == ["category"]

    def test_legacy_join_params_still_parse(self):
        config = DataTransformNodeConfiguration.model_validate(
            {
                "operation": "join",
                "input_dataset_reference": "left",
                "params": {
                    "join_type": "left",
                    "join_key": "id",
                    "right_dataset_reference": "right_ds",
                },
            }
        )

        assert config.params.join_type == JoinType.LEFT
        assert config.params.join_key == "id"
        assert config.params.right_dataset_reference == "right_ds"
        assert normalize_operations(config)[0].operation == DataTransformOperation.JOIN
