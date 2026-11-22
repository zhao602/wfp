# =============================================================================
# 工具发现领域模型（tool_discovery/models.py）
#
# 职责：定义工具清单项与来源枚举，供三源聚合与前端选项渲染。
# 设计意图（merge_tool_into_agent_node DC-A / FR-1）：
#   1. ToolSource 使用 (str, Enum) 混入（Python 3.10 兼容，禁用 StrEnum，INV-5）；
#   2. ToolCatalogItem 复用既有 ToolPermission 枚举（readonly/readwrite/admin）；
#   3. 字段与前端 ToolCatalogItem TS 接口一一对应。
# =============================================================================

from enum import Enum

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import ToolPermission


class ToolSource(str, Enum):
    """工具来源：平台声明 / MCP 工具市场 / 本地注册表。"""

    PLATFORM = "platform"
    MCP = "mcp"
    LOCAL = "local"


class ToolCatalogItem(BaseModel):
    """工具清单项：Agent 节点工具选项的最小元数据。"""

    tool_identifier: str = Field(min_length=1, description="工具标识（全局唯一）")
    name: str = Field(min_length=1, description="工具显示名称")
    description: str = Field(default="", description="工具功能描述")
    source: ToolSource = Field(description="来源：platform / mcp / local")
    permission: ToolPermission = Field(
        default=ToolPermission.READONLY, description="权限级别（复用既有枚举）"
    )
    version: str = Field(default="1.0.0", description="工具版本号")
