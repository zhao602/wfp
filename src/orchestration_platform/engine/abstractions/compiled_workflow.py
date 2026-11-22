# =============================================================================
# 编译后的工作流中间表示（engine/abstractions/compiled_workflow.py）
#
# 职责：定义框架无关的可执行图中间表示 CompiledWorkflow。
# 设计意图（对应 design 2.2.2.2、spec 5.2.1.1）：
#   1. 仅含节点清单、边清单、入口节点、条件边元数据，不暴露任何 LangGraph 类型；
#   2. 节点顺序在编译期固定为拓扑序，为确定性调度奠定基础；
#   3. 条件边元数据记录分支表达式，供调度器求值使用。
# =============================================================================

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.node_definition import NodeDefinition


class ConditionalEdgeMeta(BaseModel):
    """条件边元数据：记录分支表达式与默认分支标记。

    用于条件分支节点的出边——调度器按表达式求值结果选择实际后继。
    """

    edge_id: str = Field(description="连线标识")
    source_node_id: str = Field(description="分支节点标识")
    target_node_id: str = Field(description="候选目标节点标识")
    expression: Optional[str] = Field(
        default=None, description="分支条件表达式（为真时选择此边）"
    )
    is_default: bool = Field(default=False, description="是否为默认分支")


class ParallelRegionMeta(BaseModel):
    """并行扇出区域元数据（B6 roadmap_b6_parallel_fanout 编译期识别产物）。

    由 GraphCompiler._extract_parallel_regions 生成，仅严格扇出形状激活
    （design §3.1 激活判定全项满足）；非活跃声明不产出元数据、维持单游标
    语义 + 编译警告（examples 串行化形态与 agent 模板零回归）。
    """

    join_node_id: str = Field(description="汇聚节点标识（PARALLEL_JOIN）")
    fork_source_node_id: str = Field(
        description="分叉源节点标识（非控制流；出边目标集合 == 分支入口集合）"
    )
    branch_entry_ids: List[str] = Field(description="分支入口节点标识列表（声明顺序）")
    branch_paths: Dict[str, List[str]] = Field(
        description="分支入口标识 -> 路径节点序（含入口、不含 join；线性、兄弟不相交）"
    )
    wait_policy: str = Field(description="等待策略（JoinWaitPolicy 值）")
    wait_count: Optional[int] = Field(default=None, description="wait_n_of_m 等待数")
    failure_strategy: str = Field(description="失败策略（ParallelFailureStrategy 值）")


class CompiledWorkflow(BaseModel):
    """编译后的工作流中间表示（框架无关）。

    字段：
        definition_id      —— 源定义标识
        definition_version —— 源定义版本
        nodes              —— 节点清单（已按拓扑序排列，保证确定性调度）
        edges              —— 连线清单
        entry_node_id      —— 入口节点标识（触发器节点）
        terminal_node_ids  —— 终点节点标识列表（无出边节点）
        conditional_edges  —— 条件边元数据表：分支节点 ID -> 条件边列表
        node_order         —— 拓扑序节点标识列表（与 nodes 顺序一致，显式冗余便于调试）
        parallel_regions   —— 并行扇出区域元数据表（B6）：join_node_id -> 区域；
                              空 = 无激活区域，调度器走原单游标路径（p95 天然达标）
    """

    definition_id: str = Field(description="源定义标识")
    definition_version: int = Field(description="源定义版本")
    nodes: List[NodeDefinition] = Field(description="节点清单（拓扑序）")
    edges: List[EdgeDefinition] = Field(description="连线清单")
    entry_node_id: str = Field(description="入口节点标识")
    terminal_node_ids: List[str] = Field(default_factory=list, description="终点节点标识列表")
    conditional_edges: Dict[str, List[ConditionalEdgeMeta]] = Field(
        default_factory=dict, description="条件边元数据：分支节点 ID -> 条件边列表"
    )
    node_order: List[str] = Field(default_factory=list, description="拓扑序节点标识列表")
    parallel_regions: Dict[str, ParallelRegionMeta] = Field(
        default_factory=dict,
        description="并行扇出区域元数据：join_node_id -> 区域（B6；空 = 无激活区域）",
    )

    def get_node(self, node_id: str) -> Optional[NodeDefinition]:
        """按标识获取节点。"""
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def successors(self, node_id: str) -> List[EdgeDefinition]:
        """返回以 node_id 为起点的出边列表（保持声明顺序）。"""
        return [edge for edge in self.edges if edge.source_node_id == node_id]

    def region_by_fork_source(self, node_id: str) -> Optional[ParallelRegionMeta]:
        """按分叉源节点标识查激活区域（无则 None；调度器 S 完成后取区域）。"""
        for region in self.parallel_regions.values():
            if region.fork_source_node_id == node_id:
                return region
        return None

    def region_containing(self, node_id: str) -> Optional[ParallelRegionMeta]:
        """查节点所属激活区域（分叉源/分支路径内/汇聚节点均算；无则 None）。"""
        for region in self.parallel_regions.values():
            if node_id in (region.fork_source_node_id, region.join_node_id):
                return region
            for path in region.branch_paths.values():
                if node_id in path:
                    return region
        return None
