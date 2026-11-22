# =============================================================================
# CRM 连接器（enterprise/crm_connector.py）
# =============================================================================

import logging
from typing import Any, Dict, Optional

from orchestration_platform.domain.errors import (
    CONNECTOR_AUTH_FAILED,
    CONNECTOR_SCHEMA_MISMATCH,
    CONNECTOR_TIMEOUT,
    DomainError,
)
from orchestration_platform.integrations.data_source_connectors.enterprise.credential_store import (
    EnterpriseCredentialStore,
)
from orchestration_platform.integrations.data_source_connectors.enterprise.data_scope_guard import (
    DataScopeGuard,
)
from orchestration_platform.integrations.data_source_connectors.enterprise.models import (
    DataScope,
    EnterpriseConnectorConfig,
)

_logger = logging.getLogger(__name__)


class CrmConnector:
    """CRM 连接器。

    REST 抽象读写客户与商机数据。
    凭证经 EnterpriseCredentialStore 注入（不落日志）。
    写操作前经 DataScopeGuard 校验数据范围。
    """

    def __init__(
        self,
        config: EnterpriseConnectorConfig,
        credential_store: EnterpriseCredentialStore,
        scope_guard: Optional[DataScopeGuard] = None,
    ) -> None:
        self._config = config
        self._credential_store = credential_store
        self._scope_guard = scope_guard or DataScopeGuard()

    async def read_customers(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """读取客户数据。"""
        self._scope_guard.check_read("customers", self._config.data_scope)
        return await self._request("GET", "/api/customers", params=query)

    async def write_customer(self, customer_data: Dict[str, Any]) -> Dict[str, Any]:
        """写入客户数据。"""
        self._scope_guard.check_write("customers", self._config.data_scope)
        return await self._request("POST", "/api/customers", json_data=customer_data)

    async def read_opportunities(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """读取商机数据。"""
        self._scope_guard.check_read("opportunities", self._config.data_scope)
        return await self._request("GET", "/api/opportunities", params=query)

    async def _request(
        self,
        method: str,
        path: str,
        params: Dict = None,
        json_data: Dict = None,
    ) -> Dict[str, Any]:
        """执行 HTTP 请求（httpx/requests 延迟导入）。"""
        credential = self._credential_store.get_active(self._config.connector_id)
        url = f"{self._config.target_endpoint}{path}"

        try:
            import httpx
        except ImportError:
            _logger.warning("httpx 未安装，CRM 连接器不可用")
            return {"error": "HTTP 客户端未安装"}

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                headers = {"Authorization": f"Bearer {credential}"}
                response = await client.request(
                    method, url, params=params, json=json_data, headers=headers
                )
                if response.status_code == 401:
                    raise DomainError(CONNECTOR_AUTH_FAILED, "CRM 认证失败")
                if response.status_code == 408:
                    raise DomainError(CONNECTOR_TIMEOUT, "CRM 请求超时")
                return response.json()
        except DomainError:
            raise
        except Exception as exc:
            _logger.error("CRM 请求失败: %s", exc)
            return {"error": str(exc)}
