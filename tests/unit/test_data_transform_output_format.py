# =============================================================================
# 数据转换输出格式投影单元测试（tests/unit/test_data_transform_output_format.py）
#
# 覆盖验收：expand_data_transform_ops T6.3（DC-04 / CHG-B10 / AC-17 / AC-18）
# 覆盖不变式：INV-09（缺省 dataset 与下游装配兼容，逐字等价）、
#             INV-10（命名输出键仍为 output_name，仅值形态改变）
# 覆盖矩阵（design §2.5）：
#   dataset/None → List[Dict]（下游 expected_kind="dataset" 装配成功）；
#   single_object → dict（空数据集/越界 → None）；
#   text → str（空数据集 → ""）；
#   kv → dict（空数据集 → {}）；
#   非 dataset 形态作下游主输入 → NODE_INPUT_TYPE_MISMATCH（确定行为）。
# =============================================================================

import json
from typing import Any, Dict, List, Optional

from orchestration_platform.domain.errors import NODE_INPUT_TYPE_MISMATCH
from orchestration_platform.domain.models.enums import (
    DataTransformOperation,
    DataTransformOutputFormat,
    NodeType,
)
from orchestration_platform.domain.models.node_configurations import (
    DataTransformNodeConfiguration,
    DataTransformOperationParams,
    DataTransformOutputFormatParams,
    DataTransformStep,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.engine.nodes.data_transform_node_executor import (
    DataTransformNodeExecutor,
)
from orchestration_platform.engine.nodes.data_transform_ops.output_format import (
    OutputFormatProjector,
)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

ROWS: List[Dict[str, Any]] = [
    {"id": 1, "category": "a", "value": 10},
    {"id": 2, "category": "a", "value": 30},
    {"id": 3, "category": "b", "value": 20},
    {"id": 4, "category": "b", "value": 40},
]


def _make_shared_state(outputs: Optional[Dict[str, Any]] = None) -> SharedState:
    return SharedState(
        instance_id="inst-test",
        node_outputs=outputs if outputs is not None else {"raw_data": ROWS},
    )


def _make_node_def(
    config: DataTransformNodeConfiguration,
    output_name: str = "transformed_data",
    node_id: str = "node-transform-1",
) -> NodeDefinition:
    return NodeDefinition(
        node_id=node_id,
        name="数据转换",
        node_type=NodeType.DATA_TRANSFORM,
        configuration=config,
        output_name=output_name,
    )


def _make_context(
    config: DataTransformNodeConfiguration,
    outputs: Optional[Dict[str, Any]] = None,
) -> NodeExecutionContext:
    return NodeExecutionContext(
        node_definition=_make_node_def(config),
        shared_state=_make_shared_state(outputs),
        instance_id="inst-test",
        trace_id="trace-test",
    )


def _config(**overrides: Any) -> DataTransformNodeConfiguration:
    payload: Dict[str, Any] = {
        "operation": DataTransformOperation.FILTER,
        "input_dataset_reference": "raw_data",
        "params": {"filter_condition": "True"},
    }
    payload.update(overrides)
    return DataTransformNodeConfiguration(**payload)


async def _run(
    config: DataTransformNodeConfiguration,
    outputs: Optional[Dict[str, Any]] = None,
) -> NodeExecutionResult:
    executor = DataTransformNodeExecutor()
    return await executor.execute(_make_context(config, outputs))


# ---------------------------------------------------------------------------
# 投影纯函数：dataset 缺省（零破坏，逐字等价）
# ---------------------------------------------------------------------------


class TestProjectDatasetDefault:
    def test_none_format_returns_dataset_identity(self):
        result = OutputFormatProjector.project(ROWS, None, None)

        assert result is ROWS

    def test_dataset_format_returns_dataset_identity(self):
        result = OutputFormatProjector.project(
            ROWS, DataTransformOutputFormat.DATASET, None
        )

        assert result is ROWS

    def test_dataset_format_ignores_params(self):
        params = DataTransformOutputFormatParams(record_index=2, text_field="x")

        result = OutputFormatProjector.project(
            ROWS, DataTransformOutputFormat.DATASET, params
        )

        assert result is ROWS


# ---------------------------------------------------------------------------
# 投影纯函数：single_object
# ---------------------------------------------------------------------------


class TestProjectSingleObject:
    def test_default_record_index_is_first_row(self):
        params = DataTransformOutputFormatParams()

        result = OutputFormatProjector.project(
            ROWS, DataTransformOutputFormat.SINGLE_OBJECT, params
        )

        assert result == {"id": 1, "category": "a", "value": 10}

    def test_explicit_record_index(self):
        params = DataTransformOutputFormatParams(record_index=2)

        result = OutputFormatProjector.project(
            ROWS, DataTransformOutputFormat.SINGLE_OBJECT, params
        )

        assert result == {"id": 3, "category": "b", "value": 20}

    def test_record_index_out_of_range_returns_none(self):
        params = DataTransformOutputFormatParams(record_index=99)

        result = OutputFormatProjector.project(
            ROWS, DataTransformOutputFormat.SINGLE_OBJECT, params
        )

        assert result is None

    def test_negative_record_index_returns_none(self):
        params = DataTransformOutputFormatParams(record_index=-1)

        result = OutputFormatProjector.project(
            ROWS, DataTransformOutputFormat.SINGLE_OBJECT, params
        )

        assert result is None

    def test_empty_dataset_returns_none(self):
        params = DataTransformOutputFormatParams(record_index=0)

        result = OutputFormatProjector.project(
            [], DataTransformOutputFormat.SINGLE_OBJECT, params
        )

        assert result is None

    def test_none_params_defaults_to_first_row(self):
        result = OutputFormatProjector.project(
            ROWS, DataTransformOutputFormat.SINGLE_OBJECT, None
        )

        assert result == {"id": 1, "category": "a", "value": 10}


# ---------------------------------------------------------------------------
# 投影纯函数：text
# ---------------------------------------------------------------------------


class TestProjectText:
    def test_text_field_joined_with_default_newline(self):
        params = DataTransformOutputFormatParams(text_field="category")

        result = OutputFormatProjector.project(
            ROWS, DataTransformOutputFormat.TEXT, params
        )

        assert result == "a\na\nb\nb"

    def test_custom_separator(self):
        params = DataTransformOutputFormatParams(
            text_field="category", text_separator=", "
        )

        result = OutputFormatProjector.project(
            ROWS, DataTransformOutputFormat.TEXT, params
        )

        assert result == "a, a, b, b"

    def test_non_string_values_coerced(self):
        dataset = [{"n": 1}, {"n": 2}]
        params = DataTransformOutputFormatParams(text_field="n", text_separator="-")

        result = OutputFormatProjector.project(
            dataset, DataTransformOutputFormat.TEXT, params
        )

        assert result == "1-2"

    def test_none_values_become_empty_string(self):
        dataset = [{"n": "x"}, {"n": None}, {"n": "y"}]
        params = DataTransformOutputFormatParams(text_field="n", text_separator="|")

        result = OutputFormatProjector.project(
            dataset, DataTransformOutputFormat.TEXT, params
        )

        assert result == "x||y"

    def test_empty_dataset_returns_empty_string(self):
        params = DataTransformOutputFormatParams(text_field="category")

        result = OutputFormatProjector.project(
            [], DataTransformOutputFormat.TEXT, params
        )

        assert result == ""

    def test_missing_text_field_serializes_full_json(self):
        params = DataTransformOutputFormatParams(text_separator="\n")

        result = OutputFormatProjector.project(
            ROWS, DataTransformOutputFormat.TEXT, params
        )

        assert result == json.dumps(ROWS, ensure_ascii=False)

    def test_chinese_text_not_escaped(self):
        dataset = [{"name": "张三"}, {"name": "李四"}]
        params = DataTransformOutputFormatParams(text_field="name")

        result = OutputFormatProjector.project(
            dataset, DataTransformOutputFormat.TEXT, params
        )

        assert result == "张三\n李四"


# ---------------------------------------------------------------------------
# 投影纯函数：kv
# ---------------------------------------------------------------------------


class TestProjectKv:
    def test_explicit_key_value_fields(self):
        params = DataTransformOutputFormatParams(
            kv_key_field="category", kv_value_field="value"
        )

        result = OutputFormatProjector.project(ROWS, DataTransformOutputFormat.KV, params)

        assert result == {"a": 30, "b": 40}

    def test_duplicate_keys_later_wins(self):
        dataset = [
            {"k": "x", "v": 1},
            {"k": "x", "v": 2},
        ]
        params = DataTransformOutputFormatParams(kv_key_field="k", kv_value_field="v")

        result = OutputFormatProjector.project(
            dataset, DataTransformOutputFormat.KV, params
        )

        assert result == {"x": 2}

    def test_empty_dataset_returns_empty_dict(self):
        params = DataTransformOutputFormatParams(
            kv_key_field="category", kv_value_field="value"
        )

        result = OutputFormatProjector.project([], DataTransformOutputFormat.KV, params)

        assert result == {}

    def test_default_fields_first_two_columns_first_seen_order(self):
        dataset = [
            {"b": 2, "a": 1},
            {"c": 3, "b": 4},
        ]

        result = OutputFormatProjector.project(
            dataset, DataTransformOutputFormat.KV, None
        )

        # 首见列序：键列=b、值列=a；第二行无 a 列 → 值 None
        assert result == {2: 1, 4: None}

    def test_single_column_defaults_value_none(self):
        dataset = [{"a": 1}, {"a": 2}]

        result = OutputFormatProjector.project(
            dataset, DataTransformOutputFormat.KV, None
        )

        assert result == {1: None, 2: None}

    def test_unhashable_key_serialized_to_json(self):
        dataset = [{"k": [1, 2], "v": "x"}]
        params = DataTransformOutputFormatParams(kv_key_field="k", kv_value_field="v")

        result = OutputFormatProjector.project(
            dataset, DataTransformOutputFormat.KV, params
        )

        assert result == {"[1, 2]": "x"}

    def test_missing_value_field_in_row_is_none(self):
        dataset = [{"k": "a", "v": 1}, {"k": "b"}]
        params = DataTransformOutputFormatParams(kv_key_field="k", kv_value_field="v")

        result = OutputFormatProjector.project(
            dataset, DataTransformOutputFormat.KV, params
        )

        assert result == {"a": 1, "b": None}


# ---------------------------------------------------------------------------
# 执行器集成：链尾投影（T6.2）
# ---------------------------------------------------------------------------


class TestExecutorProjection:
    async def test_default_output_format_returns_list_zero_break(self):
        result = await _run(_config())

        assert result.success is True
        assert result.output_name == "transformed_data"
        assert result.output_value == ROWS
        assert isinstance(result.output_value, list)

    async def test_explicit_dataset_format_returns_list(self):
        result = await _run(
            _config(output_format=DataTransformOutputFormat.DATASET)
        )

        assert result.success is True
        assert result.output_value == ROWS

    async def test_single_object_output_value_is_dict(self):
        result = await _run(
            _config(
                output_format=DataTransformOutputFormat.SINGLE_OBJECT,
                output_format_params=DataTransformOutputFormatParams(record_index=1),
            )
        )

        assert result.success is True
        assert result.output_name == "transformed_data"
        assert result.output_value == {"id": 2, "category": "a", "value": 30}

    async def test_single_object_default_record_index(self):
        result = await _run(
            _config(output_format=DataTransformOutputFormat.SINGLE_OBJECT)
        )

        assert result.success is True
        assert result.output_value == {"id": 1, "category": "a", "value": 10}

    async def test_text_output_value_is_str(self):
        result = await _run(
            _config(
                output_format=DataTransformOutputFormat.TEXT,
                output_format_params=DataTransformOutputFormatParams(
                    text_field="category"
                ),
            )
        )

        assert result.success is True
        assert result.output_value == "a\na\nb\nb"

    async def test_kv_output_value_is_dict(self):
        result = await _run(
            _config(
                output_format=DataTransformOutputFormat.KV,
                output_format_params=DataTransformOutputFormatParams(
                    kv_key_field="category", kv_value_field="value"
                ),
            )
        )

        assert result.success is True
        assert result.output_value == {"a": 30, "b": 40}

    async def test_empty_dataset_single_object_returns_none(self):
        result = await _run(
            _config(output_format=DataTransformOutputFormat.SINGLE_OBJECT),
            outputs={"raw_data": []},
        )

        assert result.success is True
        assert result.output_value is None

    async def test_empty_dataset_text_returns_empty_string(self):
        result = await _run(
            _config(
                output_format=DataTransformOutputFormat.TEXT,
                output_format_params=DataTransformOutputFormatParams(
                    text_field="category"
                ),
            ),
            outputs={"raw_data": []},
        )

        assert result.success is True
        assert result.output_value == ""

    async def test_empty_dataset_kv_returns_empty_dict(self):
        result = await _run(
            _config(output_format=DataTransformOutputFormat.KV),
            outputs={"raw_data": []},
        )

        assert result.success is True
        assert result.output_value == {}

    async def test_chain_then_projection(self):
        config = _config(
            operations=[
                DataTransformStep(
                    operation=DataTransformOperation.SELECT_COLUMNS,
                    params=DataTransformOperationParams(
                        select_columns=["id", "value"]
                    ),
                ),
                DataTransformStep(
                    operation=DataTransformOperation.FILTER,
                    params=DataTransformOperationParams(filter_condition="True"),
                ),
            ],
            output_format=DataTransformOutputFormat.SINGLE_OBJECT,
            output_format_params=DataTransformOutputFormatParams(record_index=3),
        )

        result = await _run(config)

        assert result.success is True
        assert result.output_value == {"id": 4, "value": 40}

    async def test_projection_preserves_warnings_metadata(self):
        config = _config(
            operations=[
                DataTransformStep(
                    operation=DataTransformOperation.MASKING,
                    params=DataTransformOperationParams(
                        mask_fields=["id"], mask_rules=["phone"]
                    ),
                ),
            ],
            output_format=DataTransformOutputFormat.SINGLE_OBJECT,
        )

        result = await _run(config)

        assert result.success is True
        assert isinstance(result.output_value, dict)
        warnings = result.metadata.get("warnings")
        assert isinstance(warnings, list)
        assert len(warnings) >= 1


# ---------------------------------------------------------------------------
# 下游装配兼容（INV-09 / design §2.5 矩阵）
# ---------------------------------------------------------------------------


class TestDownstreamCompat:
    async def test_single_object_output_downstream_dataset_mismatch(self):
        result_a = await _run(
            _config(output_format=DataTransformOutputFormat.SINGLE_OBJECT)
        )

        assert result_a.success is True
        assert isinstance(result_a.output_value, dict)

        # 下游节点以 single_object 输出为主输入 → expected_kind="dataset" 失败
        result_b = await _run(_config(), outputs={"raw_data": result_a.output_value})

        assert result_b.success is False
        assert result_b.error_code == NODE_INPUT_TYPE_MISMATCH

    async def test_dataset_output_downstream_compatible(self):
        result_a = await _run(_config())

        assert result_a.success is True
        assert isinstance(result_a.output_value, list)

        result_b = await _run(_config(), outputs={"raw_data": result_a.output_value})

        assert result_b.success is True
        assert result_b.output_value == ROWS
