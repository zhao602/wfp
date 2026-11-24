# =============================================================================
# 工作流定义模型（domain/models/workflow_definition.py）
#
# 职责：定义一份完整的工作流定义——五要素（版本号/节点/连线/输入/输出）+ 元信息。
# 设计意图：
#   1. 严格对齐需求规格 6.1 数据约束（标识、名称、版本、节点、连线、输入输出、时间）；
#   2. 提供图遍历辅助方法（入口、后继、前驱、节点索引），供校验器与引擎复用，
#      避免各处重复实现图访问逻辑。
# =============================================================================

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.common import InputParameter, OutputDeclaration
from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_definition import NodeDefinition


def _utc_now() -> datetime:
    """返回当前 UTC 时间（统一时间基准，避免本地时区差异）。"""
    return datetime.now(timezone.utc)


# 定义发布状态常量（B11 定义版本与发布态；自由字符串，与作业状态常量风格一致）
DEFINITION_STATUS_DRAFT = "draft"
DEFINITION_STATUS_PUBLISHED = "published"


class WorkflowDefinition(BaseModel):
    """工作流定义（对应需求规格 6.1）。"""

    definition_id: str = Field(description="定义标识（全局唯一）")
    name: str = Field(max_length=100, description="定义名称（长度 ≤ 100）")
    spec_version: str = Field(description="规范版本号（必填）")
    definition_version: int = Field(default=1, ge=1, description="定义版本号（每次保存递增）")
    nodes: List[NodeDefinition] = Field(description="节点列表（至少含一个触发器节点）")
    edges: List[EdgeDefinition] = Field(default_factory=list, description="连线列表（可为空）")
    inputs: List[InputParameter] = Field(default_factory=list, description="输入参数定义")
    outputs: List[OutputDeclaration] = Field(default_factory=list, description="输出定义")
    # 发布状态机（B11 定义版本与发布态）：draft（可改写，同号幂等覆盖）/
    # published（发布态不可变——被 save 改写 → WF_STATE_CONFLICT/409）；
    # 旧数据缺字段默认 draft（与 v1_10 迁移 DEFAULT 'draft' 口径一致）
    status: Literal["draft", "published"] = Field(
        default=DEFINITION_STATUS_DRAFT,
        description="发布状态（draft=草稿可改写；published=发布态不可变）",
    )
    draft_of: Optional[int] = Field(
        default=None,
        ge=1,
        description="本版本基于的已发布版本号（保存时指向最新 published；发布回滚时承载 to_version 溯源）",
    )
    published_at: Optional[datetime] = Field(
        default=None, description="发布时刻（draft 态为 None）"
    )
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")

    # ------------------------- 图访问辅助方法 -------------------------

    def node_index(self) -> Dict[str, NodeDefinition]:
        """返回 node_id -> NodeDefinition 的索引字典。"""
        return {node.node_id: node for node in self.nodes}

    def get_node(self, node_id: str) -> Optional[NodeDefinition]:
        """按标识获取节点，不存在返回 None。"""
        return self.node_index().get(node_id)

    def trigger_nodes(self) -> List[NodeDefinition]:
        """返回全部触发器节点（入口节点候选）。"""
        return [node for node in self.nodes if node.node_type == NodeType.TRIGGER]

    def successors(self, node_id: str) -> List[EdgeDefinition]:
        """返回以 node_id 为起点的出边列表（保持声明顺序，保证确定性）。"""
        return [edge for edge in self.edges if edge.source_node_id == node_id]

    def predecessors(self, node_id: str) -> List[EdgeDefinition]:
        """返回以 node_id 为终点的入边列表（保持声明顺序）。"""
        return [edge for edge in self.edges if edge.target_node_id == node_id]

    def terminal_node_ids(self) -> List[str]:
        """返回无出边的节点标识列表（终点节点候选）。"""
        sources = {edge.source_node_id for edge in self.edges}
        return [node.node_id for node in self.nodes if node.node_id not in sources]
