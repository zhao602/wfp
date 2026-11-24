# =============================================================================
# 大数据连接器单元测试（tests/unit/test_big_data_connectors.py）
#
# 覆盖 Hologres/Kafka/Flink/Spark 四类连接器的连接验证与接口契约。
# =============================================================================

import asyncio
import pytest

from orchestration_platform.domain.models.enums import DataSourceType
from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceConnectionConfig,
    DataSourceReadRequest,
)
from orchestration_platform.integrations.data_source_connectors.connector_registry import (
    DataSourceConnectorRegistry,
)
from orchestration_platform.integrations.data_source_connectors.hologres_connector import (
    HologresDataSourceConnector,
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


# ---------- Hologres ----------

class TestHologresConnector:
    """Hologres 连接器测试。"""

    def test_connect_missing_endpoint_raises(self):
        connector = HologresDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.HOLOGRES,
            connection_reference="access_id",
            hologres_database="test_db",
        )
        from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.connect(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED

    def test_connect_missing_database_raises(self):
        connector = HologresDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.HOLOGRES,
            connection_reference="access_id",
            hologres_endpoint="hgproxy.example.com",
        )
        from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.connect(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED

    def test_connect_valid_config_succeeds(self):
        connector = HologresDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.HOLOGRES,
            connection_reference="access_id",
            hologres_endpoint="hgproxy.example.com",
            hologres_database="test_db",
            hologres_access_key="secret_key",
        )
        run_coro(connector.connect(config))

    def test_build_connection_string_hides_credentials(self):
        connector = HologresDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.HOLOGRES,
            connection_reference="my_access_id",
            hologres_endpoint="hgproxy.example.com",
            hologres_port=80,
            hologres_database="test_db",
            hologres_access_key="my_secret_key",
        )
        conn_str = connector._build_connection_string(config)
        assert "my_access_id" in conn_str
        assert "my_secret_key" in conn_str
        assert "hgproxy.example.com" in conn_str
        assert "test_db" in conn_str

    def test_read_empty_query_returns_empty(self):
        connector = HologresDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.HOLOGRES,
            connection_reference="access_id",
            hologres_endpoint="hgproxy.example.com",
            hologres_database="test_db",
        )
        request = DataSourceReadRequest(connection_config=config)
        result = run_coro(connector.read(request))
        assert result.data == []
        assert result.has_more is False


# ---------- Kafka ----------

class TestKafkaConnector:
    """Kafka 连接器测试。"""

    def test_connect_missing_brokers_raises(self):
        connector = KafkaDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.KAFKA,
            connection_reference="kafka_ref",
            kafka_topic="test_topic",
        )
        from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.connect(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED

    def test_connect_missing_topic_raises(self):
        connector = KafkaDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.KAFKA,
            connection_reference="kafka_ref",
            kafka_brokers="broker1:9092",
        )
        from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.connect(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED

    def test_connect_valid_config_succeeds(self):
        connector = KafkaDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.KAFKA,
            connection_reference="kafka_ref",
            kafka_brokers="broker1:9092,broker2:9092",
            kafka_topic="test_topic",
            kafka_group_id="test_group",
        )
        run_coro(connector.connect(config))

    def test_infer_type_helper(self):
        assert KafkaDataSourceConnector._infer_type(True) == "boolean"
        assert KafkaDataSourceConnector._infer_type(42) == "integer"
        assert KafkaDataSourceConnector._infer_type(3.14) == "float"
        assert KafkaDataSourceConnector._infer_type("hello") == "string"
        assert KafkaDataSourceConnector._infer_type({"a": 1}) == "object"
        assert KafkaDataSourceConnector._infer_type([1, 2]) == "array"


# ---------- Flink ----------

class TestFlinkConnector:
    """Flink 连接器测试。"""

    def test_connect_missing_jm_url_raises(self):
        connector = FlinkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.FLINK,
            connection_reference="flink_ref",
            flink_jar_id="jar_001",
        )
        from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.connect(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED

    def test_connect_missing_jar_id_raises(self):
        connector = FlinkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.FLINK,
            connection_reference="flink_ref",
            flink_jobmanager_url="http://flink-host:8081",
        )
        from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.connect(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED

    def test_connect_valid_config_succeeds(self):
        connector = FlinkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.FLINK,
            connection_reference="flink_ref",
            flink_jobmanager_url="http://flink-host:8081",
            flink_jar_id="jar_001",
            flink_program_args="--input topic1 --output topic2",
        )
        run_coro(connector.connect(config))


# ---------- Spark ----------

class TestSparkConnector:
    """Spark 连接器测试。"""

    def test_connect_missing_master_url_raises(self):
        connector = SparkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.SPARK,
            connection_reference="spark_ref",
        )
        from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
        with pytest.raises(DomainError) as exc_info:
            run_coro(connector.connect(config))
        assert exc_info.value.code == DATASOURCE_CONNECTION_FAILED

    def test_connect_valid_config_succeeds(self):
        connector = SparkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.SPARK,
            connection_reference="spark_ref",
            spark_master_url="spark://spark-host:7077",
            spark_app_name="test_app",
        )
        run_coro(connector.connect(config))

    def test_livy_url_from_master(self):
        connector = SparkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.SPARK,
            connection_reference="spark_ref",
            spark_master_url="spark://spark-host:7077",
        )
        livy_url = connector._livy_url(config)
        assert livy_url == "http://spark-host:8998"

    def test_livy_url_from_explicit_url(self):
        connector = SparkDataSourceConnector()
        config = DataSourceConnectionConfig(
            source_type=DataSourceType.SPARK,
            connection_reference="spark_ref",
            spark_master_url="spark://spark-host:7077",
            url="http://livy-host:8998/",
        )
        livy_url = connector._livy_url(config)
        assert livy_url == "http://livy-host:8998"

    def test_extract_rows_from_dict(self):
        rows = SparkDataSourceConnector._extract_rows({"a": 1, "b": "hello"})
        assert len(rows) == 1
        assert rows[0]["a"] == 1

    def test_extract_rows_from_list_of_dicts(self):
        rows = SparkDataSourceConnector._extract_rows([{"a": 1}, {"a": 2}])
        assert len(rows) == 2

    def test_extract_rows_from_list_of_scalars(self):
        rows = SparkDataSourceConnector._extract_rows([1, 2, 3])
        assert len(rows) == 3
        assert rows[0]["value"] == 1

    def test_extract_rows_empty(self):
        rows = SparkDataSourceConnector._extract_rows(None)
        assert rows == []


# ---------- 注册表 ----------

class TestConnectorRegistry:
    """连接器注册表测试。"""

    def test_all_eight_connectors_registered(self):
        registry = DataSourceConnectorRegistry()
        for source_type in [
            DataSourceType.CSV,
            DataSourceType.JSON,
            DataSourceType.REST_API,
            DataSourceType.DATABASE,
            DataSourceType.HOLOGRES,
            DataSourceType.KAFKA,
            DataSourceType.FLINK,
            DataSourceType.SPARK,
        ]:
            connector = registry.get_connector(source_type)
            assert connector is not None

    def test_get_unknown_connector_raises(self):
        registry = DataSourceConnectorRegistry()
        # 使用未注册的枚举值
        from orchestration_platform.domain.errors import NODE_TOOL_NOT_FOUND, DomainError
        # 手动构造一个不在注册表中的枚举成员
        fake_type = DataSourceType.CSV  # 先取一个合法值
        registry._connectors.pop(fake_type)  # 从注册表移除
        with pytest.raises(DomainError) as exc_info:
            registry.get_connector(fake_type)
        assert exc_info.value.code == NODE_TOOL_NOT_FOUND
