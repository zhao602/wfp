# =============================================================================
# 数据源节点分页循环累积测试（tests/unit/test_data_source_cursor_pagination.py）
#
# 覆盖验收：roadmap_b12_data_chunking T4（B12-D1.1/D1.2/D1.1a）
#   1. 真实 CSV 连接器多页累积（首轮 100 + 次轮 5000 页大小提升）；
#   2. 硬上限截断 + 截断告警（常量 monkeypatch 缩小构造超限）；
#   3. 软阈值条件触发（metadata 键存在性；未触发时精确等值保持）；
#   4. 空页保护（恰满页后空页终止）；
#   5. mock 单轮兼容（既有 mock 行为回放，C3）；
#   6. 游标型连接器单轮（kafka 现状非请求寻址，D1.1a-2）；
#   7. metadata 空结果精确等值（C2/AC-15）。
# =============================================================================

import csv
from unittest.mock import AsyncMock, MagicMock

from orchestration_platform.domain.models.enums import DataSourceType, NodeType
from orchestration_platform.domain.models.node_configurations import (
    DataSourceNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes.base import NodeExecutionContext
from orchestration_platform.engine.nodes import data_source_node_executor as ds_mod
from orchestration_platform.engine.nodes.data_source_node_executor import (
    DataSourceNodeExecutor,
)
from orchestration_platform.integrations.data_source_connectors.connector_registry import (
    DataSourceConnectorRegistry,
)
from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceReadResult,
)


ROW_COUNT = 105  # 首轮 100 + 次轮 5


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "value"])
        writer.writeheader()
        writer.writerows(rows)


def _make_context(config: DataSourceNodeConfiguration) -> NodeExecutionContext:
    node_def = NodeDefinition(
        node_id="node-ds-1",
        name="read_data",
        node_type=NodeType.DATA_SOURCE,
        configuration=config,
        output_name="dataset",
    )
    return NodeExecutionContext(
        node_definition=node_def,
        shared_state=SharedState(instance_id="inst-1"),
        instance_id="inst-1",
        trace_id="trace-1",
    )


def _make_config(**kwargs) -> DataSourceNodeConfiguration:
    defaults = {"connection_reference": "ds-1", "query": "SELECT 1"}
    defaults.update(kwargs)
    return DataSourceNodeConfiguration(**defaults)


def _real_registry():
    return DataSourceConnectorRegistry()


class TestMultiPageAccumulation:
    async def test_105_row_csv_accumulates_all_rows(self, tmp_path):
        """105 行 CSV 经真实连接器 → 全量 105 行输出（次轮页大小提升）。"""
        csv_path = tmp_path / "data.csv"
        _write_csv(str(csv_path), [{"id": i, "value": i * 2} for i in range(ROW_COUNT)])

        executor = DataSourceNodeExecutor(connector_registry=_real_registry())
        config = _make_config(
            source_type=DataSourceType.CSV, file_path=str(csv_path)
        )
        result = await executor.execute(_make_context(config))

        assert result.success is True
        assert len(result.output_value) == ROW_COUNT
        assert result.output_value[0] == {"id": "0", "value": "0"}
        assert result.output_value[104] == {"id": "104", "value": "208"}
        # 未达阈值：metadata 精确等值保持（C2）
        assert result.metadata == {"connection_reference": "ds-1"}

    async def test_hard_cap_truncates_and_warns(self, tmp_path, monkeypatch):
        """硬上限（monkeypatch=100）截断至 100 行 + 截断告警入 metadata。"""
        monkeypatch.setattr(ds_mod, "DATA_SOURCE_MAX_ROWS", 100)
        monkeypatch.setattr(ds_mod, "DATA_SOURCE_WARN_ROWS", 50)
        csv_path = tmp_path / "data.csv"
        _write_csv(str(csv_path), [{"id": i, "value": i} for i in range(ROW_COUNT)])

        executor = DataSourceNodeExecutor(connector_registry=_real_registry())
        config = _make_config(
            source_type=DataSourceType.CSV, file_path=str(csv_path)
        )
        result = await executor.execute(_make_context(config))

        assert result.success is True
        assert len(result.output_value) == 100
        assert result.metadata["row_count"] == 100
        assert "已截断" in result.metadata["row_count_warning"]
        assert "200_000" not in result.metadata["row_count_warning"]

    async def test_soft_threshold_conditional_metadata(self, tmp_path, monkeypatch):
        """软阈值（monkeypatch=50）触发 row_count/row_count_warning 条件键。"""
        monkeypatch.setattr(ds_mod, "DATA_SOURCE_WARN_ROWS", 50)
        csv_path = tmp_path / "data.csv"
        _write_csv(str(csv_path), [{"id": i, "value": i} for i in range(60)])

        executor = DataSourceNodeExecutor(connector_registry=_real_registry())
        config = _make_config(
            source_type=DataSourceType.CSV, file_path=str(csv_path)
        )
        result = await executor.execute(_make_context(config))

        assert result.success is True
        assert len(result.output_value) == 60
        assert result.metadata["row_count"] == 60
        assert "告警阈值" in result.metadata["row_count_warning"]
        assert "connection_reference" in result.metadata  # 既有键不丢失


