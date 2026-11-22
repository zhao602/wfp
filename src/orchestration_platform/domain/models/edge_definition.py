# =============================================================================
# 连线定义模型（domain/models/edge_definition.py）
#
# 职责：定义节点之间的有向连线——标识、起点、终点、可选条件表达式。
# 设计意图：连线仅表达「有向依赖」；循环语义必须由循环控制流节点显式表达，
#          普通连线构成的环一律非法（对应需求规格 5.1.1.3）。
# =============================================================================

from typing import Optional

from pydantic import BaseModel, Field


class EdgeDefinition(BaseModel):
    """连线定义。

    字段：
        edge_id        —— 连线标识（同定义内唯一，供画布定位）
        source_node_id —— 起点节点标识
        target_node_id —— 终点节点标识
        condition      —— 可选条件表达式（普通条件边使用；分支节点亦可使用配置内条件）
    """

    edge_id: str = Field(description="连线标识（同定义内唯一）")
    source_node_id: str = Field(description="起点节点标识")
    target_node_id: str = Field(description="终点节点标识")
    condition: Optional[str] = Field(default=None, description="可选条件表达式")
