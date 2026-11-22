# =============================================================================
# 工具发现包（tool_discovery/__init__.py）
#
# 职责：三源（平台声明 / MCP 工具市场 / 本地注册表）聚合的工具清单发现能力。
# 设计意图（merge_tool_into_agent_node DC-A / FR-1）：
#   1. 为 Agent 节点工具选项化提供统一只读清单；
#   2. 单源异常跳过、恒可用（清单三重降级的第一层）；
#   3. 严禁调用仓储 save / market register（只读，INV-10）。
# =============================================================================

from orchestration_platform.tool_discovery.models import (
    ToolCatalogItem,
    ToolSource,
)
from orchestration_platform.tool_discovery.tool_catalog_service import (
    ToolCatalogService,
)

__all__ = ["ToolCatalogItem", "ToolSource", "ToolCatalogService"]
