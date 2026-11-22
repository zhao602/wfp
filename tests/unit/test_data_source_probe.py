# =============================================================================
# 数据源连接器 probe() 单元测试（tests/unit/test_data_source_probe.py）
#
# 覆盖 8 类连接器的 probe() 方法：参数错误 / 连接失败 / 无法探测 / 文件类 / rest_api HEAD / kafka 双探针。
# 执行命令：pytest tests/unit/test_data_source_probe.py -v
# 约束：不依赖真实外网与真实驱动（R-2），用桩件/monkeypatch。
# =============================================================================

import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration_platform.domain.errors import (
    DATASOURCE_CONNECTION_FAILED,
    DATASOURCE_PROBE_PARAM_INVALID,
    DATASOURCE_PROBE_UNAVAILABLE,
    DomainError,
)
from orchestration_platform.domain.models.enums import DataSourceType
from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceConnectionConfig,
)
from orchestration_platform.integrations.data_source_connectors.database_connector import (
    DatabaseDataSourceConnector,
)
from orchestration_platform.integrations.data_source_connectors.rest_api_connector import (
    RestApiDataSourceConnector,
)
from orchestration_platform.integrations.data_source_connectors.csv_connector import (
    CsvDataSourceConnector,
)
from orchestration_platform.integrations.data_source_connectors.json_connector import (
    JsonDataSourceConnector,
)
from orchestration_platform.integrations.data_source_connectors.kafka_connector import (
    KafkaDataSourceConnector,
)
from orchestration_platform.integrations.data_source_connectors.flink_connector import (
    FlinkDataSourceConnector,
)
from orchestration_platform.integrations.data_source_connectors.spark_connector import (
    SparkDataSourceConnector,
)


def run_coro(coro):
    """在同步测试中驱动协程（兼容 Windows 沙箱限制）。"""
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------- Database ----------

class TestDatabaseProbe:
    """Database connector probe() 测试（AC-1 / AC-5 / AC-7）。"""

    def test_param_error_empty_connection_string(self):
        """空连接串 → DATASOURCE_PROBE_PARAM_INVALID。"""
        connector = DatabaseDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.DATABASE,
            connection_reference="db-1",
        )
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_PROBE_PARAM_INVALID

    def test_param_error_invalid_format(self):
        """非法连接串格式 → DATASOURCE_PROBE_PARAM_INVALID。"""
        connector = DatabaseDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.DATABASE,
            connection_reference="db-1",
            connection_string="abc",
        )
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_PROBE_PARAM_INVALID

    def test_unavailable_no_driver(self):
        """asyncpg 缺失 → DATASOURCE_PROBE_UNAVAILABLE。"""
        connector = DatabaseDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.DATABASE,
            connection_reference="db-1",
            connection_string="postgres://user:pass@localhost/db",
        )
        with patch("importlib.import_module", side_effect=ImportError("no asyncpg")):
            with pytest.raises(DomainError) as exc_info:
                run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_PROBE_UNAVAILABLE

    def test_connection_failure(self):
        """驱动可用但连接被拒 → DATASOURCE_CONNECTION_FAILED。"""
        connector = DatabaseDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.DATABASE,
            connection_reference="db-1",
            connection_string="postgres://user:pass@localhost/db",
        )
        mock_asyncpg = MagicMock()
        mock_asyncpg.connect = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        with patch("importlib.import_module", return_value=mock_asyncpg):
            with pytest.raises(DomainError) as exc_info:
                run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED
        assert "secret" not in exc_info.value.message
        assert "pass" not in exc_info.value.message


# ---------- REST API ----------

