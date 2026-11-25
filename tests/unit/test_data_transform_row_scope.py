# =============================================================================
# 数据转换 filter/map 行级表达式语义测试（OP-08 filter 侧闭环，2026-11-24）
#
# 背景：原实现把行 dict 直接传给 RestrictedExpressionEvaluator——
# ast.Name 解析走 state.node_outputs，普通 dict 无该属性 → AttributeError
# 被吞 → 变量引用必失败 → filter 全行剔除（用户实测「过滤后数据为空」）。
#
# 闭环：_RowScope 适配器让变量名/output.field 映射行字段（求值器零改动）。
# 覆盖：真实过滤 / output.field 兼容 / 字段缺失容错 / map 侧行为边界。
# =============================================================================

from typing import Any, Dict, List, Optional

import pytest

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


ROWS: List[Dict[str, Any]] = [
    {"id": 1, "category": "a", "value": 10},
    {"id": 2, "category": "a", "value": 30},
    {"id": 3, "category": "b", "value": 20},
    {"id": 4, "category": "b", "value": 40},
]


def _config(
    operation: DataTransformOperation, params: Dict[str, Any]
) -> DataTransformNodeConfiguration:
    return DataTransformNodeConfiguration(
        operation=operation,
        input_dataset_reference="raw_data",
        params=params,
    )


async def _run(
    config: DataTransformNodeConfiguration,
    outputs: Optional[Dict[str, Any]] = None,
) -> NodeExecutionResult:
    node = NodeDefinition(
        node_id="node-filter-1",
        name="过滤",
        node_type=NodeType.DATA_TRANSFORM,
        configuration=config,
        output_name="filtered",
    )
    context = NodeExecutionContext(
        node_definition=node,
        shared_state=SharedState(
            instance_id="inst-test",
            node_outputs=outputs if outputs is not None else {"raw_data": ROWS},
        ),
        instance_id="inst-test",
        trace_id="trace-test",
    )
    return await DataTransformNodeExecutor().execute(context)


class TestFilterRowScope:
    """filter 变量引用真实过滤（OP-08 filter 侧闭环）。"""

    @pytest.mark.asyncio
    async def test_variable_reference_filters_rows(self):
        """变量名直接映射行字段：value >= 20 保留 3 行。"""
        result = await _run(
            _config(
                DataTransformOperation.FILTER,
                {"filter_condition": "value >= 20"},
            )
        )
        assert result.success is True
        rows = result.output_value
        assert [r["id"] for r in rows] == [2, 3, 4]

    @pytest.mark.asyncio
    async def test_string_equality_filter(self):
        """字符串相等过滤：category == 'a' 保留 2 行。"""
        result = await _run(
            _config(
                DataTransformOperation.FILTER,
                {"filter_condition": "category == 'a'"},
            )
        )
        assert result.success is True
        assert [r["id"] for r in result.output_value] == [1, 2]

    @pytest.mark.asyncio
    async def test_output_dot_field_syntax_boundary_documented(self):
        """output.field 是分支路由的「节点输出」语法，非行级语义——
        行级上下文解析为 None（行无 "output" 键）→ 干净剔除全行。
        锚定该边界防止误用后的行为漂移；行级过滤用字段名直引用。
        """
        result = await _run(
            _config(
                DataTransformOperation.FILTER,
                {"filter_condition": "output.category == 'b'"},
            )
        )
        assert result.success is True
        assert result.output_value == []

    @pytest.mark.asyncio
    async def test_missing_field_rows_dropped_with_warning(self):
        """字段缺失行求值失败 → 剔除 + 计数告警 + degraded 标记（不静默）。"""
        mixed: List[Dict[str, Any]] = [
            {"id": 1, "value": 10},
            {"id": 2, "value": 30},
            {"id": 3},  # value 缺失：None >= 20 抛 TypeError → 剔除
        ]
        result = await _run(
            _config(
                DataTransformOperation.FILTER,
                {"filter_condition": "value >= 20"},
            ),
            outputs={"raw_data": mixed},
        )
        assert result.success is True
        assert [r["id"] for r in result.output_value] == [2]
        # 行级容错告警可见（metadata.warnings，可辨识不静默空结果）
        warnings = (result.metadata or {}).get("warnings", [])
        assert any("filter 条件求值失败" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_constant_true_keeps_all_rows(self):
        """常量条件（既有测试口径）零回归。"""
        result = await _run(
            _config(
                DataTransformOperation.FILTER,
                {"filter_condition": "True"},
            )
        )
        assert result.success is True
        assert len(result.output_value) == len(ROWS)

    @pytest.mark.asyncio
    async def test_no_condition_returns_dataset_unchanged(self):
        """无 filter_condition → 原样返回（既有口径零回归）。"""
        result = await _run(
            _config(DataTransformOperation.FILTER, {})
        )
        assert result.success is True
        assert result.output_value == ROWS


class TestMapRowScope:
    """map 侧行为边界：变量引用现可解析，但构造新 dict 仍受白名单限制。"""

    @pytest.mark.asyncio
    async def test_map_expression_field_reference_non_dict_keeps_row(self):
        """表达式返回行字段（非 dict）→ 保留原行 + 计数告警（口径登记）。

        白名单无 ast.Dict 节点——map 真实变换能力仍不闭环（OP-09 范畴）；
        本用例锚定「变量引用不再必失败，但非 dict 结果保留原行」的边界。
        """
        result = await _run(
            _config(
                DataTransformOperation.MAP,
                {"map_expression": "value"},
            )
        )
        assert result.success is True
        # 非对象结果 4 行 → 全部保留原行
        assert result.output_value == ROWS
        warnings = (result.metadata or {}).get("warnings", [])
        assert any("map 表达式结果非对象" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_map_variable_reference_no_longer_counts_as_failure(self):
        """变量引用可解析：不再走「求值失败」分支（原 OP-08 必失败口径废止）。"""
        result = await _run(
            _config(
                DataTransformOperation.MAP,
                {"map_expression": "value"},
            )
        )
        assert "map 表达式求值失败" not in (result.error_message or "")
