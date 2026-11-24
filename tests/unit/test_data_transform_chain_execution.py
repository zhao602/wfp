# =============================================================================
# 数据转换链式执行单元测试（tests/unit/test_data_transform_chain_execution.py）
#
# 覆盖验收：expand_data_transform_ops T2.5（CHG-B15 / DC-02 / OP-12）
# 覆盖不变式：INV-01（接口签名不变）、INV-09（主/副装配语义不变）、
#             INV-13（既有四操作行为等价回归）、INV-14（不修改求值器）、
#             INV-15（链步数上限）
# =============================================================================

from typing import Any, Dict, List, Optional


from orchestration_platform.domain.errors import (
    DATA_TRANSFORM_CHAIN_TOO_LONG,
    DATA_TRANSFORM_UNKNOWN_OPERATION,
    NODE_INPUT_REFERENCE_MISSING,
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


def _legacy_config(**overrides: Any) -> DataTransformNodeConfiguration:
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
# 主输入装配四态（既有语义保持，INV-09）
# ---------------------------------------------------------------------------


class TestMainInputAssembly:
    async def test_missing_reference_fails_fast(self):
        result = await _run(
            _legacy_config(), outputs={"other": ROWS}
        )

        assert result.success is False
        assert result.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "node-transform-1" in result.error_message

    async def test_non_list_reference_fails_type_mismatch(self):
        result = await _run(
            _legacy_config(), outputs={"raw_data": {"not": "a list"}}
        )

        assert result.success is False
        assert result.error_code == NODE_INPUT_TYPE_MISMATCH

    async def test_empty_list_assembles_empty_dataset(self):
        result = await _run(_legacy_config(), outputs={"raw_data": []})

        assert result.success is True
        assert result.output_value == []


# ---------------------------------------------------------------------------
# 链式执行：串行顺序（上一步输出 = 下一步输入）
# ---------------------------------------------------------------------------


class TestChainSerialOrder:
    async def test_two_step_chain_passes_output_forward(self):
        """select 后 drop：验证串行顺序（B3 操作到位前的等价链：filter True + aggregate）。"""
        config = DataTransformNodeConfiguration(
            operation=DataTransformOperation.AGGREGATE,
            input_dataset_reference="raw_data",
            operations=[
                {
                    "operation": "filter",
                    "params": {"filter_condition": "True"},
                },
                {
                    "operation": "aggregate",
                    "params": {
                        "group_by": ["category"],
                        "aggregate_function": "sum",
                        "aggregate_field": "value",
                    },
                },
            ],
        )
        result = await _run(config)

        assert result.success is True
        rows = result.output_value
        assert rows is not None
        # 第二步聚合以第一步过滤输出为输入（4 行 → 2 组）
        assert len(rows) == 2
        by_category = {r["category"]: r for r in rows}
        assert by_category["a"]["sum_value"] == 40
        assert by_category["b"]["sum_value"] == 60

    async def test_legacy_single_operation_equivalent_to_chain_len_one(self):
        legacy = _legacy_config(
            operation=DataTransformOperation.AGGREGATE,
            params={
                "group_by": ["category"],
                "aggregate_function": "sum",
                "aggregate_field": "value",
            },
        )
        single_step = DataTransformNodeConfiguration(
            operation=DataTransformOperation.AGGREGATE,
            input_dataset_reference="raw_data",
            operations=[
                {
                    "operation": "aggregate",
                    "params": {
                        "group_by": ["category"],
                        "aggregate_function": "sum",
                        "aggregate_field": "value",
                    },
                }
            ],
        )

        legacy_result = await _run(legacy)
        step_result = await _run(single_step)

        assert legacy_result.success and step_result.success
        assert legacy_result.output_value == step_result.output_value


# ---------------------------------------------------------------------------
# fail-fast 三类（链过长 / 未知操作 / 步失败）+ 步骤定位
# ---------------------------------------------------------------------------


class TestFailFastBranches:
    async def test_chain_too_long_fails_fast(self):
        steps = [
            {"operation": "filter", "params": {"filter_condition": "True"}}
            for _ in range(21)
        ]
        config = DataTransformNodeConfiguration(
            operation=DataTransformOperation.FILTER,
            input_dataset_reference="raw_data",
            operations=steps,
        )
        result = await _run(config)

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_CHAIN_TOO_LONG
        assert "21" in (result.error_message or "")
        assert "20" in (result.error_message or "")

    async def test_unknown_operation_fails_fast_with_step_positioning(self):
        from orchestration_platform.engine.nodes.data_transform_ops import (
            OPERATION_REGISTRY,
        )

        # 摘除真实 FILTER 注册走未知操作分支，finally 原样恢复（不得污染其他
        # 测试可见的注册表状态——B5 起 SAMPLING 等真实注册共存，严禁误删）
        original_filter = OPERATION_REGISTRY.pop(DataTransformOperation.FILTER)
        try:
            config = DataTransformNodeConfiguration(
                operation=DataTransformOperation.FILTER,
                input_dataset_reference="raw_data",
                operations=[
                    {"operation": "filter", "params": {"filter_condition": "True"}},
                ],
            )
            result = await _run(config)

            assert result.success is False
            assert result.error_code == DATA_TRANSFORM_UNKNOWN_OPERATION
            assert "步骤 #0（filter）" in (result.error_message or "")
            assert "node-transform-1" in (result.error_message or "")
        finally:
            OPERATION_REGISTRY[DataTransformOperation.FILTER] = original_filter

    async def test_step_failure_propagates_with_positioning(self):
        """join 副引用缺失 → 透传装配错误码并带步骤定位（T2.4 验收）。"""
        config = DataTransformNodeConfiguration(
            operation=DataTransformOperation.JOIN,
            input_dataset_reference="raw_data",
            operations=[
                {"operation": "filter", "params": {"filter_condition": "True"}},
                {
                    "operation": "join",
                    "params": {
                        "join_type": "left",
                        "join_key": "id",
                        "right_dataset_reference": "missing_right",
                    },
                },
            ],
        )
        result = await _run(config, outputs={"raw_data": ROWS})

        assert result.success is False
        assert result.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "步骤 #1（join）" in (result.error_message or "")

    async def test_join_right_non_list_propagates_type_mismatch(self):
        config = _legacy_config(
            operation=DataTransformOperation.JOIN,
            params={
                "join_type": "inner",
                "join_key": "id",
                "right_dataset_reference": "bad_right",
            },
        )
        result = await _run(config, outputs={"raw_data": ROWS, "bad_right": {"x": 1}})

        assert result.success is False
        assert result.error_code == NODE_INPUT_TYPE_MISMATCH


# ---------------------------------------------------------------------------
# 既有四操作行为等价回归（INV-13 / OP-08 现状保持）
# ---------------------------------------------------------------------------


class TestExistingOperationsRegression:
    async def test_filter_true_keeps_all_rows(self):
        """预制 True 值：字面量表达式可求值，全部保留（OP-08 现状语义）。"""
        result = await _run(
            _legacy_config(params={"filter_condition": "True"})
        )

        assert result.success is True
        assert result.output_value == ROWS
        assert result.metadata.get("warnings") is None

    async def test_filter_false_drops_all_rows(self):
        result = await _run(
            _legacy_config(params={"filter_condition": "False"})
        )

        assert result.success is True
        assert result.output_value == []

    async def test_filter_with_variable_reference_drops_rows_with_warning(self):
        """OP-08 现状：变量引用必失败 → 行被剔除；差异仅为失败不再静默（计数告警）。"""
        result = await _run(
            _legacy_config(params={"filter_condition": "value >= 20"})
        )

        assert result.success is True
        assert result.output_value == []
        warnings = result.metadata.get("warnings")
        assert isinstance(warnings, list) and warnings

    async def test_filter_without_condition_passes_through(self):
        result = await _run(_legacy_config(params={}))

        assert result.success is True
        assert result.output_value == ROWS

    async def test_map_without_expression_passes_through(self):
        result = await _run(
            _legacy_config(operation=DataTransformOperation.MAP, params={})
        )

        assert result.success is True
        assert result.output_value == ROWS

    async def test_map_with_expression_keeps_original_rows(self):
        """OP-08 现状：map 求值非 dict → 保留原行（结果非对象计数告警）。"""
        result = await _run(
            _legacy_config(
                operation=DataTransformOperation.MAP,
                params={"map_expression": "True"},
            )
        )

        assert result.success is True
        assert result.output_value == ROWS
        warnings = result.metadata.get("warnings")
        assert isinstance(warnings, list) and warnings

    async def test_aggregate_sum_by_category(self):
        result = await _run(
            _legacy_config(
                operation=DataTransformOperation.AGGREGATE,
                params={
                    "group_by": ["category"],
                    "aggregate_function": "sum",
                    "aggregate_field": "value",
                },
            )
        )

        assert result.success is True
        rows = result.output_value
        assert rows is not None
        by_category = {r["category"]: r for r in rows}
        assert by_category["a"]["sum_value"] == 40
        assert by_category["a"]["count"] == 2
        assert by_category["b"]["sum_value"] == 60

    async def test_aggregate_without_function_passes_through(self):
        result = await _run(
            _legacy_config(operation=DataTransformOperation.AGGREGATE, params={})
        )

        assert result.success is True
        assert result.output_value == ROWS

    async def test_join_left_single_key(self):
        right_rows = [
            {"id": 1, "label": "一"},
            {"id": 3, "label": "三"},
            {"id": 99, "label": "九九"},
        ]
        result = await _run(
            _legacy_config(
                operation=DataTransformOperation.JOIN,
                params={
                    "join_type": "left",
                    "join_key": "id",
                    "right_dataset_reference": "right_ds",
                },
            ),
            outputs={"raw_data": ROWS, "right_ds": right_rows},
        )

        assert result.success is True
        rows = result.output_value
        assert rows is not None
        assert len(rows) == 4  # 左连接：左行全保留
        by_id = {r["id"]: r for r in rows}
        assert by_id[1]["right_label"] == "一"
        assert by_id[3]["right_label"] == "三"
        assert "right_label" not in by_id[2]
        assert "right_label" not in by_id[4]

    async def test_join_inner_single_key_drops_unmatched(self):
        right_rows = [{"id": 1, "label": "一"}]
        result = await _run(
            _legacy_config(
                operation=DataTransformOperation.JOIN,
                params={
                    "join_type": "inner",
                    "join_key": "id",
                    "right_dataset_reference": "right_ds",
                },
            ),
            outputs={"raw_data": ROWS, "right_ds": right_rows},
        )

        assert result.success is True
        rows = result.output_value
        assert rows is not None
        assert len(rows) == 1
        assert rows[0]["id"] == 1
        assert rows[0]["right_label"] == "一"

    async def test_join_full_appends_unmatched_right_rows(self):
        right_rows = [
            {"id": 1, "label": "一"},
            {"id": 77, "label": "七七"},
        ]
        result = await _run(
            _legacy_config(
                operation=DataTransformOperation.JOIN,
                params={
                    "join_type": "full",
                    "join_key": "id",
                    "right_dataset_reference": "right_ds",
                },
            ),
            outputs={"raw_data": ROWS, "right_ds": right_rows},
        )

        assert result.success is True
        rows = result.output_value
        assert rows is not None
        assert len(rows) == 5  # 4 左行全保留 + 1 未匹配右行
        assert any(r.get("right_id") == 77 for r in rows)

    async def test_join_missing_key_params_passes_through(self):
        """既有口径：join_key/right_ref 缺失 → 透传左表（无操作）。"""
        result = await _run(
            _legacy_config(operation=DataTransformOperation.JOIN, params={})
        )

        assert result.success is True
        assert result.output_value == ROWS