class TestRestApiProbe:
    """REST API connector probe() 测试（AC-2 / AC-5 / AC-13 / Q-7）。"""

    def test_param_error_empty_url(self):
        """空 URL → DATASOURCE_PROBE_PARAM_INVALID。"""
        connector = RestApiDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.REST_API,
            connection_reference="api-1",
        )
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_PROBE_PARAM_INVALID

    def test_param_error_invalid_url(self):
        """非法 URL 格式 → DATASOURCE_PROBE_PARAM_INVALID。"""
        connector = RestApiDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.REST_API,
            connection_reference="api-1",
            url="not-a-url",
        )
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_PROBE_PARAM_INVALID

    def test_head_405_treated_as_reachable(self):
        """HEAD 405 → 正常返回（Q-7=A，可达但方法不支持）。"""
        connector = RestApiDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.REST_API,
            connection_reference="api-1",
            url="http://example.com/api",
        )
        mock_httpx = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 405
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)
        mock_httpx.Timeout = MagicMock()
        with patch("importlib.import_module", return_value=mock_httpx):
            run_coro(connector.probe(config))

    def test_head_501_treated_as_reachable(self):
        """HEAD 501 → 正常返回（Q-7=A）。"""
        connector = RestApiDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.REST_API,
            connection_reference="api-1",
            url="http://example.com/api",
        )
        mock_httpx = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 501
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)
        mock_httpx.Timeout = MagicMock()
        with patch("importlib.import_module", return_value=mock_httpx):
            run_coro(connector.probe(config))

    def test_connection_failure(self):
        """连接被拒 → DATASOURCE_CONNECTION_FAILED。"""
        connector = RestApiDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.REST_API,
            connection_reference="api-1",
            url="http://example.com/api",
        )
        mock_httpx = MagicMock()
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        mock_client.aclose = AsyncMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)
        mock_httpx.Timeout = MagicMock()
        with patch("importlib.import_module", return_value=mock_httpx):
            with pytest.raises(DomainError) as exc_info:
                run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED


# ---------- CSV ----------

class TestCsvProbe:
    """CSV connector probe() 测试（AC-6 / INV-5）。"""

    def test_file_exists_reachable(self):
        """文件存在 → 正常返回。"""
        connector = CsvDataSourceConnector()
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
            f.write("a,b\n1,2\n")
            path = f.name
        try:
            config = DataSourceConnectionConfig(
                source_type=DataSourceType.CSV,
                connection_reference="csv-1",
                file_path=path,
            )
            run_coro(connector.probe(config))
        finally:
            os.unlink(path)

    def test_file_not_exists_connection_error(self):
        """文件不存在 → DATASOURCE_CONNECTION_FAILED + 诊断含"不存在"。"""
        connector = CsvDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.CSV,
            connection_reference="csv-1",
            file_path="Z:/no/such/file.csv",
        )
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED
        assert "不存在" in exc_info.value.message

    def test_param_error_empty_path(self):
        """空路径 → DATASOURCE_PROBE_PARAM_INVALID。"""
        connector = CsvDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.CSV,
            connection_reference="csv-1",
        )
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_PROBE_PARAM_INVALID


# ---------- JSON ----------

class TestJsonProbe:
    """JSON connector probe() 测试（AC-6 / INV-5）。"""

    def test_file_exists_reachable(self):
        """文件存在 → 正常返回。"""
        connector = JsonDataSourceConnector()
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write('[{"a": 1}]')
            path = f.name
        try:
            config = DataSourceConnectionConfig(
                source_type=DataSourceType.JSON,
                connection_reference="json-1",
                file_path=path,
            )
            run_coro(connector.probe(config))
        finally:
            os.unlink(path)

    def test_file_not_exists_connection_error(self):
        """文件不存在 → DATASOURCE_CONNECTION_FAILED + 诊断含"不存在"。"""
        connector = JsonDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.JSON,
            connection_reference="json-1",
            file_path="Z:/no/such/file.json",
        )
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED
        assert "不存在" in exc_info.value.message


# ---------- Flink ----------

class TestFlinkProbe:
    """Flink connector probe() 测试（AC-3 / AC-5）。"""

    def test_param_error_empty_url(self):
        """空 URL → DATASOURCE_PROBE_PARAM_INVALID。"""
        connector = FlinkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.FLINK,
            connection_reference="flink-1",
        )
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_PROBE_PARAM_INVALID

    def test_unavailable_no_driver(self):
        """httpx 缺失 → DATASOURCE_PROBE_UNAVAILABLE。"""
        connector = FlinkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.FLINK,
            connection_reference="flink-1",
            flink_jobmanager_url="http://localhost:8081",
        )
        with patch("importlib.import_module", side_effect=ImportError("no httpx")):
            with pytest.raises(DomainError) as exc_info:
                run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_PROBE_UNAVAILABLE

    def test_connection_failure(self):
        """GET /jobs 失败 → DATASOURCE_CONNECTION_FAILED。"""
        connector = FlinkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.FLINK,
            connection_reference="flink-1",
            flink_jobmanager_url="http://localhost:8081",
        )
        mock_httpx = MagicMock()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        mock_client.aclose = AsyncMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)
        mock_httpx.Timeout = MagicMock()
        with patch("importlib.import_module", return_value=mock_httpx):
            with pytest.raises(DomainError) as exc_info:
                run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED


