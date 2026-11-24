# =============================================================================
# DataSourceNodeExecutor execute() 单元测试
# 执行命令：pytest tests/unit/test_data_source_node_executor.py -v
#
# 根因组 B（fix_baseline_test_failures）：_make_context 构造字段过时
# （NodeDefinition 必填字段为 node_id 而非 id；SharedState 须含 instance_id），
# 已在共享辅助函数内双点修正（FR-02 / INV-14）。
# =============================================================================

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration_platform.domain.errors import NODE_DATASOURCE_UNAVAILABLE, NODE_TOOL_NOT_FOUND, DomainError
from orchestration_platform.domain.models.enums import DataSourceType, NodeType
from orchestration_platform.domain.models.node_configurations import DataSourceNodeConfiguration
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes.base import NodeExecutionContext
from orchestration_platform.engine.nodes.data_source_node_executor import DataSourceNodeExecutor
from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceConnectionConfig,
    DataSourceReadResult,
)


def run_coro(coro):
    """在同步测试中驱动协程。"""
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_context(config: DataSourceNodeConfiguration) -> NodeExecutionContext:
    """构造执行上下文。"""
    node_def = NodeDefinition(
        node_id="node-1",
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
    """构造数据源节点配置。"""
    defaults = {"connection_reference": "ds-1", "query": "SELECT 1"}
    defaults.update(kwargs)
    return DataSourceNodeConfiguration(**defaults)


class TestExecuteSuccessPath:
    """AC-1/AC-11/AC-12/AC-15：成功路径。"""

    def test_csv_query_returns_rows(self):
        """source_type=CSV + mock 连接器返回数据 → success=True，输出原始数据集列表。"""
        mock_connector = MagicMock()
        mock_connector.read = AsyncMock(return_value=DataSourceReadResult(data=[{"a": 1}, {"b": 2}]))
        mock_registry = MagicMock()
        mock_registry.get_connector = MagicMock(return_value=mock_connector)

        executor = DataSourceNodeExecutor(connector_registry=mock_registry)
        config = _make_config(source_type=DataSourceType.CSV, file_path="/data/test.csv")
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is True
        assert result.output_value == [{"a": 1}, {"b": 2}]

    def test_empty_result_success(self):
        """AC-12：空结果 → success=True，输出空数据集。"""
        mock_connector = MagicMock()
        mock_connector.read = AsyncMock(return_value=DataSourceReadResult(data=[]))
        mock_registry = MagicMock()
        mock_registry.get_connector = MagicMock(return_value=mock_connector)

        executor = DataSourceNodeExecutor(connector_registry=mock_registry)
        config = _make_config(source_type=DataSourceType.JSON, file_path="/data/test.json")
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is True
        assert result.output_value == []

    def test_metadata_only_connection_reference(self):
        """AC-15：metadata 仅含 connection_reference，不含连接参数明细。"""
        mock_connector = MagicMock()
        mock_connector.read = AsyncMock(return_value=DataSourceReadResult(data=[]))
        mock_registry = MagicMock()
        mock_registry.get_connector = MagicMock(return_value=mock_connector)

        executor = DataSourceNodeExecutor(connector_registry=mock_registry)
        config = _make_config(
            source_type=DataSourceType.DATABASE,
            connection_string="postgres://user:pass@host/db",
        )
        result = run_coro(executor.execute(_make_context(config)))

        assert result.metadata == {"connection_reference": "ds-1"}
        assert "connection_string" not in result.metadata


class TestExecuteFailurePaths:
    """AC-2/AC-3/AC-4/AC-5/AC-13：三种失败场景 + 防御性。"""

    def test_source_type_none(self):
        """AC-3：source_type 缺失 → 明确错误。"""
        executor = DataSourceNodeExecutor(connector_registry=MagicMock())
        config = _make_config()
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is False
        assert result.error_code == NODE_DATASOURCE_UNAVAILABLE
        assert "未指定" in result.error_message

    def test_connector_not_registered(self):
        """AC-2/DC-5：source_type=CRM（未注册）→ 连接器未注册错误。"""
        mock_registry = MagicMock()
        mock_registry.get_connector = MagicMock(side_effect=DomainError(NODE_TOOL_NOT_FOUND, "未注册"))

        executor = DataSourceNodeExecutor(connector_registry=mock_registry)
        config = _make_config(source_type=DataSourceType.CRM)
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is False
        assert result.error_code == NODE_DATASOURCE_UNAVAILABLE
        assert "未注册" in result.error_message

    def test_registry_not_injected(self):
        """AC-5：connector_registry=None → 注册表未注入错误。"""
        executor = DataSourceNodeExecutor()
        config = _make_config(source_type=DataSourceType.CSV)
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is False
        assert "未注入" in result.error_message

    def test_domain_error_from_connector(self):
        """AC-13：连接器抛 DomainError → 失败 + 诊断文本（去除凭据）。"""
        mock_connector = MagicMock()
        mock_connector.read = AsyncMock(
            side_effect=DomainError(NODE_DATASOURCE_UNAVAILABLE, "连接失败：connection_string 被拒绝")
        )
        mock_registry = MagicMock()
        mock_registry.get_connector = MagicMock(return_value=mock_connector)

        executor = DataSourceNodeExecutor(connector_registry=mock_registry)
        config = _make_config(source_type=DataSourceType.DATABASE, connection_string="postgres://host/db")
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is False
        assert result.error_code == NODE_DATASOURCE_UNAVAILABLE
        assert "connection_string" not in result.error_message

    def test_unexpected_exception_from_connector(self):
        """AC-13：连接器抛未预期异常 → 失败 + 通用信息。"""
        mock_connector = MagicMock()
        mock_connector.read = AsyncMock(side_effect=RuntimeError("internal error"))
        mock_registry = MagicMock()
        mock_registry.get_connector = MagicMock(return_value=mock_connector)

        executor = DataSourceNodeExecutor(connector_registry=mock_registry)
        config = _make_config(source_type=DataSourceType.CSV, file_path="/data/test.csv")
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is False
        assert result.error_message == "数据源查询失败"

    def test_three_failures_distinguishable(self):
        """AC-4：三种失败错误信息互斥可区分。"""
        # 1. 未指定
        executor = DataSourceNodeExecutor(connector_registry=MagicMock())
        r1 = run_coro(executor.execute(_make_context(_make_config())))
        # 2. 未注册
        mock_reg = MagicMock()
        mock_reg.get_connector = MagicMock(side_effect=DomainError(NODE_TOOL_NOT_FOUND, "x"))
        executor2 = DataSourceNodeExecutor(connector_registry=mock_reg)
        r2 = run_coro(executor2.execute(_make_context(_make_config(source_type=DataSourceType.CRM))))
        # 3. 查询失败
        mock_conn = MagicMock()
        mock_conn.read = AsyncMock(side_effect=RuntimeError("x"))
        mock_reg2 = MagicMock()
        mock_reg2.get_connector = MagicMock(return_value=mock_conn)
        executor3 = DataSourceNodeExecutor(connector_registry=mock_reg2)
        r3 = run_coro(executor3.execute(_make_context(_make_config(source_type=DataSourceType.CSV))))

        msgs = {r1.error_message, r2.error_message, r3.error_message}
        assert len(msgs) == 3


class TestPrivateMethods:
    """AC-9/AC-15：接口适配私有方法。"""

    def test_build_connection_config_field_mapping(self):
        """AC-9：字段映射与 DataSourceConnectionConfig 一一对应。"""
        executor = DataSourceNodeExecutor()
        config = _make_config(
            source_type=DataSourceType.DATABASE,
            connection_string="postgres://host/db",
            kafka_brokers="localhost:9092",
            spark_master_url="spark://localhost:7077",
        )
        cc = executor._build_connection_config(config)

        assert cc.source_type == DataSourceType.DATABASE
        assert cc.connection_reference == "ds-1"
        assert cc.connection_string == "postgres://host/db"
        assert cc.kafka_brokers == "localhost:9092"
        assert cc.spark_master_url == "spark://localhost:7077"

    def test_build_read_request_defaults(self):
        """分页默认值 offset=0, limit=100, streaming=False。"""
        executor = DataSourceNodeExecutor()
        cc = DataSourceConnectionConfig(
            source_type=DataSourceType.CSV,
            connection_reference="ds-1",
        )
        req = executor._build_read_request(cc)

        assert req.pagination.offset == 0
        assert req.pagination.limit == 100
        assert req.streaming is False

    def test_convert_result(self):
        """data → 原始数据集列表（dataset 类别契约）。"""
        executor = DataSourceNodeExecutor()
        read_result = DataSourceReadResult(data=[{"x": 1}, {"y": 2}, {"z": 3}])
        nr = executor._convert_result(read_result, "output", "ds-1")

        assert nr.success is True
        assert nr.output_name == "output"
        assert nr.output_value == [{"x": 1}, {"y": 2}, {"z": 3}]

    def test_sanitize_error_masks_credentials(self):
        """AC-15：凭据字段值替换为掩码。"""
        executor = DataSourceNodeExecutor()
        err = DomainError(NODE_DATASOURCE_UNAVAILABLE, "连接失败：connection_string 无效")
        msg = executor._sanitize_error_message(err)

        assert "connection_string" not in msg
        assert "***" in msg

    def test_sanitize_unexpected_exception(self):
        """未预期异常 → 通用信息。"""
        executor = DataSourceNodeExecutor()
        msg = executor._sanitize_error_message(RuntimeError("internal"))

        assert msg == "数据源查询失败"