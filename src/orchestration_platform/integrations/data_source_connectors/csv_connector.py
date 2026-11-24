# =============================================================================
# CSV 数据源连接器（integrations/data_source_connectors/csv_connector.py）
#
# 职责：以只读方式读取 CSV 文件，支持分页与 Schema 推断。
# =============================================================================

import csv
from itertools import islice

from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceConnectionConfig,
    DataSourceReadRequest,
    DataSourceReadResult,
    DataSchema,
    SchemaField,
)
from orchestration_platform.integrations.data_source_connectors.probe_support import (
    build_connection_error,
    build_param_error,
)


class CsvDataSourceConnector:
    """CSV 文件数据源连接器。"""

    async def connect(self, config: DataSourceConnectionConfig) -> None:
        """验证文件路径存在。"""
        import os
        if not config.file_path or not os.path.exists(config.file_path):
            from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
            raise DomainError(DATASOURCE_CONNECTION_FAILED, f"CSV 文件不存在: {config.file_path}")

    async def probe(self, config: DataSourceConnectionConfig, level: str = "liveness") -> None:
        """只读连通性探测：校验文件路径存在（语义与 connect() 一致，DC-7）。"""
        import os
        if not config.file_path:
            raise build_param_error("CSV 文件路径未配置")
        if not os.path.exists(config.file_path):
            raise build_connection_error(Exception("FileNotFound"), f"CSV 文件不存在: {config.file_path}")

    async def read(self, request: DataSourceReadRequest) -> DataSourceReadResult:
        """读取 CSV 文件，支持分页（流式逐行，不整文件驻留，B12-D1.3）。

        islice 跳过 offset 行、探测 limit+1 行判定 has_more；
        total_count 置 None（流式不再全量计数）。
        """
        config = request.connection_config
        encoding = config.encoding or "utf-8"
        offset = request.pagination.offset
        limit = request.pagination.limit

        with open(config.file_path, "r", encoding=encoding, newline="") as f:
            reader = csv.DictReader(f)
            page = list(islice(reader, offset, offset + limit + 1))

        return DataSourceReadResult(
            data=page[:limit],
            total_count=None,
            has_more=len(page) > limit,
        )

    async def infer_schema(self, config: DataSourceConnectionConfig) -> DataSchema:
        """从 CSV 首行推断 Schema。"""
        encoding = config.encoding or "utf-8"
        with open(config.file_path, "r", encoding=encoding, newline="") as f:
            reader = csv.DictReader(f)
            first_row = next(reader, None)

        if first_row is None:
            return DataSchema(fields=[])

        fields = [
            SchemaField(name=key, type="string", nullable=True)
            for key in first_row.keys()
        ]
        return DataSchema(fields=fields)

    async def close(self) -> None:
        """CSV 连接器无需关闭资源。"""
        pass
