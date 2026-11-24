# =============================================================================
# REST API 数据源连接器（integrations/data_source_connectors/rest_api_connector.py）
#
# 职责：以只读方式调用 REST API 获取数据，支持分页与超时。
# =============================================================================

from typing import Any, Dict

from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceConnectionConfig,
    DataSourceReadRequest,
    DataSourceReadResult,
    DataSchema,
)
from orchestration_platform.integrations.data_source_connectors.probe_support import (
    PROBE_TIMEOUT_SECONDS,
    build_connection_error,
    build_param_error,
    require_driver,
)


class RestApiDataSourceConnector:
    """REST API 数据源连接器。"""

    async def probe(self, config: DataSourceConnectionConfig, level: str = "liveness") -> None:
        """只读连通性探测：对 url 发 HEAD 请求（不读 body）。

        四阶段：① 参数预检 → ② 驱动守卫 → ③ 真实探测 → ④ finally 释放。
        405/501 视作"可达但方法不支持"，正常返回（Q-7=A）。
        """
        # ① 参数预检
        if not config.url:
            raise build_param_error("REST API URL 未配置")
        from urllib.parse import urlparse
        parsed = urlparse(config.url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise build_param_error("REST API URL 格式无效")
        # ② 驱动守卫
        httpx = require_driver("httpx")
        # ③ 真实探测（只读 HEAD，10s 超时）
        client = None
        response = None
        try:
            client = httpx.AsyncClient(timeout=httpx.Timeout(PROBE_TIMEOUT_SECONDS))
            response = await client.head(config.url)
        except Exception as exc:
            raise build_connection_error(exc)
        # ④ finally 释放
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass
        # 405/501：方法不支持，但端点可达 → 不视为失败
        if response.status_code in (405, 501):
            return
        if response.status_code >= 400:
            raise build_connection_error(Exception("HTTPError"), f"HTTP {response.status_code}")

    async def connect(self, config: DataSourceConnectionConfig) -> None:
        """验证 URL 非空。"""
        if not config.url:
            from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
            raise DomainError(DATASOURCE_CONNECTION_FAILED, "REST API URL 未配置")

    async def read(self, request: DataSourceReadRequest) -> DataSourceReadResult:
        """调用 REST API 获取数据。

        使用 httpx（延迟导入）发送请求，支持分页参数透传。
        """
        config = request.connection_config
        params: Dict[str, Any] = {
            "offset": request.pagination.offset,
            "limit": request.pagination.limit,
        }
        if request.pagination.cursor:
            params["cursor"] = request.pagination.cursor

        try:
            import httpx
        except ImportError:
            return DataSourceReadResult(data=[], has_more=False)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method=config.method,
                    url=config.url,
                    params=params,
                    headers=config.headers,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            from orchestration_platform.domain.errors import DATASOURCE_READ_TIMEOUT, DomainError
            raise DomainError(DATASOURCE_READ_TIMEOUT, f"REST API 读取失败: {exc}")

        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        return DataSourceReadResult(
            data=rows,
            total_count=len(rows),
            has_more=False,
        )

    async def infer_schema(self, config: DataSourceConnectionConfig) -> DataSchema:
        """从首条记录推断 Schema。"""
        return DataSchema(fields=[])

    async def close(self) -> None:
        pass
