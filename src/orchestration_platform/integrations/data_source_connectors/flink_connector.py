# =============================================================================
# Flink 数据源连接器（integrations/data_source_connectors/flink_connector.py）
#
# 职责：通过 Flink JobManager REST API 提交与查询流计算作业结果。
# 设计意图（对应项目构建计划书「数据集成」MCP 连接器要求）：
#   1. 使用 httpx（延迟导入）调用 Flink REST API；
#   2. read() 提交 JAR 作业并轮询作业状态，完成后获取结果；
#   3. infer_schema() 从作业配置推断输出 Schema。
# =============================================================================

import asyncio
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


class FlinkDataSourceConnector:
    """Flink 流计算引擎数据源连接器。

    通过 JobManager REST API（默认端口 8081）提交 JAR 作业并获取结果。
    """

    _POLL_INTERVAL_SECONDS = 2.0
    _MAX_POLL_ATTEMPTS = 60

    async def connect(self, config: DataSourceConnectionConfig) -> None:
        """验证 Flink JobManager URL 与 JAR ID。"""
        if not config.flink_jobmanager_url:
            from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
            raise DomainError(DATASOURCE_CONNECTION_FAILED, "Flink JobManager URL 未配置")
        if not config.flink_jar_id:
            from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
            raise DomainError(DATASOURCE_CONNECTION_FAILED, "Flink JAR ID 未配置")

    async def probe(self, config: DataSourceConnectionConfig, level: str = "liveness") -> None:
        """只读连通性探测：GET Flink JobManager /jobs（不提交作业）。

        四阶段：① 参数预检 → ② 驱动守卫 → ③ 真实探测 → ④ finally 释放。
        """
        # ① 参数预检
        if not config.flink_jobmanager_url:
            raise build_param_error("Flink JobManager URL 未配置")
        from urllib.parse import urlparse
        parsed = urlparse(config.flink_jobmanager_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise build_param_error("Flink JobManager URL 格式无效")
        # ② 驱动守卫
        httpx = require_driver("httpx")
        # ③ 真实探测（只读 GET，10s 超时）
        client = None
        try:
            client = httpx.AsyncClient(timeout=httpx.Timeout(PROBE_TIMEOUT_SECONDS))
            url = config.flink_jobmanager_url.rstrip("/") + "/jobs"
            response = await client.get(url)
            if response.status_code >= 400:
                raise build_connection_error(Exception("HTTPError"), f"HTTP {response.status_code}")
        except Exception as exc:
            if getattr(exc, "code", "") == "DATASOURCE_CONNECTION_FAILED":
                raise
            raise build_connection_error(exc, "Flink 连接失败")
        # ④ finally 释放
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass

    async def read(self, request: DataSourceReadRequest) -> DataSourceReadResult:
        """提交 Flink 作业并轮询至完成，返回作业结果。

        使用 httpx（延迟导入）调用 REST API。
        """
        config = request.connection_config
        jm_url = config.flink_jobmanager_url.rstrip("/")
        jar_id = config.flink_jar_id
        program_args = config.flink_program_args or ""

        try:
            import httpx
        except ImportError:
            return DataSourceReadResult(data=[], has_more=False)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # 提交作业
                submit_url = f"{jm_url}/jars/{jar_id}/run"
                submit_payload: Dict[str, Any] = {}
                if program_args:
                    submit_payload["programArgs"] = program_args
                response = await client.post(submit_url, json=submit_payload)
                response.raise_for_status()
                job_id = response.json().get("jobid")
                if not job_id:
                    from orchestration_platform.domain.errors import DATASOURCE_JOB_SUBMIT_FAILED, DomainError
                    raise DomainError(DATASOURCE_JOB_SUBMIT_FAILED, "Flink 作业提交未返回 jobid")

                # 轮询作业状态
                for _ in range(self._MAX_POLL_ATTEMPTS):
                    await asyncio.sleep(self._POLL_INTERVAL_SECONDS)
                    status_url = f"{jm_url}/jobs/{job_id}"
                    status_resp = await client.get(status_url)
                    status_resp.raise_for_status()
                    job_state = status_resp.json().get("state", "")
                    if job_state in ("FINISHED", "FAILED", "CANCELED"):
                        break
                else:
                    from orchestration_platform.domain.errors import DATASOURCE_READ_TIMEOUT, DomainError
                    raise DomainError(DATASOURCE_READ_TIMEOUT, "Flink 作业轮询超时")

                if job_state != "FINISHED":
                    from orchestration_platform.domain.errors import DATASOURCE_JOB_SUBMIT_FAILED, DomainError
                    raise DomainError(DATASOURCE_JOB_SUBMIT_FAILED, f"Flink 作业未完成: state={job_state}")

                # 获取作业结果（Accumulators）
                acc_url = f"{jm_url}/jobs/{job_id}/accumulators"
                acc_resp = await client.get(acc_url)
                acc_resp.raise_for_status()
                acc_data = acc_resp.json().get("serialized-accumulator-data", [])

                return DataSourceReadResult(
                    data=acc_data if isinstance(acc_data, list) else [acc_data],
                    total_count=len(acc_data) if isinstance(acc_data, list) else 1,
                    has_more=False,
                )
        except Exception as exc:
            from orchestration_platform.domain.errors import DATASOURCE_JOB_SUBMIT_FAILED, DomainError
            raise DomainError(DATASOURCE_JOB_SUBMIT_FAILED, f"Flink 作业执行失败: {type(exc).__name__}")

    async def infer_schema(self, config: DataSourceConnectionConfig) -> DataSchema:
        """从 Flink 作业配置推断输出 Schema（简化实现）。"""
        return DataSchema(fields=[])

    async def close(self) -> None:
        pass