# ---------- Spark ----------

class TestSparkProbe:
    """Spark connector probe() 测试（AC-3 / AC-5 / Q-8）。"""

    def test_param_error_empty_master_url(self):
        """空 spark_master_url → DATASOURCE_PROBE_PARAM_INVALID。"""
        connector = SparkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.SPARK,
            connection_reference="spark-1",
        )
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_PROBE_PARAM_INVALID

    def test_unavailable_no_driver(self):
        """httpx 缺失 → DATASOURCE_PROBE_UNAVAILABLE。"""
        connector = SparkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.SPARK,
            connection_reference="spark-1",
            spark_master_url="spark://localhost:7077",
        )
        with patch("importlib.import_module", side_effect=ImportError("no httpx")):
            with pytest.raises(DomainError) as exc_info:
                run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_PROBE_UNAVAILABLE

    def test_connection_failure(self):
        """GET /sessions 失败 → DATASOURCE_CONNECTION_FAILED。"""
        connector = SparkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.SPARK,
            connection_reference="spark-1",
            spark_master_url="spark://localhost:7077",
        )
        mock_httpx = MagicMock()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        mock_client.aclose = AsyncMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)
        mock_httpx.Timeout = MagicMock()
        with patch("importlib.import_module", return_value=mock_httpx):
            with pytest.raises(DomainError) as exc_info:
                run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED


# ---------- Kafka 双探针（7.4） ----------

class TestKafkaProbeDualLevel:
    """Kafka connector probe() 双探针测试（Q-6 / DEV-1）。

    liveness：仅验 broker 连通；
    readiness：额外验 topic 存在。
    """

    def _make_config(self):
        return DataSourceConnectionConfig(
            source_type=DataSourceType.KAFKA,
            connection_reference="kafka-1",
            kafka_brokers="localhost:9092",
            kafka_topic="test-topic",
        )

    def test_param_error_empty_brokers(self):
        """空 broker → DATASOURCE_PROBE_PARAM_INVALID。"""
        connector = KafkaDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.KAFKA,
            connection_reference="kafka-1",
            kafka_topic="test-topic",
        )
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.probe(config))
        assert exc_info.value.code == DATASOURCE_PROBE_PARAM_INVALID

    def test_unavailable_no_driver(self):
        """aiokafka 缺失 → DATASOURCE_PROBE_UNAVAILABLE。"""
        connector = KafkaDataSourceConnector()
        with patch("importlib.import_module", side_effect=ImportError("no aiokafka")):
            with pytest.raises(DomainError) as exc_info:
                run_coro(connector.probe(self._make_config()))
        assert exc_info.value.code == DATASOURCE_PROBE_UNAVAILABLE

    def test_liveness_broker_reachable_topic_not_checked(self):
        """liveness：broker 连通即可，topic 不存在仍返回正常。"""
        connector = KafkaDataSourceConnector()
        mock_aiokafka = MagicMock()
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.partitions_for_topic = MagicMock(return_value=None)
        mock_consumer.stop = AsyncMock()
        mock_aiokafka.AIOKafkaConsumer = MagicMock(return_value=mock_consumer)
        with patch("importlib.import_module", return_value=mock_aiokafka):
            run_coro(connector.probe(self._make_config(), level="liveness"))

    def test_readiness_topic_not_found_raises(self):
        """readiness：topic 不存在 → DATASOURCE_CONNECTION_FAILED。"""
        connector = KafkaDataSourceConnector()
        mock_aiokafka = MagicMock()
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.partitions_for_topic = MagicMock(return_value=None)
        mock_consumer.stop = AsyncMock()
        mock_aiokafka.AIOKafkaConsumer = MagicMock(return_value=mock_consumer)
        with patch("importlib.import_module", return_value=mock_aiokafka):
            with pytest.raises(DomainError) as exc_info:
                run_coro(connector.probe(self._make_config(), level="readiness"))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED
        assert "不存在" in exc_info.value.message

    def test_readiness_topic_exists_reachable(self):
        """readiness：topic 存在 → 正常返回。"""
        connector = KafkaDataSourceConnector()
        mock_aiokafka = MagicMock()
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.partitions_for_topic = MagicMock(return_value={0, 1})
        mock_consumer.stop = AsyncMock()
        mock_aiokafka.AIOKafkaConsumer = MagicMock(return_value=mock_consumer)
        with patch("importlib.import_module", return_value=mock_aiokafka):
            run_coro(connector.probe(self._make_config(), level="readiness"))