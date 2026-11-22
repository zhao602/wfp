# =============================================================================
# 企业连接器领域模型（enterprise/models.py）
# =============================================================================

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.common import RetryPolicy
from orchestration_platform.domain.models.enums import DataSourceType


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DataScope(BaseModel):
    """数据范围声明。"""
    readable_resources: List[str] = Field(default_factory=list, description="可读资源")
    writable_resources: List[str] = Field(default_factory=list, description="可写资源")


class EnterpriseConnectorConfig(BaseModel):
    """企业连接器配置。"""
    connector_id: str = Field(description="连接器标识")
    tenant_id: str = Field(description="租户标识")
    connector_type: DataSourceType = Field(description="连接器类型")
    target_endpoint: str = Field(description="目标端点 URL")
    timeout_seconds: int = Field(default=30, ge=1, description="超时（秒）")
    retry_policy: Optional[RetryPolicy] = Field(default=None, description="重试策略")
    data_scope: DataScope = Field(default_factory=DataScope, description="数据范围")
    enabled: bool = Field(default=True, description="是否启用")


class ConnectorCredential(BaseModel):
    """连接器凭证（密文）。"""
    encrypted_secret: str = Field(description="加密密文")
    version: int = Field(description="版本号")
    created_by: str = Field(description="创建人")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")


class CredentialVersionInfo(BaseModel):
    """凭证版本元数据（不含密文）。"""
    version: int = Field(description="版本号")
    created_by: str = Field(description="创建人")
    created_at: datetime = Field(description="创建时间")


class ConnectionTestResult(BaseModel):
    """连接测试结果。"""
    reachable: bool = Field(description="是否可达")
    response_latency_ms: int = Field(default=0, description="响应延迟（毫秒）")
    diagnosis: str = Field(default="", description="诊断信息")
    tested_at: datetime = Field(default_factory=_utc_now, description="测试时间")
