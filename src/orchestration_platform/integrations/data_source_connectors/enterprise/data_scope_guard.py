# =============================================================================
# 数据范围守卫与连接测试器（enterprise/data_scope_guard.py + connection_tester.py）
# =============================================================================

import logging
import time
from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import CONNECTOR_SCOPE_DENIED, DomainError
from orchestration_platform.integrations.data_source_connectors.enterprise.models import (
    ConnectionTestResult,
    DataScope,
)

_logger = logging.getLogger(__name__)


class DataScopeGuard:
    """数据范围守卫。

    按数据范围声明拒绝越权读写。
    """

    def check_read(self, resource: str, data_scope: DataScope) -> None:
        """校验读操作是否在数据范围内。"""
        if resource not in data_scope.readable_resources:
            raise DomainError(
                CONNECTOR_SCOPE_DENIED,
                f"读操作超出数据范围: {resource}",
                {"resource": resource, "allowed": data_scope.readable_resources},
            )

    def check_write(self, resource: str, data_scope: DataScope) -> None:
        """校验写操作是否在数据范围内。"""
        if resource not in data_scope.writable_resources:
            raise DomainError(
                CONNECTOR_SCOPE_DENIED,
                f"写操作超出数据范围: {resource}",
                {"resource": resource, "allowed": data_scope.writable_resources},
            )


class ConnectionTester:
    """连接测试器。

    轻量探活请求，8 秒硬超时。
    httpx/requests 延迟导入。
    """

    def test(
        self,
        endpoint: str,
        credential: str = "",
        timeout_seconds: Optional[int] = None,
    ) -> ConnectionTestResult:
        """执行连接测试。"""
        settings = get_settings()
        timeout = timeout_seconds or settings.enterprise_connector_test_timeout_seconds

        start = time.monotonic()
        try:
            import httpx
        except ImportError:
            try:
                import requests
            except ImportError:
                return ConnectionTestResult(
                    reachable=False,
                    diagnosis="HTTP 客户端未安装（httpx/requests）",
                )

            try:
                response = requests.head(endpoint, timeout=timeout)
                latency = int((time.monotonic() - start) * 1000)
                return ConnectionTestResult(
                    reachable=response.status_code < 500,
                    response_latency_ms=latency,
                    diagnosis=f"HTTP {response.status_code}",
                )
            except Exception as exc:
                return ConnectionTestResult(
                    reachable=False,
                    diagnosis=f"连接失败: {type(exc).__name__}: {exc}",
                )

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.head(endpoint)
                latency = int((time.monotonic() - start) * 1000)
                return ConnectionTestResult(
                    reachable=response.status_code < 500,
                    response_latency_ms=latency,
                    diagnosis=f"HTTP {response.status_code}",
                )
        except Exception as exc:
            return ConnectionTestResult(
                reachable=False,
                diagnosis=f"连接失败: {type(exc).__name__}: {exc}",
            )
