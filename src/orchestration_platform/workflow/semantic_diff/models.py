# =============================================================================
# 语义 diff 报告模型（workflow/semantic_diff/models.py）
#
# 职责（arch_review_v2 6.1，CHG-16）：定义三类变更的判定结果结构——
# 变更种类枚举、单条变更、汇总报告；与既有领域模型风格一致（Pydantic v2）。
#
# 契约（cli/commands/diff.py 按该契约消费）：
#   SemanticDiffReport{changes: [DiffChange{kind, target, detail}], summary}
# =============================================================================

from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class DiffChangeKind(str, Enum):
    """语义变更种类（判定顺序锁定：拓扑 > 参数 > 布局，DC-12）。

    TOPOLOGY_CHANGE —— 拓扑变更：边集合差异（新增/删除/目标重定向）、
                       条件文本差异（source+target+condition 三元组比对）、
                       节点增删、入口变更
    PARAM_CHANGE    —— 参数变更：拓扑签名一致，但节点 configuration
                       dict 深比对不一致（忽略键序）
    LAYOUT_CHANGE   —— 布局变更：拓扑与参数均一致，仅声明顺序或
                       画布位置元数据变化（语义等价）
    """

    TOPOLOGY_CHANGE = "topology-change"
    PARAM_CHANGE = "param-change"
    LAYOUT_CHANGE = "layout-change"


class DiffChange(BaseModel):
    """单条语义变更：种类 + 定位目标 + 人读明细。"""

    kind: DiffChangeKind = Field(description="变更种类")
    target: str = Field(description="定位目标（节点/连线/入口标识）")
    detail: str = Field(description="人读明细")


class SemanticDiffReport(BaseModel):
    """语义 diff 汇总报告：变更清单 + 汇总结要。"""

    changes: List[DiffChange] = Field(default_factory=list, description="变更清单")
    summary: str = Field(default="", description="汇总结要")
