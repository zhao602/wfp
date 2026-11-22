# =============================================================================
# 数据源连接器注册表（integrations/data_source_connectors/connector_registry.py）
#
# 职责：按 DataSourceType 登记与查找连接器实例。
# 设计意图：新增数据源类型只需实现 IDataSourceConnector 并调用 register 登记。
# =============================================================================

from typing import Dict, Optional

from orchestration_platform.domain.errors import NODE_TOOL_NOT_FOUND, DomainError
from orchestration_platform.domain.models.enums import DataSourceType
from orchestration_platform.integrations.data_source_connectors.csv_connector import CsvDataSourceConnector
from orchestration_platform.integrations.data_source_connectors.json_connector import JsonDataSourceConnector
from orchestration_platform.integrations.data_source_connectors.rest_api_connector import RestApiDataSourceConnector
from orchestration_platform.integrations.data_source_connectors.database_connector import DatabaseDataSourceConnector
from orchestration_platform.integrations.data_source_connectors.hologres_connector import HologresDataSourceConnector
from orchestration_platform.integrations.data_source_connectors.kafka_connector import KafkaDataSourceConnector
from orchestration_platform.integrations.data_source_connectors.flink_connector import FlinkDataSourceConnector
from orchestration_platform.integrations.data_source_connectors.spark_connector import SparkDataSourceConnector


class DataSourceConnectorRegistry:
    """数据源连接器注册表。"""

    def __init__(self) -> None:
        self._connectors: Dict[DataSourceType, object] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """登记八类默认连接器（通用数据源 + 大数据组件）。"""
        self._connectors[DataSourceType.CSV] = CsvDataSourceConnector()
        self._connectors[DataSourceType.JSON] = JsonDataSourceConnector()
        self._connectors[DataSourceType.REST_API] = RestApiDataSourceConnector()
        self._connectors[DataSourceType.DATABASE] = DatabaseDataSourceConnector()
        self._connectors[DataSourceType.HOLOGRES] = HologresDataSourceConnector()
        self._connectors[DataSourceType.KAFKA] = KafkaDataSourceConnector()
        self._connectors[DataSourceType.FLINK] = FlinkDataSourceConnector()
        self._connectors[DataSourceType.SPARK] = SparkDataSourceConnector()

    def register(self, source_type: DataSourceType, connector: object) -> None:
        """登记连接器（覆盖同类型）。"""
        self._connectors[source_type] = connector

    def get_connector(self, source_type: DataSourceType) -> object:
        """按类型获取连接器。"""
        connector = self._connectors.get(source_type)
        if connector is None:
            raise DomainError(
                NODE_TOOL_NOT_FOUND,
                f"数据源类型 {source_type.value} 的连接器未注册",
            )
        return connector
