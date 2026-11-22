# =============================================================================
# Agent 市场与 Tool 市场领域模型（market/models.py）
#
# 职责：定义市场条目、Agent 模板（预设/文档/工具配置/配方预设/边界/指标）
#       与 Tool 市场条目等新增领域对象（agent_tool_marketplace DC-01 / spec §9）。
# 设计意图：
#   1. 复用既有 ToolSource / ToolPermission / AgentInputEntry / NodeType（零重复枚举）；
#   2. 不含凭据类字段（INV-D08）；可空整数字段用 int | None（INV-D11）；
#   3. 分类载体复用 tags（OP-1），category 为主分类约定；
#   4. recipe 节点一律复用既有节点类型与配置字段（INV-D03，零新增）。
# =============================================================================

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from orchestration_platform.domain.models.enums import (
    ContextWindowStrategyType,
    NodeType,
    ToolPermission,
)
from orchestration_platform.domain.models.node_configurations import AgentInputEntry
from orchestration_platform.tool_discovery.models import ToolSource


def _utc_now() -> datetime:
    """UTC 当前时间（对齐 mcp_tool_market/models.py 时间戳口径）。"""
    return datetime.now(timezone.utc)


class MarketEntryStatus(str, Enum):
    """市场条目状态（spec §9.1.6：builtin / available / draft / pending_review 占位）。"""

    BUILTIN = "builtin"
    AVAILABLE = "available"
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"


class AgentMarketEntry(BaseModel):
    """Agent 市场条目（spec §9.1 / design §2.3.3）。

    tool_refs 为弱引用（不级联删除）；preset_ref 指向内置模板时由服务层
    附加模板引用，不内联模板全文；tags 去重保序（T1.2）。
    """

    entry_id: str = Field(min_length=1, description="条目全局唯一标识（必备）")
    name: str = Field(min_length=1, description="条目显示名称（必备）")
    description: str = Field(default="", description="条目功能描述（可为空）")
    category: str = Field(
        default="general", min_length=1, description="主分类（缺省约定默认分类 general）"
    )
    tags: List[str] = Field(default_factory=list, description="标签列表（分类检索依据）")
    status: MarketEntryStatus = Field(
        default=MarketEntryStatus.DRAFT, description="条目状态"
    )
    source_kind: Literal["builtin", "registered", "user_dev"] = Field(
        default="registered",
        description="条目来源：内置模板派生 / 用户注册 / 用户自主开发上架（DC-04）",
    )
    preset_ref: Optional[str] = Field(
        default=None, description="配置预设引用（可空；指向内置模板标识）"
    )
    tool_refs: List[str] = Field(
        default_factory=list, description="工具引用集合（弱引用 Tool 市场条目，不级联删除）"
    )
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间（必填）")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间（必填）")

    @field_validator("tags")
    @classmethod
    def _dedupe_tags_preserve_order(cls, value: List[str]) -> List[str]:
        seen: List[str] = []
        for item in value:
            if item not in seen:
                seen.append(item)
        return seen

    @field_validator("tool_refs")
    @classmethod
    def _require_non_empty_tool_refs(cls, value: List[str]) -> List[str]:
        for item in value:
            if not item:
                raise ValueError("tool_refs 元素必须为非空字符串")
        return value


class TemplatePreset(BaseModel):
    """模板预设（键集合 ⊆ TEMPLATE_PRESET_ALLOWED_KEYS，agent_templates.py 导入期校验）。

    字段与既有 AgentNodeConfiguration 字段一一对应（INV-D04/INV-D10）——
    其中 context_strategy 为同步扩展的 Agent 配置字段（双侧同名字段，子集关系保持）；
    应用到画布时仅写入此处声明的既有字段值。
    """

    model_identifier: Optional[str] = Field(
        default=None, description="模型标识（可选，编排者填写）"
    )
    system_prompt: str = Field(default="", description="系统提示词（模板核心）")
    available_tools: List[str] = Field(
        default_factory=list, description="工具选择集（映射 available_tools）"
    )
    max_tool_rounds: int | None = Field(
        default=None,
        ge=1,
        description="单节点最大工具轮次（缺省回落全局 agent_max_tool_rounds=5）",
    )
    output_max_retries: int | None = Field(
        default=None, ge=0, description="输出结构校验重试上限（缺省回落全局 2）"
    )
    output_schema: Optional[Dict[str, Any]] = Field(
        default=None, description="期望输出结构约束（可选）"
    )
    inputs: Optional[List[AgentInputEntry]] = Field(
        default=None, description="输入引用与别名声明（可选，三态）"
    )
    context_strategy: Optional[ContextWindowStrategyType] = Field(
        default=None,
        description="上下文窗口策略（None=执行器缺省滑动窗口；映射 context_engine 四种策略）",
    )


