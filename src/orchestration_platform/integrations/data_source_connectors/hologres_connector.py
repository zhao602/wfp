# =============================================================================
# Hologres 数据源连接器（integrations/data_source_connectors/hologres_connector.py）
#
# 职责：以只读方式查询 Hologres 实时数仓，支持分页与 Schema 推断。
# 设计意图（对应项目构建计划书「数据集成」MCP 连接器要求）：
#   1. Hologres 兼容 PostgreSQL 协议，通过 psycopg2/asyncpg 延迟导入连接；
#   2. 查询语句经 read_only_statement_guard 校验，禁止写操作；
#   3. 凭据（AccessKey）不出现在日志或异常消息中。
# =============================================================================


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


class HologresDataSourceConnector:
    """Hologres 实时数仓数据源连接器（只读查询）。

    Hologres 兼容 PostgreSQL 协议，连接串格式：
        postgresql://<access_id>:<access_key>@<endpoint>:<port>/<database>
    本连接器从 DataSourceConnectionConfig 中组装连接串，凭据不出现在日志。
    """

    async def connect(self, config: DataSourceConnectionConfig) -> None:
        """验证 Hologres 连接参数完整性。"""
        if not config.hologres_endpoint:
            from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
            raise DomainError(DATASOURCE_CONNECTION_FAILED, "Hologres 端点未配置")
        if not config.hologres_database:
            from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
            raise DomainError(DATASOURCE_CONNECTION_FAILED, "Hologres 数据库名未配置")

    async def probe(self, config: DataSourceConnectionConfig, level: str = "liveness") -> None:
        """只读连通性探测：真实连接 Hologres 后立即关闭（无副作用）。

        四阶段：① 参数预检 → ② 驱动守卫 → ③ 真实探测 → ④ finally 释放。
        凭据（AccessKey）不得进入诊断（INV-4）。
        """
        # ① 参数预检
        if not config.hologres_endpoint:
            raise build_param_error("Hologres 端点未配置")
        if not config.hologres_database:
            raise build_param_error("Hologres 数据库名未配置")
        if "." not in config.hologres_endpoint and ":" not in config.hologres_endpoint:
            raise build_param_error("Hologres 端点格式无效")
        # ② 驱动守卫
        asyncpg = require_driver("asyncpg")
        # ③ 真实探测（复用组装的连接串，仅用于连接，不外泄）
        conn = None
        try:
            conn_str = self._build_connection_string(config)
            conn = await asyncpg.connect(conn_str, timeout=PROBE_TIMEOUT_SECONDS)
        except Exception as exc:
            raise build_connection_error(exc, "Hologres 连接失败")
        # ④ finally 释放
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass

    def _build_connection_string(self, config: DataSourceConnectionConfig) -> str:
        """从配置组装 Hologres 连接串（凭据不外泄）。"""
        endpoint = config.hologres_endpoint
        port = config.hologres_port
        database = config.hologres_database
        access_key = config.hologres_access_key or ""
        # access_id 从 connection_reference 获取
        access_id = config.connection_reference
        return f"postgresql://{access_id}:{access_key}@{endpoint}:{port}/{database}"

    async def read(self, request: DataSourceReadRequest) -> DataSourceReadResult:
        """执行只读 SQL 查询，支持分页。

        使用 asyncpg（延迟导入），无驱动时返回空结果。
        查询语句经 read_only_statement_guard 校验，禁止写操作。
        """
        config = request.connection_config
        if not config.query:
            return DataSourceReadResult(data=[], has_more=False)

        # 写操作守卫
        from orchestration_platform.integrations.data_source.read_only_statement_guard import (
            ReadOnlyStatementGuard,
        )
        guard = ReadOnlyStatementGuard()
        violation = guard.check(config.query)
        if violation:
            from orchestration_platform.domain.errors import WF_VALIDATION_FAILED, DomainError
            raise DomainError(WF_VALIDATION_FAILED, f"查询包含写操作: {violation}")

        # 构造分页查询
        offset = request.pagination.offset
        limit = request.pagination.limit
        paginated_query = f"{config.query} LIMIT {limit} OFFSET {offset}"

        conn_str = self._build_connection_string(config)

        try:
            import asyncpg
        except ImportError:
            return DataSourceReadResult(data=[], has_more=False)

        try:
            conn = await asyncpg.connect(conn_str)
            try:
                rows = await conn.fetch(paginated_query)
                data = [dict(row) for row in rows]
                return DataSourceReadResult(data=data, has_more=len(data) == limit)
            finally:
                await conn.close()
        except Exception as exc:
            from orchestration_platform.domain.errors import DATASOURCE_READ_TIMEOUT, DomainError
            # 异常消息中不包含连接串（含凭据）
            raise DomainError(DATASOURCE_READ_TIMEOUT, f"Hologres 查询失败: {type(exc).__name__}")

    async def infer_schema(self, config: DataSourceConnectionConfig) -> DataSchema:
        """查询 information_schema 推断表 Schema。"""
        if not config.query:
            return DataSchema(fields=[])

        conn_str = self._build_connection_string(config)

        try:
            import asyncpg
        except ImportError:
            return DataSchema(fields=[])

        try:
            conn = await asyncpg.connect(conn_str)
            try:
                # 解析表名（简化实现：从 FROM 子句提取）
                import re
                match = re.search(r'\bFROM\s+(\w+)', config.query, re.IGNORECASE)
                if not match:
                    return DataSchema(fields=[])
                table_name = match.group(1)
                schema_query = (
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    f"WHERE table_name = '{table_name}' ORDER BY ordinal_position"
                )
                rows = await conn.fetch(schema_query)
                fields = [
                    SchemaField(
                        name=row["column_name"],
                        type=str(row["data_type"]),
                        nullable=row["is_nullable"] == "YES",
                    )
                    for row in rows
                ]
                return DataSchema(fields=fields)
            finally:
                await conn.close()
        except Exception:
            return DataSchema(fields=[])

    async def close(self) -> None:
        pass