class TestLoopTermination:
    async def test_empty_page_protection(self):
        """恰满页后空页 → 空页保护终止，不重复不遗漏。"""
        page = DataSourceReadResult(
            data=[{"i": k} for k in range(100)],
            total_count=100,
            has_more=True,
        )
        empty = DataSourceReadResult(data=[], total_count=100, has_more=False)
        mock_connector = MagicMock()
        mock_connector.read = AsyncMock(side_effect=[page, empty])
        mock_registry = MagicMock()
        mock_registry.get_connector = MagicMock(return_value=mock_connector)

        executor = DataSourceNodeExecutor(connector_registry=mock_registry)
        config = _make_config(source_type=DataSourceType.CSV, file_path="/x.csv")
        result = await executor.execute(_make_context(config))

        assert result.success is True
        assert len(result.output_value) == 100
        assert mock_connector.read.await_count == 2

    async def test_has_more_false_single_round(self):
        """连接器声明无更多（rest/spark/flink 语义）→ 单轮，不重复取数。"""
        rows = [{"i": k} for k in range(150)]  # 超 limit 但 has_more=False
        mock_connector = MagicMock()
        mock_connector.read = AsyncMock(
            return_value=DataSourceReadResult(data=rows, has_more=False)
        )
        mock_registry = MagicMock()
        mock_registry.get_connector = MagicMock(return_value=mock_connector)

        executor = DataSourceNodeExecutor(connector_registry=mock_registry)
        config = _make_config(source_type=DataSourceType.CSV, file_path="/x.csv")
        result = await executor.execute(_make_context(config))

        assert result.success is True
        assert result.output_value == rows
        assert mock_connector.read.await_count == 1  # D1.1a-1：不重复取数

    async def test_cursor_connector_single_round(self):
        """游标型（kafka 语义：next_cursor 回显）→ 单轮保持既有行为（D1.1a-2）。"""
        rows = [{"i": k} for k in range(100)]
        mock_connector = MagicMock()
        mock_connector.read = AsyncMock(
            return_value=DataSourceReadResult(
                data=rows, has_more=True, next_cursor="100"
            )
        )
        mock_registry = MagicMock()
        mock_registry.get_connector = MagicMock(return_value=mock_connector)

        executor = DataSourceNodeExecutor(connector_registry=mock_registry)
        config = _make_config(
            source_type=DataSourceType.KAFKA, kafka_brokers="b:9092"
        )
        result = await executor.execute(_make_context(config))

        assert result.success is True
        assert result.output_value == rows
        assert mock_connector.read.await_count == 1  # 不推进游标（防重复消费）

    async def test_mock_single_round_passthrough(self):
        """既有 mock 行为回放（C3）：小数据 + 默认 has_more=False → 单轮透传。"""
        mock_connector = MagicMock()
        mock_connector.read = AsyncMock(
            return_value=DataSourceReadResult(data=[{"a": 1}, {"b": 2}])
        )
        mock_registry = MagicMock()
        mock_registry.get_connector = MagicMock(return_value=mock_connector)

        executor = DataSourceNodeExecutor(connector_registry=mock_registry)
        config = _make_config(source_type=DataSourceType.CSV, file_path="/x.csv")
        result = await executor.execute(_make_context(config))

        assert result.success is True
        assert result.output_value == [{"a": 1}, {"b": 2}]
        assert result.metadata == {"connection_reference": "ds-1"}
        assert mock_connector.read.await_count == 1

    async def test_empty_result_metadata_exact_equality(self):
        """空结果 metadata 精确等值（C2/AC-15）：告警键条件触发不误伤。"""
        mock_connector = MagicMock()
        mock_connector.read = AsyncMock(return_value=DataSourceReadResult(data=[]))
        mock_registry = MagicMock()
        mock_registry.get_connector = MagicMock(return_value=mock_connector)

        executor = DataSourceNodeExecutor(connector_registry=mock_registry)
        config = _make_config(source_type=DataSourceType.JSON, file_path="/x.json")
        result = await executor.execute(_make_context(config))

        assert result.success is True
        assert result.output_value == []
        assert result.metadata == {"connection_reference": "ds-1"}


class TestPaginationConstants:
    def test_thresholds_pinned_to_spec(self):
        """守卫常量与 spec 定稿口径一致（200k 硬 / 100k 软 / 5k 页）。"""
        assert ds_mod.DATA_SOURCE_MAX_ROWS == 200_000
        assert ds_mod.DATA_SOURCE_WARN_ROWS == 100_000
        assert ds_mod.DATA_SOURCE_PAGE_SIZE == 5_000
