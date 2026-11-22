"""后端企业连接器 Schema 推断端点测试（tests/phase4/test_enterprise_connector_schema.py）。

测试 POST /api/v1/connectors/enterprise/{connector_id}/schema 端点。
兼容 Python 3.10（无 StrEnum/Self/TaskGroup/PEP695）。

根因组 E（fix_baseline_test_failures）：
  1. 实现：端点构建 DataSourceConnectionConfig 只传 target_endpoint 被静默丢弃
     → file_path=None → CSV 连接器 open(None) 抛 TypeError，已在实现侧新增
     端点层映射 _build_schema_connection_config（FR-06 / INV-18）；
  2. 测试：fixture 用 Unix 风格 /tmp/test.csv（Windows 不存在），改为
     tmp_path 真实临时 CSV（FR-07）；
  3. 新增字段存在性断言防回归（FR-07 / AC-06，来源：2026-10-14-pydantic-v2-
     extra-ignore-silent-field-drop 经验）。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestration_platform.api.routers.governance.enterprise_connector_router import (
    router as enterprise_router,
    _build_schema_connection_config,
    _configs,
)
from orchestration_platform.integrations.data_source_connectors.enterprise.models import (
    EnterpriseConnectorConfig,
    DataScope,
)
from orchestration_platform.domain.models.enums import DataSourceType


@pytest.fixture
def app():
    """构造含企业连接器路由的 FastAPI 测试应用。"""
    _app = FastAPI()
    _app.include_router(enterprise_router, prefix="/api/v1")
    return _app


@pytest.fixture
def client(app):
    """TestClient 实例。"""
    return TestClient(app)


@pytest.fixture
def registered_connector(tmp_path):
    """注册一个测试连接器并返回 connector_id。

    在 tmp_path 下写入真实临时 CSV（Windows 兼容），target_endpoint 指向该文件。
    """
    connector_id = "test-connector-for-schema"
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("name,age\nAlice,30\n", encoding="utf-8")
    config = EnterpriseConnectorConfig(
        connector_id=connector_id,
        tenant_id="default",
        connector_type=DataSourceType.CSV,
        target_endpoint=str(csv_path),
        data_scope=DataScope(readable_resources=["*"], writable_resources=[]),
        enabled=True,
    )
    _configs[connector_id] = config
    yield connector_id
    _configs.pop(connector_id, None)


class TestInferSchema:
    """Schema 推断端点测试。"""

    def test_schema_inference_returns_data_schema(self, client, registered_connector):
        """已注册 connector_id 返回 DataSchema（含 fields 列表 + inferred_at）。"""
        response = client.post(
            f"/api/v1/connectors/enterprise/{registered_connector}/schema"
        )
        assert response.status_code == 200
        data = response.json()
        assert "fields" in data
        assert "inferred_at" in data
        assert isinstance(data["fields"], list)

    def test_schema_connection_config_field_roundtrip(self, registered_connector):
        """AC-06 防回归：端点层映射 file_path 不丢失（round-trip 校验）。

        来源：2026-10-14-pydantic-v2-extra-ignore-silent-field-drop 经验——
        若映射失效，file_path 将被 extra='ignore' 静默丢弃为 None。
        """
        config = _configs[registered_connector]
        connection_config = _build_schema_connection_config(config)
        assert connection_config.file_path is not None
        assert connection_config.file_path == config.target_endpoint
        assert connection_config.source_type == DataSourceType.CSV

    def test_schema_inference_nonexistent_connector_404(self, client):
        """不存在的 connector_id 返回 404。"""
        response = client.post(
            "/api/v1/connectors/enterprise/nonexistent/schema"
        )
        assert response.status_code == 404

    def test_schema_inference_unregistered_type_404(self, client):
        """未注册的连接器类型（如 CRM）返回 404。"""
        connector_id = "crm-connector-test"
        config = EnterpriseConnectorConfig(
            connector_id=connector_id,
            tenant_id="default",
            connector_type=DataSourceType.CRM,
            target_endpoint="https://crm.example.com/api",
            data_scope=DataScope(readable_resources=["*"], writable_resources=[]),
            enabled=True,
        )
        _configs[connector_id] = config
        try:
            response = client.post(
                f"/api/v1/connectors/enterprise/{connector_id}/schema"
            )
            assert response.status_code == 404
            assert "Schema 推断未实现" in response.json()["detail"]
        finally:
            _configs.pop(connector_id, None)