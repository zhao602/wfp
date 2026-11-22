# =============================================================================
# 结构化操作 10 项矩阵单测（tests/unit/test_data_transform_ops_structure.py）
#
# 覆盖验收：expand_data_transform_ops T3.11（CHG-B03 / DC-03 §2.4.1（1）~（10））
# 覆盖操作：sort / distinct / rename / select_columns / drop_columns / cast /
#           join（多条件复合键）/ row_number / flatten / explode
# 覆盖维度：输入 / 输出 / 边界 / 错误码 / 降级（表驱动参数化）
# 覆盖不变式：INV-09（副引用装配四态）、INV-13（join 单键兼容形态）、
#             INV-15（explode 行数上限 / flatten 深度上限）
# =============================================================================

from typing import Any, Dict, List, Optional

import pytest

from orchestration_platform.domain.errors import (
    DATA_TRANSFORM_INVALID_STEP_PARAMS,
    DATA_TRANSFORM_ROW_LIMIT_EXCEEDED,
    NODE_INPUT_TYPE_MISMATCH,
)
from orchestration_platform.domain.models.enums import (
    DataTransformOperation,
    NodeType,
)
from orchestration_platform.domain.models.node_configurations import (
    DataTransformNodeConfiguration,
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


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

ROWS: List[Dict[str, Any]] = [
    {"id": 1, "category": "a", "value": 10},
    {"id": 2, "category": "a", "value": 30},
    {"id": 3, "category": "b", "value": 20},
    {"id": 4, "category": "b", "value": 40},
]


def _make_context(
    config: DataTransformNodeConfiguration,
    outputs: Optional[Dict[str, Any]] = None,
) -> NodeExecutionContext:
    node_def = NodeDefinition(
        node_id="node-transform-1",
        name="数据转换",
        node_type=NodeType.DATA_TRANSFORM,
        configuration=config,
        output_name="transformed_data",
    )
    shared_state = SharedState(
        instance_id="inst-test",
        node_outputs=outputs if outputs is not None else {"raw_data": ROWS},
    )
    return NodeExecutionContext(
        node_definition=node_def,
        shared_state=shared_state,
        instance_id="inst-test",
        trace_id="trace-test",
    )


def _config(
    operation: str,
    params: Dict[str, Any],
    outputs_name: str = "raw_data",
) -> DataTransformNodeConfiguration:
    return DataTransformNodeConfiguration(
        operation=DataTransformOperation(operation),
        input_dataset_reference=outputs_name,
        operations=[{"operation": operation, "params": params}],
    )


async def _run_op(
    operation: str,
    params: Dict[str, Any],
    rows: List[Dict[str, Any]],
    extra_outputs: Optional[Dict[str, Any]] = None,
) -> NodeExecutionResult:
    outputs: Dict[str, Any] = {"raw_data": rows}
    if extra_outputs:
        outputs.update(extra_outputs)
    executor = DataTransformNodeExecutor()
    return await executor.execute(_make_context(_config(operation, params), outputs))


def _warnings(result: NodeExecutionResult) -> List[str]:
    warnings = result.metadata.get("warnings")
    return warnings if isinstance(warnings, list) else []


# ---------------------------------------------------------------------------
# （1）sort 排序（T3.1 / FR-05 / AC-04）
# ---------------------------------------------------------------------------


class TestSort:
    async def test_multi_key_independent_directions(self):
        rows = [
            {"cat": "a", "val": 2},
            {"cat": "b", "val": 1},
            {"cat": "a", "val": 1},
        ]
        result = await _run_op(
            "sort",
            {"sort_keys": ["cat", "val"], "sort_orders": ["asc", "desc"]},
            rows,
        )

        assert result.success is True
        assert result.output_value == [
            {"cat": "a", "val": 2},
            {"cat": "a", "val": 1},
            {"cat": "b", "val": 1},
        ]

    @pytest.mark.parametrize(
        ("orders", "expected_values"),
        [
            (["asc"], [1, 3, None]),  # 升序：空值排末尾
            (["desc"], [3, 1, None]),  # 降序：空值仍排末尾（双向一致）
        ],
    )
    async def test_nulls_sort_last_in_both_directions(
        self, orders: List[str], expected_values: List[Any]
    ):
        rows = [{"v": 1}, {"v": None}, {"v": 3}]
        result = await _run_op("sort", {"sort_keys": ["v"], "sort_orders": orders}, rows)

        assert result.success is True
        assert [r["v"] for r in result.output_value] == expected_values

    async def test_missing_key_in_row_treated_as_null(self):
        rows = [{"v": 5}, {"other": 1}, {"v": 1}]
        result = await _run_op("sort", {"sort_keys": ["v"]}, rows)

        assert result.success is True
        values = [r.get("v") for r in result.output_value]
        assert values[:2] == [1, 5]
        assert values[2] is None  # 键缺失行视为空值排末尾

    async def test_nonexistent_sort_key_warns_without_failure(self):
        result = await _run_op("sort", {"sort_keys": ["ghost"]}, ROWS)

        assert result.success is True
        assert result.output_value == ROWS  # 全空键稳定：顺序不变
        assert any("ghost" in w for w in _warnings(result))

    async def test_empty_sort_keys_pass_through(self):
        result = await _run_op("sort", {}, ROWS)

        assert result.success is True
        assert result.output_value == ROWS
        assert _warnings(result) == []

    async def test_orders_shorter_than_keys_default_asc(self):
        rows = [{"a": 2, "b": 1}, {"a": 1, "b": 9}]
        result = await _run_op(
            "sort", {"sort_keys": ["a", "b"], "sort_orders": ["desc"]}, rows
        )

        assert result.success is True
        assert [r["a"] for r in result.output_value] == [2, 1]  # a 降序；b 缺省升序


# ---------------------------------------------------------------------------
# （2）distinct 去重（T3.2 / FR-06 / AC-04）
# ---------------------------------------------------------------------------


class TestDistinct:
    async def test_distinct_by_keys_keeps_first(self):
        rows = [
            {"k": 1, "v": "一"},
            {"k": 2, "v": "二"},
            {"k": 1, "v": "重复"},
        ]
        result = await _run_op("distinct", {"distinct_keys": ["k"]}, rows)

        assert result.success is True
        assert result.output_value == [
            {"k": 1, "v": "一"},
            {"k": 2, "v": "二"},
        ]

    async def test_all_columns_distinct_empty_keys(self):
        rows = [
            {"a": 1, "b": 2},
            {"a": 1, "b": 2},
            {"a": 1, "b": 3},
        ]
        result = await _run_op("distinct", {}, rows)

        assert result.success is True
        assert result.output_value == [
            {"a": 1, "b": 2},
            {"a": 1, "b": 3},
        ]

    async def test_complex_values_deterministic_serialization(self):
        """复合值（dict）键序无关：稳定序列化键去重确定性。"""
        rows = [
            {"meta": {"a": 1, "b": 2}, "id": 1},
            {"meta": {"b": 2, "a": 1}, "id": 2},
        ]
        result = await _run_op("distinct", {"distinct_keys": ["meta"]}, rows)

        assert result.success is True
        output = result.output_value
        assert len(output) == 1
        assert output[0]["id"] == 1  # 保留首个

    async def test_unsupported_keep_rejected(self):
        result = await _run_op("distinct", {"keep": "last"}, ROWS)

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS
        assert "keep" in (result.error_message or "")


# ---------------------------------------------------------------------------
# （3）rename 重命名列（T3.3 / FR-07 / AC-04）
# ---------------------------------------------------------------------------


class TestRename:
    async def test_rename_applies(self):
        result = await _run_op("rename", {"rename_map": {"category": "cat"}}, ROWS)

        assert result.success is True
        first = result.output_value[0]
        assert "cat" in first and "category" not in first
        assert first["cat"] == "a"

    async def test_target_conflict_rejected(self):
        result = await _run_op("rename", {"rename_map": {"category": "value"}}, ROWS)

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS
        assert "category" in (result.error_message or "")

    async def test_duplicate_targets_rejected(self):
        result = await _run_op(
            "rename", {"rename_map": {"category": "x", "value": "x"}}, ROWS
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_swap_rename_allowed(self):
        """目标名存在但同时被改名移走 → 允许（单趟映射，无级联）。"""
        result = await _run_op(
            "rename", {"rename_map": {"category": "value", "value": "category"}}, ROWS
        )

        assert result.success is True
        first = result.output_value[0]
        assert first["value"] == "a" and first["category"] == 10

    async def test_missing_source_ignored_with_warning(self):
        result = await _run_op("rename", {"rename_map": {"ghost": "new"}}, ROWS)

        assert result.success is True
        assert result.output_value == ROWS
        assert any("ghost" in w for w in _warnings(result))

    async def test_empty_map_pass_through(self):
        result = await _run_op("rename", {}, ROWS)

        assert result.success is True
        assert result.output_value == ROWS


# ---------------------------------------------------------------------------
# （4）select_columns 选择列（T3.4 / FR-07 / AC-04）
# ---------------------------------------------------------------------------


class TestSelectColumns:
    async def test_ordered_subset(self):
        result = await _run_op(
            "select_columns", {"select_columns": ["value", "id"]}, ROWS
        )

        assert result.success is True
        first = result.output_value[0]
        assert list(first.keys()) == ["value", "id"]
        assert first == {"value": 10, "id": 1}

    async def test_missing_field_filled_with_none(self):
        rows = [{"a": 1}, {"b": 2}]
        result = await _run_op("select_columns", {"select_columns": ["a", "b"]}, rows)

        assert result.success is True
        assert result.output_value == [{"a": 1, "b": None}, {"a": None, "b": 2}]

    async def test_nonexistent_column_warns(self):
        result = await _run_op(
            "select_columns", {"select_columns": ["id", "ghost"]}, ROWS
        )

        assert result.success is True
        assert any("ghost" in w for w in _warnings(result))

    async def test_empty_selection_rejected(self):
        """空列表 = 破坏性歧义（输出空列集）→ 拒绝（非静默）。"""
        result = await _run_op("select_columns", {"select_columns": []}, ROWS)

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS


# ---------------------------------------------------------------------------
# （5）drop_columns 删除列（T3.5 / FR-07 / AC-04）
# ---------------------------------------------------------------------------


class TestDropColumns:
    async def test_drop_specified_columns(self):
        result = await _run_op("drop_columns", {"drop_columns": ["category"]}, ROWS)

        assert result.success is True
        first = result.output_value[0]
        assert first == {"id": 1, "value": 10}

    async def test_unknown_column_warns(self):
        result = await _run_op("drop_columns", {"drop_columns": ["ghost"]}, ROWS)

        assert result.success is True
        assert result.output_value == ROWS
        assert any("ghost" in w for w in _warnings(result))

    async def test_empty_drop_pass_through(self):
        result = await _run_op("drop_columns", {"drop_columns": []}, ROWS)

        assert result.success is True
        assert result.output_value == ROWS
        assert _warnings(result) == []


# ---------------------------------------------------------------------------
# （6）cast 类型强转（T3.6 / FR-08 / AC-05 / OP-11）
# ---------------------------------------------------------------------------


class TestCast:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("42", 42),
            ("3.5", 3.5),
            (7, 7),
            (2.5, 2.5),
        ],
    )
    async def test_cast_number_success(self, raw: Any, expected: Any):
        rows = [{"v": raw}]
        result = await _run_op(
            "cast", {"cast_field": "v", "cast_target": "number"}, rows
        )

        assert result.success is True
        assert result.output_value[0]["v"] == expected
        assert _warnings(result) == []

    async def test_cast_number_invalid_cell_null_with_count(self):
        rows = [{"v": "42"}, {"v": "abc"}, {"v": None}]
        result = await _run_op(
            "cast", {"cast_field": "v", "cast_target": "number"}, rows
        )

        assert result.success is True
        output = result.output_value
        assert output[0]["v"] == 42
        assert output[1]["v"] is None  # 非法 → 置空
        assert output[2]["v"] is None  # 空值 → 置空
        warnings = _warnings(result)
        assert warnings and "2" in warnings[0]  # 计数告警（不中断链）

    async def test_cast_date_explicit_format_to_iso(self):
        rows = [{"d": "2026-01-02"}, {"d": "bad"}]
        result = await _run_op(
            "cast",
            {"cast_field": "d", "cast_target": "date", "date_format": "%Y-%m-%d"},
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert output[0]["d"] == "2026-01-02T00:00:00"  # ISO 8601 字符串
        assert output[1]["d"] is None  # 格式不匹配 → 置空 + 计数
        assert _warnings(result)

    async def test_cast_date_missing_format_rejected(self):
        result = await _run_op(
            "cast", {"cast_field": "d", "cast_target": "date"}, [{"d": "2026-01-02"}]
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS
        assert "date_format" in (result.error_message or "")

    async def test_invalid_target_rejected(self):
        result = await _run_op(
            "cast", {"cast_field": "v", "cast_target": "boolean"}, [{"v": 1}]
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_missing_cast_field_rejected(self):
        result = await _run_op("cast", {"cast_target": "number"}, [{"v": 1}])

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS


# ---------------------------------------------------------------------------
# （7）join 多条件复合键（T3.7 / FR-09 / AC-06 / OP-07）
# ---------------------------------------------------------------------------

_JOIN_LEFT = [
    {"k1": "a", "k2": "x", "lv": 1},
    {"k1": "a", "k2": "y", "lv": 2},
    {"k1": "b", "k2": "x", "lv": 3},
]
_JOIN_RIGHT = [
    {"k1": "a", "k2": "x", "rv": 10},
    {"k1": "b", "k2": "x", "rv": 30},
    {"k1": "b", "k2": "x", "rv": 31},
    {"k1": "c", "k2": "z", "rv": 90},
]


class TestJoinMultiKey:
    async def test_dual_key_left_join_requires_full_match(self):
        result = await _run_op(
            "join",
            {
                "join_keys": ["k1", "k2"],
                "join_type": "left",
                "right_dataset_reference": "right_ds",
            },
            _JOIN_LEFT,
            extra_outputs={"right_ds": _JOIN_RIGHT},
        )

        assert result.success is True
        output = result.output_value
        # 左行 1 匹配右行 1；左行 2 无匹配（k2 不符）保留；左行 3 匹配右行 2 行
        assert len(output) == 4
        assert output[0]["right_rv"] == 10
        assert "right_rv" not in output[1]
        assert [r["right_rv"] for r in output[2:]] == [30, 31]

    async def test_inner_join_drops_unmatched_left(self):
        result = await _run_op(
            "join",
            {
                "join_keys": ["k1", "k2"],
                "join_type": "inner",
                "right_dataset_reference": "right_ds",
            },
            _JOIN_LEFT,
            extra_outputs={"right_ds": _JOIN_RIGHT},
        )

        assert result.success is True
        assert len(result.output_value) == 3  # 左行 2 被剔除

    async def test_null_keys_do_not_match(self):
        left = [{"k1": 1, "k2": None, "lv": 1}]
        right = [{"k1": 1, "k2": None, "rv": 5}]
        result = await _run_op(
            "join",
            {
                "join_keys": ["k1", "k2"],
                "join_type": "inner",
                "right_dataset_reference": "right_ds",
            },
            left,
            extra_outputs={"right_ds": right},
        )

        assert result.success is True
        assert result.output_value == []  # 空值键不参与匹配

    async def test_right_join_keys_different_names(self):
        left = [{"cat": "a", "sub": 1, "lv": 1}]
        right = [{"rcat": "a", "rsub": 1, "rv": 9}]
        result = await _run_op(
            "join",
            {
                "join_keys": ["cat", "sub"],
                "right_join_keys": ["rcat", "rsub"],
                "join_type": "inner",
                "right_dataset_reference": "right_ds",
            },
            left,
            extra_outputs={"right_ds": right},
        )

        assert result.success is True
        output = result.output_value
        assert len(output) == 1
        assert output[0]["right_rv"] == 9
        assert output[0]["lv"] == 1

    async def test_right_join_appends_unmatched_right_rows(self):
        left = [{"k1": "a", "k2": "x", "lv": 1}]
        result = await _run_op(
            "join",
            {
                "join_keys": ["k1", "k2"],
                "join_type": "right",
                "right_dataset_reference": "right_ds",
            },
            left,
            extra_outputs={"right_ds": _JOIN_RIGHT},
        )

        assert result.success is True
        output = result.output_value
        assert len(output) == 4  # 1 匹配 + 3 未匹配右行
        assert output[0]["right_rv"] == 10
        assert any(r.get("right_rv") == 90 for r in output)

    async def test_key_length_mismatch_rejected(self):
        result = await _run_op(
            "join",
            {
                "join_keys": ["k1", "k2"],
                "right_join_keys": ["k1"],
                "join_type": "inner",
                "right_dataset_reference": "right_ds",
            },
            _JOIN_LEFT,
            extra_outputs={"right_ds": _JOIN_RIGHT},
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_multi_key_without_right_reference_rejected(self):
        result = await _run_op(
            "join",
            {"join_keys": ["k1", "k2"], "join_type": "inner"},
            _JOIN_LEFT,
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_right_reference_non_list_propagates_type_mismatch(self):
        result = await _run_op(
            "join",
            {
                "join_keys": ["k1", "k2"],
                "join_type": "inner",
                "right_dataset_reference": "bad_right",
            },
            _JOIN_LEFT,
            extra_outputs={"bad_right": {"not": "list"}},
        )

        assert result.success is False
        assert result.error_code == NODE_INPUT_TYPE_MISMATCH

    async def test_empty_join_keys_falls_back_to_single_key(self):
        """join_keys 为空 → 回落单键兼容形态（INV-13）。"""
        result = await _run_op(
            "join",
            {
                "join_type": "left",
                "join_key": "id",
                "right_dataset_reference": "right_ds",
            },
            ROWS,
            extra_outputs={"right_ds": [{"id": 1, "label": "一"}]},
        )

        assert result.success is True
        output = result.output_value
        assert len(output) == 4
        assert output[0]["right_label"] == "一"


# ---------------------------------------------------------------------------
# （8）row_number 分组窗口（T3.8 / FR-10 / AC-07 / OP-07）
# ---------------------------------------------------------------------------


class TestRowNumber:
    async def test_group_order_numbering_from_one(self):
        result = await _run_op(
            "row_number",
            {
                "window_group_by": ["category"],
                "window_order_by": ["value"],
                "window_orders": ["desc"],
            },
            ROWS,
        )

        assert result.success is True
        output = result.output_value
        # 行序保持原顺序，仅追加序号列；组内按 value 降序从 1 起编号
        by_id = {r["id"]: r for r in output}
        assert by_id[1]["row_number"] == 2  # a 组 value=10 → 第 2
        assert by_id[2]["row_number"] == 1  # a 组 value=30 → 第 1
        assert by_id[3]["row_number"] == 2  # b 组 value=20 → 第 2
        assert by_id[4]["row_number"] == 1  # b 组 value=40 → 第 1
        assert [r["id"] for r in output] == [1, 2, 3, 4]  # 原行序不变

    async def test_custom_output_field(self):
        result = await _run_op(
            "row_number",
            {"window_output_field": "rn", "window_order_by": ["value"]},
            ROWS,
        )

        assert result.success is True
        by_id = {r["id"]: r for r in result.output_value}
        assert "row_number" not in by_id[1]
        assert by_id[1]["rn"] == 1  # value=10 升序全局第 1（缺省 asc）

    async def test_global_numbering_without_group(self):
        result = await _run_op(
            "row_number",
            {"window_order_by": ["value"]},
            ROWS,
        )

        assert result.success is True
        values = {r["id"]: r["row_number"] for r in result.output_value}
        assert values == {1: 1, 3: 2, 2: 3, 4: 4}  # 按 value 升序（缺省 asc）

    async def test_no_order_numbering_by_row_order(self):
        """无排序键：按组内出现顺序编号（从 1 起，无并列）。"""
        result = await _run_op(
            "row_number",
            {"window_group_by": ["category"]},
            ROWS,
        )

        assert result.success is True
        by_id = {r["id"]: r for r in result.output_value}
        assert by_id[1]["row_number"] == 1
        assert by_id[2]["row_number"] == 2
        assert by_id[3]["row_number"] == 1
        assert by_id[4]["row_number"] == 2


# ---------------------------------------------------------------------------
# （9）flatten 打平嵌套对象（T3.9 / FR-11 / AC-08 / INV-15）
# ---------------------------------------------------------------------------


class TestFlatten:
    async def test_nested_dict_flattened_with_dotted_path(self):
        rows = [{"用户": {"姓名": "张三", "年龄": 20}, "id": 1}]
        result = await _run_op("flatten", {"flatten_field": "用户"}, rows)

        assert result.success is True
        assert result.output_value == [
            {"id": 1, "用户.姓名": "张三", "用户.年龄": 20}
        ]

    async def test_default_depth_three_truncates_with_warning(self):
        rows = [{"用户": {"a": {"b": {"c": {"d": 1}}}}}]
        result = await _run_op("flatten", {"flatten_field": "用户"}, rows)

        assert result.success is True
        output = result.output_value[0]
        assert output["用户.a.b.c"] == {"d": 1}  # 深度 3 截断（值原样保留）
        assert any("截断" in w for w in _warnings(result))

    async def test_explicit_max_depth(self):
        rows = [{"x": {"a": {"b": 1}}}]
        result = await _run_op(
            "flatten", {"flatten_field": "x", "max_depth": 1}, rows
        )

        assert result.success is True
        assert result.output_value == [{"x.a": {"b": 1}}]

    async def test_non_dict_value_preserved(self):
        rows = [{"用户": "不是对象", "id": 1}]
        result = await _run_op("flatten", {"flatten_field": "用户"}, rows)

        assert result.success is True
        assert result.output_value == [{"id": 1, "用户": "不是对象"}]

    async def test_key_conflict_disambiguated_with_suffix(self):
        rows = [{"x": 1, "用户": {"姓名": "张三"}, "用户.姓名": "已占位"}]
        result = await _run_op("flatten", {"flatten_field": "用户"}, rows)

        assert result.success is True
        output = result.output_value[0]
        assert output["用户.姓名"] == "已占位"
        assert output["用户.姓名_1"] == "张三"  # 冲突加后缀消歧

    async def test_whole_row_flatten_when_field_empty(self):
        rows = [{"a": 1, "b": {"c": 2}}]
        result = await _run_op("flatten", {"flatten_field": ""}, rows)

        assert result.success is True
        assert result.output_value == [{"a": 1, "b.c": 2}]

    async def test_invalid_max_depth_rejected(self):
        result = await _run_op(
            "flatten", {"flatten_field": "x", "max_depth": 0}, [{"x": {"a": 1}}]
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS


# ---------------------------------------------------------------------------
# （10）explode 展开 JSON 数组为多行（T3.10 / FR-11 / AC-08 / INV-15）
# ---------------------------------------------------------------------------


class TestExplode:
    async def test_array_expanded_with_other_fields_copied(self):
        rows = [
            {"id": 1, "tags": ["x", "y"]},
            {"id": 2, "tags": ["z"]},
        ]
        result = await _run_op("explode", {"explode_field": "tags"}, rows)

        assert result.success is True
        assert result.output_value == [
            {"id": 1, "tags": "x"},
            {"id": 1, "tags": "y"},
            {"id": 2, "tags": "z"},
        ]
        assert _warnings(result) == []

    async def test_non_array_and_empty_array_nulled_with_count(self):
        rows = [
            {"id": 1, "tags": "非数组"},
            {"id": 2, "tags": []},
            {"id": 3, "tags": ["ok"]},
        ]
        result = await _run_op("explode", {"explode_field": "tags"}, rows)

        assert result.success is True
        output = result.output_value
        assert len(output) == 3
        assert output[0]["tags"] is None  # 非数组 → 置空
        assert output[1]["tags"] is None  # 空数组 → 置空
        assert output[2]["tags"] == "ok"
        warnings = _warnings(result)
        assert warnings and "2" in warnings[0]  # 计数告警（不中断链）

    async def test_row_limit_exceeded_fails_fast(self):
        rows = [{"id": 1, "tags": ["a", "b", "c"]}]
        result = await _run_op(
            "explode", {"explode_field": "tags", "max_rows": 2}, rows
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_ROW_LIMIT_EXCEEDED

    async def test_max_rows_boundary_allowed(self):
        rows = [{"id": 1, "tags": ["a", "b"]}]
        result = await _run_op(
            "explode", {"explode_field": "tags", "max_rows": 2}, rows
        )

        assert result.success is True
        assert len(result.output_value) == 2

    async def test_missing_explode_field_rejected(self):
        result = await _run_op("explode", {}, ROWS)

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS


# ---------------------------------------------------------------------------
# 链式组合（结构化操作串行顺序回归）
# ---------------------------------------------------------------------------


class TestStructuredChainComposition:
    async def test_sort_then_row_number_chain(self):
        config = DataTransformNodeConfiguration(
            operation=DataTransformOperation.SORT,
            input_dataset_reference="raw_data",
            operations=[
                {"operation": "sort", "params": {"sort_keys": ["value"], "sort_orders": ["desc"]}},
                {"operation": "row_number", "params": {"window_output_field": "rank"}},
            ],
        )
        executor = DataTransformNodeExecutor()
        result = await executor.execute(_make_context(config))

        assert result.success is True
        by_id = {r["id"]: r for r in result.output_value}
        assert by_id[4]["rank"] == 1  # value=40 排序后全局第 1
        assert by_id[1]["rank"] == 4

    async def test_explode_then_distinct_chain(self):
        rows = [{"id": 1, "tags": ["a", "a", "b"]}]
        config = DataTransformNodeConfiguration(
            operation=DataTransformOperation.EXPLODE,
            input_dataset_reference="raw_data",
            operations=[
                {"operation": "explode", "params": {"explode_field": "tags"}},
                {"operation": "distinct", "params": {"distinct_keys": ["tags"]}},
            ],
        )
        outputs = {"raw_data": rows}
        executor = DataTransformNodeExecutor()
        result = await executor.execute(_make_context(config, outputs))

        assert result.success is True
        assert [r["tags"] for r in result.output_value] == ["a", "b"]
