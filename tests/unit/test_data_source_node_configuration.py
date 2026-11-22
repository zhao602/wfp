# =============================================================================
# DataSourceNodeConfiguration 字段扩展单元测试
# 执行命令：pytest tests/unit/test_data_source_node_configuration.py -v
# =============================================================================

import pytest
from pydantic import ValidationError

from orchestration_platform.domain.models.enums import DataSourceType, NodeType
from orchestration_platform.domain.models.node_configurations import (
    DataSourceNodeConfiguration,
)


class TestDataSourceNodeConfigurationFields:
    """AC-6/AC-7/AC-8/AC-9：字段扩展与向后兼容。"""

    def test_old_yaml_backward_compatible(self):
        """AC-7/AC-8：旧 YAML（仅 connection_reference + query）解析成功，新字段缺省 None。"""
        model = DataSourceNodeConfiguration(
            connection_reference="ds-1",
            query="SELECT * FROM users",
        )
        assert model.connection_reference == "ds-1"
        assert model.query == "SELECT * FROM users"
        assert model.source_type is None
        assert model.file_path is None
        assert model.connection_string is None
        assert model.kafka_brokers is None

    def test_full_fields_parsed(self):
        """AC-6：含全部字段解析后完整保留。"""
        model = DataSourceNodeConfiguration(
            connection_reference="ds-1",
            query="SELECT 1",
            source_type=DataSourceType.CSV,
            file_path="/data/test.csv",
            encoding="utf-8",
            url="http://example.com/api",
            method="POST",
            headers={"Authorization": "Bearer token"},
            connection_string="postgres://user:pass@host/db",
            hologres_endpoint="hg-endpoint",
            hologres_port=80,
            hologres_database="hg-db",
            hologres_access_key="ak-secret",
            kafka_brokers="localhost:9092",
            kafka_topic="test-topic",
            kafka_group_id="group-1",
            kafka_offset="latest",
            flink_jobmanager_url="http://flink:8081",
            flink_jar_id="jar-1",
            flink_program_args="--arg1 val1",
            spark_master_url="spark://localhost:7077",
            spark_submit_args="--num-executors 2",
            spark_app_name="my-app",
            target_endpoint="crm-endpoint",
        )
        assert model.source_type == DataSourceType.CSV
        assert model.file_path == "/data/test.csv"
        assert model.connection_string == "postgres://user:pass@host/db"
        assert model.kafka_brokers == "localhost:9092"
        assert model.spark_master_url == "spark://localhost:7077"
        assert model.target_endpoint == "crm-endpoint"

    def test_unknown_source_type_rejected(self):
        """AC 异常场景：未知 source_type 值解析抛 ValidationError。"""
        with pytest.raises(ValidationError):
            DataSourceNodeConfiguration(
                connection_reference="ds-1",
                source_type="unknown_type",
            )

    def test_headers_default_isolated(self):
        """headers 默认值为独立字典实例（default_factory=dict）。"""
        m1 = DataSourceNodeConfiguration(connection_reference="ds-1")
        m2 = DataSourceNodeConfiguration(connection_reference="ds-2")
        m1.headers["x"] = "1"
        assert "x" not in m2.headers

    def test_round_trip(self):
        """Round-trip：构造 → model_dump → model_validate → 字段保留。"""
        original = DataSourceNodeConfiguration(
            connection_reference="ds-1",
            query="SELECT 1",
            source_type=DataSourceType.DATABASE,
            connection_string="postgres://host/db",
        )
        dumped = original.model_dump()
        restored = DataSourceNodeConfiguration.model_validate(dumped)
        assert restored.connection_reference == "ds-1"
        assert restored.source_type == DataSourceType.DATABASE
        assert restored.connection_string == "postgres://host/db"

    def test_node_type_unchanged(self):
        """既有字段语义不变。"""
        model = DataSourceNodeConfiguration(connection_reference="ds-1")
        assert model.node_type == NodeType.DATA_SOURCE
        assert model.dataset_identifier is None
        assert model.query == ""

    def test_required_field_missing(self):
        """connection_reference 缺失 → ValidationError。"""
        with pytest.raises(ValidationError):
            DataSourceNodeConfiguration(query="SELECT 1")