class TemplateDoc(BaseModel):
    """模板五维度说明文档（spec §9.4；五字段均必填非空，AC-16）。"""

    core_principle_flow: str = Field(min_length=1, description="①核心原理与执行流程")
    application_features: str = Field(min_length=1, description="②应用中的特性")
    applicable_scenarios: str = Field(min_length=1, description="③适用场景")
    advantages_limitations: str = Field(min_length=1, description="④优势与局限")
    comparison: str = Field(min_length=1, description="⑤与相关算法/方案的对比")


class TemplateToolConfig(BaseModel):
    """模板工具调用配置（spec §9.5；tool_types 非空 / invocation_mode 二值）。"""

    tool_types: List[ToolSource] = Field(
        min_length=1, description="可调用工具类型（platform/mcp/local 子集，非空）"
    )
    invocation_mode: Literal["react_loop", "recipe_nodes"] = Field(
        description="调用方式：react_loop（Agent 节点 ReAct 闭环）/ recipe_nodes（配方节点协作）"
    )
    parameter_constraints: str = Field(
        min_length=1,
        description="参数约束（依工具 input_schema；失败映射 NODE_TOOL_ARGUMENT_MISMATCH）",
    )
    retry_and_exception_policy: str = Field(
        min_length=1, description="失败重试与异常处理策略（受 max_tool_rounds 约束）"
    )


class TemplateNodePreset(BaseModel):
    """配方节点预设（design §2.3.2；节点一律复用既有节点类型，INV-D03）。"""

    local_ref: str = Field(
        min_length=1, description="配方内节点局部引用（画布实例化时映射为 node_id）"
    )
    node_type: NodeType = Field(description="节点类型（复用既有 NodeType，零新增）")
    name: str = Field(min_length=1, description="节点名称")
    configuration: Dict[str, Any] = Field(
        default_factory=dict,
        description="节点配置（键集合 ⊆ 对应节点配置字段集，导入期校验）",
    )


class TemplateEdgePreset(BaseModel):
    """配方连线预设（以 local_ref 表达；condition_label 仅作画布展示）。"""

    source_ref: str = Field(min_length=1, description="起点节点局部引用")
    target_ref: str = Field(min_length=1, description="终点节点局部引用")
    condition_label: Optional[str] = Field(
        default=None, description="连线条件标签（仅画布展示，复用既有 label 语义）"
    )


class TemplatePerformanceBoundaries(BaseModel):
    """模板性能边界（FR-14；字段映射既有 AgentNodeConfiguration 执行边界）。"""

    max_tool_rounds: int | None = Field(
        default=None, ge=1, description="最大工具轮次（None=沿用全局缺省）"
    )
    tool_timeout_seconds: int = Field(
        default=60, ge=1, description="单次工具调用超时（秒，缺省 60）"
    )
    output_max_retries: int | None = Field(
        default=None, ge=0, description="输出校验重试上限（None=沿用全局缺省）"
    )


class TemplateMetric(BaseModel):
    """模板评价指标（FR-15；collectable 显式标注沙箱可采集性，AC-19）。"""

    metric: str = Field(min_length=1, description="评价指标名（可映射既有执行元数据）")
    collectable: Literal["static", "runtime", "not_quantifiable"] = Field(
        description="沙箱可采集性标注：static（静态可计数）/ runtime（运行期可采集）/ not_quantifiable（不可量化）"
    )


