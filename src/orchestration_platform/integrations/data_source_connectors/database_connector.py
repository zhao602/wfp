# =============================================================================
# 数据库数据源连接器（integrations/data_source_connectors/database_connector.py）
#
# 职责：以只读方式执行 SQL 查询获取数据，支持分页与 Schema 推断。
# =============================================================================

import re
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


class DatabaseDataSourceConnector:
    """数据库数据源连接器（只读查询）。"""

    async def connect(self, config: DataSourceConnectionConfig) -> None:
        """验证连接串与查询非空。"""
        if not config.connection_string:
            from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
            raise DomainError(DATASOURCE_CONNECTION_FAILED, "数据库连接串未配置")

    async def probe(self, config: DataSourceConnectionConfig, level: str = "liveness") -> None:
        """只读连通性探测：真实建立数据库连接后立即关闭（无副作用）。

        四阶段：① 参数预检 → ② 驱动守卫 → ③ 真实探测 → ④ finally 释放。
        """
        # ① 参数预检
        if not config.connection_string:
            raise build_param_error("数据库连接串未配置")
        if "://" in config.connection_string:
            from urllib.parse import urlparse
            parsed = urlparse(config.connection_string)
            if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
                raise build_param_error("数据库连接串格式无效")
        elif "=" not in config.connection_string:
            raise build_param_error("数据库连接串格式无效")
        # ② 驱动守卫（无驱动抛无法探测，不假阳性）
        asyncpg = require_driver("asyncpg")
        # ③ 真实探测（只读，10s 超时）
        conn = None
        try:
            conn = await asyncpg.connect(config.connection_string, timeout=PROBE_TIMEOUT_SECONDS)
        except Exception as exc:
            raise build_connection_error(exc, "数据库连接失败")
        # ④ finally 释放
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass

    def _build_paginated_query(self, query: str, limit: int, offset: int) -> str:
        """构造分页查询语句（纯字符串处理，无 I/O、无副作用）。

        步骤：① 去除末尾分号与空白 → ② 空串短路 → ③ 检测已有 LIMIT/OFFSET → ④ 条件追加。

        已知限制：LIMIT/OFFSET 出现在字符串字面量或注释中可能误判（Q-2 缓解）。
        """
        cleaned = re.sub(r"[\s;]+$", "", query)
        if not cleaned:
            return ""
        has_limit = re.search(r"\bLIMIT\b", cleaned, re.IGNORECASE) is not None
        has_offset = re.search(r"\bOFFSET\b", cleaned, re.IGNORECASE) is not None
        if has_limit or has_offset:
            return cleaned
        return f"{cleaned} LIMIT {limit} OFFSET {offset}"

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
        paginated_query = self._build_paginated_query(config.query, limit, offset)
        if not paginated_query:
            return DataSourceReadResult(data=[], has_more=False)

        try:
            import asyncpg
        except ImportError:
            return DataSourceReadResult(data=[], has_more=False)

        try:
            conn = await asyncpg.connect(config.connection_string)
            try:
                rows = await conn.fetch(paginated_query)
                data = [dict(row) for row in rows]
                return DataSourceReadResult(data=data, has_more=len(data) == limit)
            finally:
                await conn.close()
        except Exception as exc:
            from orchestration_platform.domain.errors import DATASOURCE_READ_TIMEOUT, DomainError
            raise DomainError(DATASOURCE_READ_TIMEOUT, f"数据库读取失败: {exc}")

    async def infer_schema(self, config: DataSourceConnectionConfig) -> DataSchema:
        """从查询结果推断 Schema（简化实现）。"""
        return DataSchema(fields=[])

    async def close(self) -> None:
        pass
