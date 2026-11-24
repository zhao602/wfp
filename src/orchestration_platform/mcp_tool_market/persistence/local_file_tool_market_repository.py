# =============================================================================
# 本地文件工具市场仓储（mcp_tool_market/persistence/local_file_tool_market_repository.py）
#
# 职责：以本地文件方式持久化 MCP 工具市场注册记录。
# 设计意图（对应 spec 5.3.1.12、任务 T34.6）：
#   按 tool_id + version 唯一存储，重启后不丢失。
# =============================================================================

import os
from typing import List, Optional

from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.mcp_tool_market.models import McpToolRegistration


class LocalFileToolMarketRepository:
    """本地文件工具市场仓储。"""

    def __init__(self, storage_dir: str = ".data/mcp_tool_market") -> None:
        """初始化本地文件仓储。

        Args:
            storage_dir: 存储目录。
        """
        self._storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)

    def _file_path(self, tool_identifier: str, version: str) -> str:
        """生成存储文件路径。"""
        safe_id = tool_identifier.replace("/", "_").replace("\\", "_")
        return os.path.join(self._storage_dir, f"{safe_id}_{version}.json")

    async def save(self, registration: McpToolRegistration) -> None:
        """保存工具注册记录。"""
        path = self._file_path(
            registration.descriptor.tool_identifier,
            registration.descriptor.version,
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(registration.model_dump_json(indent=2))

    async def get(self, tool_identifier: str, version: str) -> Optional[McpToolRegistration]:
        """按标识与版本获取工具注册记录。"""
        path = self._file_path(tool_identifier, version)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return McpToolRegistration.model_validate_json(f.read())

    async def list_by_category(self, category: str) -> List[McpToolRegistration]:
        """按类别（标签）列出工具。"""
        results = []
        for filename in os.listdir(self._storage_dir):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(self._storage_dir, filename)
            with open(path, "r", encoding="utf-8") as f:
                try:
                    reg = McpToolRegistration.model_validate_json(f.read())
                    if category in reg.descriptor.tags:
                        results.append(reg)
                except Exception:
                    continue
        return results

    async def list_by_permission(self, permission: ToolPermission) -> List[McpToolRegistration]:
        """按权限列出工具。"""
        results = []
        for filename in os.listdir(self._storage_dir):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(self._storage_dir, filename)
            with open(path, "r", encoding="utf-8") as f:
                try:
                    reg = McpToolRegistration.model_validate_json(f.read())
                    if reg.descriptor.permission == permission:
                        results.append(reg)
                except Exception:
                    continue
        return results

    async def list_versions(self, tool_identifier: str) -> List[McpToolRegistration]:
        """列出工具的所有版本。"""
        safe_id = tool_identifier.replace("/", "_").replace("\\", "_")
        prefix = f"{safe_id}_"
        results = []
        for filename in os.listdir(self._storage_dir):
            if filename.startswith(prefix) and filename.endswith(".json"):
                path = os.path.join(self._storage_dir, filename)
                with open(path, "r", encoding="utf-8") as f:
                    try:
                        results.append(McpToolRegistration.model_validate_json(f.read()))
                    except Exception:
                        continue
        return results

    async def exists(self, tool_identifier: str, version: str) -> bool:
        """检查工具版本是否已存在。"""
        return os.path.exists(self._file_path(tool_identifier, version))
