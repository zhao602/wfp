# =============================================================================
# JSON 数据源连接器（integrations/data_source_connectors/json_connector.py）
#
# 职责：以只读方式读取 JSON 文件，支持分页与 Schema 推断。
# =============================================================================

import json
from typing import Any, Dict, List

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


class JsonDataSourceConnector:
    """JSON 文件数据源连接器。"""

    def _load_data(self, config: DataSourceConnectionConfig) -> List[Dict[str, Any]]:
        """加载 JSON 文件并返回行列表。"""
        encoding = config.encoding or "utf-8"
        with open(config.file_path, "r", encoding=encoding) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []

    async def connect(self, config: DataSourceConnectionConfig) -> None:
        """验证文件路径存在。"""
        import os
        if not config.file_path or not os.path.exists(config.file_path):
            from orchestration_platform.domain.errors import DATASOURCE_CONNECTION_FAILED, DomainError
            raise DomainError(DATASOURCE_CONNECTION_FAILED, f"JSON 文件不存在: {config.file_path}")

    async def probe(self, config: DataSourceConnectionConfig, level: str = "liveness") -> None:
        """只读连通性探测：校验文件路径存在（语义与 connect() 一致，DC-7）。"""
        import os
        if not config.file_path:
            raise build_param_error("JSON 文件路径未配置")
        if not os.path.exists(config.file_path):
            raise build_connection_error(Exception("FileNotFound"), f"JSON 文件不存在: {config.file_path}")

    async def read(self, request: DataSourceReadRequest) -> DataSourceReadResult:
        """读取 JSON 文件，支持分页。"""
        all_rows = self._load_data(request.connection_config)
        total = len(all_rows)
        offset = request.pagination.offset
        limit = request.pagination.limit
        page_rows = all_rows[offset : offset + limit]

        return DataSourceReadResult(
            data=page_rows,
            total_count=total,
            has_more=offset + limit < total,
        )

    async def infer_schema(self, config: DataSourceConnectionConfig) -> DataSchema:
        """从首条记录推断 Schema。"""
        rows = self._load_data(config)
        if not rows:
            return DataSchema(fields=[])

        first = rows[0]
        fields = [
            SchemaField(name=key, type=self._infer_type(value), nullable=True)
            for key, value in first.items()
        ]
        return DataSchema(fields=fields)

    async def close(self) -> None:
        pass

    @staticmethod
    def _infer_type(value: Any) -> str:
        """推断 Python 值的类型字符串。"""
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        return "object"
