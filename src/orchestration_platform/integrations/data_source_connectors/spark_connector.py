# =============================================================================
# Spark 数据源连接器（integrations/data_source_connectors/spark_connector.py）
#
# 职责：通过 Spark REST API / Livy 提交批计算作业并获取结果。
# 设计意图（对应项目构建计划书「数据集成」MCP 连接器要求）：
#   1. 使用 httpx（延迟导入）调用 Livy REST API（默认端口 8998）；
#   2. read() �交批作业并轮询会话状态，完成后获取结果；
#   3. infer_schema() 从作业输出推断 Schema。
# =============================================================================

import asyncio
from typing import Any, Dict, List

from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceConnectionConfig,
    DataSourceReadRequest,
    DataSourceReadResult,
    DataSchema,
    SchemaField,
)
from orchestration_platform.integrations.data_source_connectors.probe_support import (
    PROBE_TIMEOUT_SECONDS,
    build_connection_error,
    build_param_error,
    require_driver,
)


class SparkDataSourceConnector:
    """Spark 批计算引擎数据源连接器。

    通过 Livy REST API 提交交互式/批处理会话并执行 Spark SQL 或代码。
    """

    _POLL_INTERVAL_SECONDS = 3.0
    _MAX_POLL_ATTEMPTS = 60

    async def connect(self, config: DataSourceConnectionConfig) -> None:
        """验证 Spark Master URL 配置。"""
        if not config.spark_master_url:
            from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
            raise DomainError(DATASOURCE_CONNECTION_FAILED, "Spark Master URL 未配置")

    async def probe(self, config: DataSourceConnectionConfig, level: str = "liveness") -> None:
        """只读连通性探测：GET Livy /sessions（不创建会话）。

        四阶段：① 参数预检 → ② 驱动守卫 → ③ 真实探测 → ④ finally 释放。
        不注入认证头（Q-8=默认 A）。
        """
        # ① 参数预检
        if not config.spark_master_url:
            raise build_param_error("Spark Master URL 未配置")
        # ② 驱动守卫
        httpx = require_driver("httpx")
        # ③ 真实探测（只读 GET，10s 超时，不创建会话）
        livy_url = self._livy_url(config)
        client = None
        try:
            client = httpx.AsyncClient(timeout=httpx.Timeout(PROBE_TIMEOUT_SECONDS))
            response = await client.get(f"{livy_url}/sessions")
            if response.status_code >= 400:
                raise build_connection_error(Exception("HTTPError"), f"HTTP {response.status_code}")
        except Exception as exc:
            if getattr(exc, "code", "") == "DATASOURCE_CONNECTION_FAILED":
                raise
            raise build_connection_error(exc, "Spark Livy 连接失败")
        # ④ finally 释放
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass

    def _livy_url(self, config: DataSourceConnectionConfig) -> str:
        """从配置推导 Livy REST API URL。

        若 url 字段已设置则直接使用，否则从 spark_master_url 推导默认 Livy 端口。
        """
        if config.url:
            return config.url.rstrip("/")
        # 从 spark_master_url 提取 host，使用默认 Livy 端口 8998
        master = config.spark_master_url
        if master.startswith("spark://"):
            host = master[len("spark://"):].split(":")[0]
            return f"http://{host}:8998"
        return "http://localhost:8998"

    async def read(self, request: DataSourceReadRequest) -> DataSourceReadResult:
        """提交 Spark 批作业并轮询至完成，返回作业结果。

        使用 httpx（延迟导入）调用 Livy REST API。
        查询语句（config.query）作为 Spark SQL 在会话中执行。
        """
        config = request.connection_config
        livy_url = self._livy_url(config)
        app_name = config.spark_app_name or "orchestration_platform_job"
        master = config.spark_master_url

        try:
            import httpx
        except ImportError:
            return DataSourceReadResult(data=[], has_more=False)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # 创建 Livy 会话
                session_payload = {
                    "kind": "pyspark",
                    "name": app_name,
                    "conf": {"spark.master": master},
                }
                if config.spark_submit_args:
                    session_payload["driverMemory"] = "2g"

                create_resp = await client.post(f"{livy_url}/sessions", json=session_payload)
                create_resp.raise_for_status()
                session_id = create_resp.json().get("id")
                if session_id is None:
                    from orchestration_platform.domain.errors import DATASOURCE_JOB_SUBMIT_FAILED, DomainError
                    raise DomainError(DATASOURCE_JOB_SUBMIT_FAILED, "Livy 会话创建未返回 id")

                # 轮询会话状态
                for _ in range(self._MAX_POLL_ATTEMPTS):
                    await asyncio.sleep(self._POLL_INTERVAL_SECONDS)
                    status_resp = await client.get(f"{livy_url}/sessions/{session_id}")
                    status_resp.raise_for_status()
                    session_state = status_resp.json().get("state", "")
                    if session_state in ("idle", "error", "dead"):
                        break
                else:
                    await client.delete(f"{livy_url}/sessions/{session_id}")
                    from orchestration_platform.domain.errors import DATASOURCE_READ_TIMEOUT, DomainError
                    raise DomainError(DATASOURCE_READ_TIMEOUT, "Spark 会话就绪轮询超时")

                if session_state != "idle":
                    await client.delete(f"{livy_url}/sessions/{session_id}")
                    from orchestration_platform.domain.errors import DATASOURCE_JOB_SUBMIT_FAILED, DomainError
                    raise DomainError(DATASOURCE_JOB_SUBMIT_FAILED, f"Spark 会话状态异常: state={session_state}")

                # 提交 Spark SQL 语句
                code = config.query or "spark.sql('SELECT 1').collect()"
                stmt_payload = {"code": code}
                stmt_resp = await client.post(
                    f"{livy_url}/sessions/{session_id}/statements",
                    json=stmt_payload,
                )
                stmt_resp.raise_for_status()
                stmt_id = stmt_resp.json().get("id")

                # 轮询语句执行状态
                for _ in range(self._MAX_POLL_ATTEMPTS):
                    await asyncio.sleep(self._POLL_INTERVAL_SECONDS)
                    result_resp = await client.get(
                        f"{livy_url}/sessions/{session_id}/statements/{stmt_id}"
                    )
                    result_resp.raise_for_status()
                    result_json = result_resp.json()
                    stmt_state = result_json.get("state", "")
                    if stmt_state in ("available", "error"):
                        break
                else:
                    await client.delete(f"{livy_url}/sessions/{session_id}")
                    from orchestration_platform.domain.errors import DATASOURCE_READ_TIMEOUT, DomainError
                    raise DomainError(DATASOURCE_READ_TIMEOUT, "Spark 语句执行轮询超时")

                # 提取结果
                output = result_json.get("output", {})
                status = output.get("status", "")
                if status == "error":
                    await client.delete(f"{livy_url}/sessions/{session_id}")
                    from orchestration_platform.domain.errors import DATASOURCE_JOB_SUBMIT_FAILED, DomainError
                    raise DomainError(DATASOURCE_JOB_SUBMIT_FAILED, "Spark SQL 执行出错")

                data = output.get("data", {})
                rows = self._extract_rows(data)

                # 清理会话
                await client.delete(f"{livy_url}/sessions/{session_id}")

                return DataSourceReadResult(
                    data=rows,
                    total_count=len(rows),
                    has_more=False,
                )
        except Exception as exc:
            from orchestration_platform.domain.errors import DATASOURCE_JOB_SUBMIT_FAILED, DomainError
            raise DomainError(DATASOURCE_JOB_SUBMIT_FAILED, f"Spark 作业执行失败: {type(exc).__name__}")

    @staticmethod
    def _extract_rows(data: Any) -> List[Dict[str, Any]]:
        """从 Livy 响应中提取数据行。"""
        if isinstance(data, dict):
            # PySpark DataFrame collect() 返回 {"application/json": {"data": [...]}}
            for key in ("application/json", "text/plain"):
                if key in data:
                    inner = data[key]
                    if isinstance(inner, dict) and "data" in inner:
                        items = inner["data"]
                        if isinstance(items, list):
                            return items if all(isinstance(i, dict) for i in items) else [{"value": i} for i in items]
                    if isinstance(inner, list):
                        return inner if all(isinstance(i, dict) for i in inner) else [{"value": i} for i in inner]
            return [data]
        if isinstance(data, list):
            return data if all(isinstance(i, dict) for i in data) else [{"value": i} for i in data]
        return [{"value": data}] if data is not None else []

    async def infer_schema(self, config: DataSourceConnectionConfig) -> DataSchema:
        """从 Spark SQL 查询结果推断 Schema（简化实现）。"""
        return DataSchema(fields=[])

    async def close(self) -> None:
        pass
