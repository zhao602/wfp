# =============================================================================
# Agent 市场与 Tool 市场领域模型与模板常量入口（market/__init__.py）
#
# 职责：导出市场领域模型与内置模板常量访问入口（agent_tool_marketplace T1.7）。
# 约束：无 langgraph 引用（INV-D12）；枚举写法 (str, Enum)（INV-D11）。
# =============================================================================

from orchestration_platform.market.agent_templates import (
    AGENT_TEMPLATES,
    TEMPLATE_PRESET_ALLOWED_KEYS,
    get_template,
    list_templates,
)
from orchestration_platform.market.models import (
    AgentMarketEntry,
    AgentTemplate,
    MarketEntryStatus,
    ParamSummary,
    TemplateDoc,
    TemplateEdgePreset,
    TemplateMetric,
    TemplateNodePreset,
    TemplatePerformanceBoundaries,
    TemplatePreset,
    TemplateToolConfig,
    ToolMarketEntry,
)

__all__ = [
    "AGENT_TEMPLATES",
    "TEMPLATE_PRESET_ALLOWED_KEYS",
    "get_template",
    "list_templates",
    "AgentMarketEntry",
    "AgentTemplate",
    "MarketEntryStatus",
    "ParamSummary",
    "TemplateDoc",
    "TemplateEdgePreset",
    "TemplateMetric",
    "TemplateNodePreset",
    "TemplatePerformanceBoundaries",
    "TemplatePreset",
    "TemplateToolConfig",
    "ToolMarketEntry",
]
