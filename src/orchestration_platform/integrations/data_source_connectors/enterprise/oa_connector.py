# TODO(tech-debt): B档候选孤儿（族群 B14），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# OA 连接器（enterprise/oa_connector.py）
# =============================================================================

import logging
from typing import Any, Dict, Optional

from orchestration_platform.domain.errors import (
    CONNECTOR_AUTH_FAILED,
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
    EnterpriseConnectorConfig,
)

_logger = logging.getLogger(__name__)


class OaConnector:
    """OA 连接器。

    REST 抽象只读流程审批状态与待办数据。
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

    async def read_approval_status(self, flow_id: str) -> Dict[str, Any]:
        """读取审批状态（只读）。"""
        self._scope_guard.check_read("approvals", self._config.data_scope)
        return await self._request("GET", f"/api/approvals/{flow_id}")

    async def read_todos(self, user_id: str) -> Dict[str, Any]:
        """读取待办列表（只读）。"""
        self._scope_guard.check_read("todos", self._config.data_scope)
        return await self._request("GET", "/api/todos", params={"user_id": user_id})

    async def _request(
        self,
        method: str,
        path: str,
        params: Dict = None,
    ) -> Dict[str, Any]:
        """执行 HTTP 请求。"""
        credential = self._credential_store.get_active(self._config.connector_id)
        url = f"{self._config.target_endpoint}{path}"

        try:
            import httpx
        except ImportError:
            _logger.warning("httpx 未安装，OA 连接器不可用")
            return {"error": "HTTP 客户端未安装"}

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                headers = {"Authorization": f"Bearer {credential}"}
                response = await client.request(method, url, params=params, headers=headers)
                if response.status_code == 401:
                    raise DomainError(CONNECTOR_AUTH_FAILED, "OA 认证失败")
                if response.status_code == 408:
                    raise DomainError(CONNECTOR_TIMEOUT, "OA 请求超时")
                return response.json()
        except DomainError:
            raise
        except Exception as exc:
            _logger.error("OA 请求失败: %s", exc)
            return {"error": str(exc)}
