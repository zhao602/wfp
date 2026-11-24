# =============================================================================
# 数据转换行数守卫测试（tests/unit/test_data_transform_row_guardrails.py）
#
# 覆盖验收：roadmap_b12_data_chunking T4（B12-D3.1/D3.2/D3.3）
#   1. 装配后检查：硬上限 fail-fast / 软阈值告警；
#   2. 每步输出后检查：硬限 fail-fast 保持「步骤 #i（{op}）」定位（OP-12）；
#   3. 软阈值告警入 metadata.warnings（与 degraded 告警同构通道）；
#   4. 未触发阈值时零告警（metadata 无 warnings 键）；
#   5. 错误码复用 DATA_TRANSFORM_ROW_LIMIT_EXCEEDED（D3.3 落地修正）。
# 阈值常量经 monkeypatch 缩小以轻量构造超限数据集（B12-D4 测试设计）。
# =============================================================================

from typing import Any, Dict

from orchestration_platform.domain.errors import DATA_TRANSFORM_ROW_LIMIT_EXCEEDED
from orchestration_platform.domain.models.enums import (
    DataTransformOperation,
    NodeType,
)
from orchestration_platform.domain.models.node_configurations import (
    DataTransformNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes import data_transform_node_executor as dt_mod
from orchestration_platform.engine.nodes.base import NodeExecutionContext
from orchestration_platform.engine.nodes.data_transform_node_executor import (
    DataTransformNodeExecutor,
)
from orchestration_platform.engine.nodes.data_transform_ops import (
    MAX_DATASET_ROWS,
    WARN_DATASET_ROWS,
)


def _make_context(
    config: DataTransformNodeConfiguration,
    outputs: Dict[str, Any],
) -> NodeExecutionContext:
    node_def = NodeDefinition(
        node_id="node-guard-1",
        name="数据转换",
        node_type=NodeType.DATA_TRANSFORM,
        configuration=config,
        output_name="guarded_out",
    )
    return NodeExecutionContext(
        node_definition=node_def,
        shared_state=SharedState(instance_id="inst-1", node_outputs=outputs),
        instance_id="inst-1",
        trace_id="trace-1",
    )


def _filter_config(**overrides: Any) -> DataTransformNodeConfiguration:
    payload: Dict[str, Any] = {
        "operation": DataTransformOperation.FILTER,
        "input_dataset_reference": "raw_data",
        "params": {"filter_condition": "True"},
    }
    payload.update(overrides)
    return DataTransformNodeConfiguration(**payload)


def _explode_config() -> DataTransformNodeConfiguration:
    return DataTransformNodeConfiguration(
        operation=DataTransformOperation.EXPLODE,
        input_dataset_reference="raw_data",
        params={"explode_field": "tags"},
    )


async def _run(
    config: DataTransformNodeConfiguration,
    outputs: Dict[str, Any],
) -> Any:
    executor = DataTransformNodeExecutor()
    return await executor.execute(_make_context(config, outputs))


class TestConstantsPinnedToSpec:
    def test_thresholds_match_spec(self):
        """守卫常量与 spec 定稿口径一致（200k 硬 / 100k 软）。"""
        assert MAX_DATASET_ROWS == 200_000
        assert WARN_DATASET_ROWS == 100_000


class TestAssemblyGuard:
    async def test_hard_limit_fail_fast_after_assembly(self, monkeypatch):
        """装配后超硬上限 → fail-fast ROW_LIMIT_EXCEEDED（含行数与阈值）。"""
        monkeypatch.setattr(dt_mod, "MAX_DATASET_ROWS", 10)
        rows = [{"i": k} for k in range(11)]

        result = await _run(_filter_config(), {"raw_data": rows})

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_ROW_LIMIT_EXCEEDED
        assert "输入数据集行数 11" in result.error_message
        assert "上限 10" in result.error_message
        assert "node-guard-1" in result.error_message

    async def test_soft_threshold_warns_without_failure(self, monkeypatch):
        """装配后超软阈值未超硬限 → 成功 + metadata.warnings 告警。"""
        monkeypatch.setattr(dt_mod, "WARN_DATASET_ROWS", 5)
        monkeypatch.setattr(dt_mod, "MAX_DATASET_ROWS", 10)
        rows = [{"i": k} for k in range(6)]

        result = await _run(_filter_config(), {"raw_data": rows})

        assert result.success is True
        assert len(result.output_value) == 6
        assert "数据集行数 6 超过告警阈值 5" in result.metadata["warnings"]


class TestStepOutputGuard:
    async def test_step_output_hard_limit_fail_fast(self, monkeypatch):
        """每步输出后超硬上限 → fail-fast 含「步骤 #i（{op}）」定位（OP-12）。"""
        monkeypatch.setattr(dt_mod, "MAX_DATASET_ROWS", 10)
        # 3 行 × 5 元素 explode → 15 行 > 10（explode 内部 max_rows 缺省 100000 不先触发）
        rows = [{"tags": ["a", "b", "c", "d", "e"]} for _ in range(3)]

        result = await _run(_explode_config(), {"raw_data": rows})

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_ROW_LIMIT_EXCEEDED
        assert "步骤 #0（explode）" in result.error_message
        assert "输出数据集行数 15 超过上限 10" in result.error_message

    async def test_step_output_soft_threshold_warns(self, monkeypatch):
        """每步输出超软阈值未超硬限 → 成功 + 步骤定位告警入 warnings。"""
        monkeypatch.setattr(dt_mod, "WARN_DATASET_ROWS", 5)
        monkeypatch.setattr(dt_mod, "MAX_DATASET_ROWS", 20)
        # 2 行 × 3 元素 explode → 6 行（> 5 软阈值，< 20 硬限）
        rows = [{"tags": ["a", "b", "c"]} for _ in range(2)]

        result = await _run(_explode_config(), {"raw_data": rows})

        assert result.success is True
        assert len(result.output_value) == 6
        assert any(
            "步骤 #0（explode）" in w and "告警阈值 5" in w
            for w in result.metadata["warnings"]
        )


class TestNoFalsePositives:
    async def test_below_thresholds_no_warning_key(self):
        """未触发任一阈值 → metadata 无 warnings 键（零噪声）。"""
        rows = [{"i": k} for k in range(3)]

        result = await _run(_filter_config(), {"raw_data": rows})

        assert result.success is True
        assert "warnings" not in result.metadata

    async def test_empty_dataset_passes(self):
        """空数据集 → 正常通过守卫（既有空态语义保持）。"""
        result = await _run(_filter_config(), {"raw_data": []})

        assert result.success is True
        assert result.output_value == []
        assert "warnings" not in result.metadata
