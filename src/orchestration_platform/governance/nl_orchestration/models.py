# =============================================================================
# NL 编排领域模型（governance/nl_orchestration/models.py）
#
# 职责：定义自然语言编排请求、结果与中间表示模型。
# 设计意图（对应偏差修正 spec 6.1 / design 2.3.2 类图）：
#   - NLOrchestrationRequest：封装用户指令、意图类型、目标定义标识
#   - NLOrchestrationResult：封装生成结果（YAML文本+定义+校验报告）或错误
#   - ParsedIntent：LLM 解析产出的结构化中间表示（节点列表+边列表+修改操作）
#   - InjectionCheckResult：注入防护校验结果
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.common import ValidationReport
from orchestration_platform.domain.models.enums import (
    IntentType,
    NLErrorCode,
    NodeType,
)
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition


def _utc_now() -> datetime:
    """UTC 当前时间工厂。"""
    return datetime.now(timezone.utc)


class ParsedNodeSpec(BaseModel):
    """LLM 解析产出的节点规格（中间表示）。

    由 NLIntentParser 从 LLM 返回中解析得到，后续由 YamlGenerator 映射为 NodeDefinition。
    """

    node_id: str = Field(description="节点标识（同定义内唯一）")
    name: str = Field(default="", description="节点名称")
    node_type: NodeType = Field(description="节点类型")
    configuration: Dict[str, Any] = Field(default_factory=dict, description="节点配置键值对")
    description: str = Field(default="", description="节点用途说明（供 LLM 理解上下文）")


class ParsedEdgeSpec(BaseModel):
    """LLM 解析产出的连线规格（中间表示）。"""

    source_node_id: str = Field(description="源节点标识")
    target_node_id: str = Field(description="目标节点标识")
    condition: Optional[str] = Field(default=None, description="条件表达式（条件边）")


class NodeModification(BaseModel):
    """节点修改操作（修改意图专用）。

    action —— INSERT（在 anchor_node_id 之后/之前插入）、DELETE（删除）、UPDATE（更新配置）
    """

    action: str = Field(description="修改动作：insert / delete / update")
    target_node_id: Optional[str] = Field(default=None, description="被修改的既有节点标识")
    anchor_node_id: Optional[str] = Field(default=None, description="插入锚点节点标识")
    insert_position: Optional[str] = Field(default=None, description="插入位置：before / after")
    node_spec: Optional[ParsedNodeSpec] = Field(default=None, description="新增/更新节点规格")
    edge_specs: List[ParsedEdgeSpec] = Field(default_factory=list, description="伴随新增的连线")


class ParsedIntent(BaseModel):
    """LLM 意图解析产出的结构化中间表示。

    创建意图：填充 nodes + edges，modifications 为空。
    修改意图：填充 modifications，nodes + edges 为空。
    """

    intent_type: IntentType = Field(description="意图类型")
    workflow_name: str = Field(default="", description="工作流名称（创建意图）")
    nodes: List[ParsedNodeSpec] = Field(default_factory=list, description="节点规格列表（创建意图）")
    edges: List[ParsedEdgeSpec] = Field(default_factory=list, description="连线规格列表（创建意图）")
    modifications: List[NodeModification] = Field(default_factory=list, description="修改操作列表（修改意图）")
    description: str = Field(default="", description="工作流用途描述")


class InjectionCheckResult(BaseModel):
    """注入防护校验结果。"""

    passed: bool = Field(description="是否通过校验")
    reason: Optional[str] = Field(default=None, description="未通过原因")
    detected_patterns: List[str] = Field(default_factory=list, description="检测到的注入模式")


class NLOrchestrationRequest(BaseModel):
    """自然语言编排请求。

    对应 design 2.2.2.1 的 create_from_nl / modify_from_nl 入参聚合。
    """

    directive: str = Field(description="自然语言指令文本")
    intent_type: IntentType = Field(description="意图类型")
    target_definition_id: Optional[str] = Field(default=None, description="目标工作流定义标识（修改意图必填）")
    target_version: Optional[int] = Field(default=None, description="目标工作流定义版本（修改意图必填）")
    tenant_id: str = Field(description="租户标识")
    user_id: str = Field(description="用户标识")
    occurred_at: datetime = Field(default_factory=_utc_now, description="请求时间")


class NLOrchestrationResult(BaseModel):
    """自然语言编排结果。

    success=True 时 yaml_text / definition / validation_report 有效。
    success=False 时 error_code / error_message 有效。
    """

    success: bool = Field(description="是否成功")
    yaml_text: Optional[str] = Field(default=None, description="生成的 YAML 文本")
    definition: Optional[WorkflowDefinition] = Field(default=None, description="生成的工作流定义")
    validation_report: Optional[ValidationReport] = Field(default=None, description="拓扑校验报告")
    error_code: Optional[NLErrorCode] = Field(default=None, description="错误码")
    error_message: Optional[str] = Field(default=None, description="错误描述")
    correction_rounds: int = Field(default=0, description="LLM 自纠错轮次")