class AgentTemplate(BaseModel):
    """Agent 模板（spec §9.2 / design §2.3.2；内置 5 个取值固定，AC-12）。

    kind=single_node：预设合并/新建单个 agent 节点；
    kind=recipe：nodes/edges 非空（≥2 节点），节点全部复用既有节点类型。
    """

    template_id: str = Field(
        min_length=1, description="模板全局唯一标识（内置 5 个取值固定）"
    )
    name: str = Field(min_length=1, description="模板名称")
    kind: Literal["single_node", "recipe"] = Field(
        description="模板形态：单节点 / 多节点配方"
    )
    preset: TemplatePreset = Field(description="预设配置（键集合 ⊆ 白名单）")
    system_prompt: str = Field(min_length=1, description="提示词模板（非空）")
    allowed_tools: List[str] = Field(
        default_factory=list, description="工具选择集（映射 available_tools，去重）"
    )
    max_tool_rounds: int | None = Field(
        default=None,
        ge=1,
        description="单节点轮次上限（None=回落全局 agent_max_tool_rounds=5）",
    )
    output_max_retries: int | None = Field(
        default=None, ge=0, description="输出校验重试上限（None=回落全局 2）"
    )
    doc: TemplateDoc = Field(description="五维度说明文档（必填且五维度均非空）")
    tool_config: TemplateToolConfig = Field(description="工具调用配置")
    nodes: List[TemplateNodePreset] = Field(
        default_factory=list, description="配方节点预设（kind=recipe 时非空）"
    )
    edges: List[TemplateEdgePreset] = Field(
        default_factory=list, description="配方连线预设（kind=recipe 时非空）"
    )
    implementation_key_points: List[str] = Field(
        default_factory=list, description="实现关键点"
    )
    dependency_conditions: List[str] = Field(
        default_factory=list, description="依赖条件"
    )
    performance_boundaries: TemplatePerformanceBoundaries = Field(
        default_factory=TemplatePerformanceBoundaries, description="性能边界"
    )
    security_boundaries: List[str] = Field(
        default_factory=list, description="安全边界"
    )
    evaluation_metrics: List[TemplateMetric] = Field(
        default_factory=list, description="评价指标（含可采集性标注）"
    )

    @field_validator("allowed_tools")
    @classmethod
    def _dedupe_allowed_tools(cls, value: List[str]) -> List[str]:
        seen: List[str] = []
        for item in value:
            if item not in seen:
                seen.append(item)
        return seen

    @model_validator(mode="after")
    def _validate_kind_topology(self) -> "AgentTemplate":
        local_refs = [node.local_ref for node in self.nodes]
        if self.kind == "recipe":
            if len(self.nodes) < 2:
                raise ValueError("kind=recipe 时 nodes 至少 2 个节点")
            if len(local_refs) != len(set(local_refs)):
                raise ValueError("nodes 内 local_ref 必须唯一")
            known_refs = set(local_refs)
            for edge in self.edges:
                if edge.source_ref not in known_refs or edge.target_ref not in known_refs:
                    raise ValueError(
                        "edges 的 source_ref/target_ref 必须存在于 nodes 的 local_ref 集合"
                    )
        else:
            if self.nodes or self.edges:
                raise ValueError("kind=single_node 时 nodes/edges 必须为空")
        return self


class ParamSummary(BaseModel):
    """参数摘要（仅参数名与类型占位；不含示例值/默认值/凭据，INV-D08）。"""

    param: str = Field(min_length=1, description="参数名")
    type: str = Field(
        default="string", description="类型占位（string/number/boolean/array/object 等）"
    )


class ToolMarketEntry(BaseModel):
    """Tool 市场条目（spec §9.3；三源工具目录条目的市场侧包装，AC-7 1:1 派生）。"""

    tool_identifier: str = Field(
        min_length=1,
        description="工具标识（跨源唯一，冲突按 platform > mcp > local 保留）",
    )
    name: str = Field(min_length=1, description="工具名称（非空）")
    description: str = Field(default="", description="工具描述（可为空）")
    source: ToolSource = Field(
        description="来源（复用既有 ToolSource：platform/mcp/local）"
    )
    permission: ToolPermission = Field(
        default=ToolPermission.READONLY,
        description="权限级别（复用既有 ToolPermission：readonly/readwrite/admin）",
    )
    version: str = Field(default="1.0.0", min_length=1, description="版本号（缺省 1.0.0）")
    tags: List[str] = Field(default_factory=list, description="标签列表（分类检索依据）")
    input_schema_summary: List[ParamSummary] = Field(
        default_factory=list, description="输入参数摘要（仅参数名与类型占位）"
    )
    output_schema_summary: List[ParamSummary] = Field(
        default_factory=list, description="输出参数摘要（仅参数名与类型占位）"
    )


class MarketCategoryCount(BaseModel):
    """市场分类计数（分类聚合视图载荷，design §2.2.2 E-3 / E-9）。"""

    category: str = Field(
        min_length=1, description="分类名（Agent 市场=主分类；Tool 市场=tag）"
    )
    count: int = Field(ge=1, description="该分类下条目计数")


class AgentMarketEntryDetail(AgentMarketEntry):
    """Agent 市场条目详情（design §2.2.2 E-2）：基础条目 + 预设解析与模板引用。

    preset_ref 指向内置模板时附加 preset 与 template_ref（不内联五维度全文，T7.2）；
    未指向内置模板时两者为 None（基础条目字段保持不变）。
    """

    preset: Optional[TemplatePreset] = Field(
        default=None, description="配置预设（preset_ref 指向内置模板时附加）"
    )
    template_ref: Optional[str] = Field(
        default=None,
        description="模板详情引用（/api/v1/agent-market/templates/{template_id}）",
    )
