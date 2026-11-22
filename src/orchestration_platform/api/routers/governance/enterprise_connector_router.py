# =============================================================================
# 企业连接器 REST API（api/routers/governance/enterprise_connector_router.py）
# =============================================================================

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import DataSourceType
from orchestration_platform.integrations.data_source_connectors import models as ds_models
from orchestration_platform.integrations.data_source_connectors.connector_registry import (
    DataSourceConnectorRegistry,
)
from orchestration_platform.integrations.data_source_connectors.enterprise.credential_store import (
    EnterpriseCredentialStore,
)
from orchestration_platform.integrations.data_source_connectors.enterprise.data_scope_guard import (
    ConnectionTester,
)
from orchestration_platform.integrations.data_source_connectors.enterprise.models import (
    ConnectionTestResult,
    CredentialVersionInfo,
    DataScope,
    EnterpriseConnectorConfig,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["企业连接器管理"])

_credential_store = EnterpriseCredentialStore()
_connection_tester = ConnectionTester()
_configs: dict = {}
_connector_registry = DataSourceConnectorRegistry()


class ConnectorConfigRequest(BaseModel):
    connector_id: str = Field(description="连接器标识")
    tenant_id: str = Field(description="租户标识")
    connector_type: str = Field(description="连接器类型")
    target_endpoint: str = Field(description="目标端点")
    timeout_seconds: int = Field(default=30, ge=1)
    readable_resources: List[str] = Field(default_factory=list)
    writable_resources: List[str] = Field(default_factory=list)
    enabled: bool = Field(default=True)


class CredentialUpdateRequest(BaseModel):
    credential_plaintext: str = Field(description="凭证明文（仅写入）")
    created_by: str = Field(description="操作人")


class ConnectorResponse(BaseModel):
    connector_id: str
    connector_type: str
    target_endpoint: str
    enabled: bool


@router.get("/connectors/enterprise", response_model=List[ConnectorResponse])
async def list_connectors(tenant_id: str) -> List[ConnectorResponse]:
    """列出企业连接器。"""
    return [
        ConnectorResponse(
            connector_id=c.connector_id,
            connector_type=c.connector_type.value,
            target_endpoint=c.target_endpoint,
            enabled=c.enabled,
        )
        for c in _configs.values()
        if c.tenant_id == tenant_id
    ]


@router.post("/connectors/enterprise", response_model=ConnectorResponse)
async def create_connector(request: ConnectorConfigRequest) -> ConnectorResponse:
    """创建企业连接器配置。"""
    config = EnterpriseConnectorConfig(
        connector_id=request.connector_id,
        tenant_id=request.tenant_id,
        connector_type=DataSourceType(request.connector_type),
        target_endpoint=request.target_endpoint,
        timeout_seconds=request.timeout_seconds,
        data_scope=DataScope(
            readable_resources=request.readable_resources,
            writable_resources=request.writable_resources,
        ),
        enabled=request.enabled,
    )
    _configs[request.connector_id] = config
    return ConnectorResponse(
        connector_id=config.connector_id,
        connector_type=config.connector_type.value,
        target_endpoint=config.target_endpoint,
        enabled=config.enabled,
    )


@router.put("/connectors/enterprise/{connector_id}/credentials", response_model=List[CredentialVersionInfo])
async def update_credential(
    connector_id: str,
    request: CredentialUpdateRequest,
) -> List[CredentialVersionInfo]:
    """更新凭证（响应仅版本元数据）。"""
    _credential_store.put(connector_id, request.credential_plaintext, request.created_by)
    return _credential_store.list_versions(connector_id)


@router.post("/connectors/enterprise/{connector_id}/test", response_model=ConnectionTestResult)
async def test_connection(connector_id: str) -> ConnectionTestResult:
    """测试连接。"""
    config = _configs.get(connector_id)
    if config is None:
        raise HTTPException(status_code=404, detail="连接器不存在")
    credential = _credential_store.get_active(connector_id)
    return _connection_tester.test(config.target_endpoint, credential)

def _build_schema_connection_config(config: EnterpriseConnectorConfig) -> ds_models.DataSourceConnectionConfig:
    """将企业连接器配置映射为数据源连接配置（fix_baseline_test_failures 任务 1，DC-E1）。

    按 connector_type 分派：CSV/JSON（文件类）以 file_path=target_endpoint 构造，
    其余类型不传 file_path/url、保持既有行为（INV-18：映射仅实现于端点层）。
    """
    if config.connector_type in (DataSourceType.CSV, DataSourceType.JSON):
        return ds_models.DataSourceConnectionConfig(
            source_type=config.connector_type,
            connection_reference=config.connector_id,
            file_path=config.target_endpoint,
        )
    return ds_models.DataSourceConnectionConfig(
        source_type=config.connector_type,
        connection_reference=config.connector_id,
    )


@router.post("/connectors/enterprise/{connector_id}/schema", response_model=ds_models.DataSchema)
async def infer_schema(connector_id: str) -> ds_models.DataSchema:
    """推断数据 Schema（Q-2 本期新增，CHG-12）。

    按 connector_id 查企业连接器配置 → 构建 DataSourceConnectionConfig →
    从连接器注册表获取连接器实例 → 调用 infer_schema → 返回 DataSchema。
    """
    from orchestration_platform.domain.errors import DomainError

    config = _configs.get(connector_id)
    if config is None:
        raise HTTPException(status_code=404, detail="连接器不存在")
    connection_config = _build_schema_connection_config(config)
    try:
        connector = _connector_registry.get_connector(config.connector_type)
    except DomainError:
        raise HTTPException(
            status_code=404,
            detail=f"连接器类型 {config.connector_type.value} 的 Schema 推断未实现",
        )
    return await connector.infer_schema(connection_config)
