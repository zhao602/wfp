# =============================================================================
# 节点输出类别登记表（domain/values/node_output_categories.py）
#
# 职责：后端侧节点输出类别唯一权威登记（unified_node_input DC-04 / CHG-B02），
#       供加载期静态类型校验作参照（referable / value_kind）。
# 设计意图：
#   1. 后端校验器是 domain 层组件，不能 import 前端契约表——本表为后端权威；
#   2. 未登记类型（plugin / supervisor / output_display 透传输出）查询返回 None，
#      校验侧安全降级为跳过类别规则（不因未知类型崩溃，OP-07）；
#   3. Agent 的 output_schema 作为实例级细化参照由校验器直接读取（不经此表）。
#
# 与前端 web/src/canvas/nodeOutputContracts.ts 的 NODE_OUTPUT_CONTRACTS 逐类对齐表
# （DFX §4.4.1 前后端对齐显式记录）：
# | 后端 NodeType     | 本表 value_kind | 本表 referable | 前端 category 文案        | 前端 referable |
# |-------------------|-----------------|----------------|--------------------------|----------------|
# | TRIGGER           | keyed           | True           | 输入参数集合（键值对）      | true           |
# | AGENT             | object          | True           | 结构化对象                 | true           |
# | TOOL              | object          | True           | 工具调用结果               | true           |
# | DATA_SOURCE       | dataset         | True           | 数据集（记录集合）          | true           |
# | CONTROL_FLOW      | signal          | False          | 控制信号                   | false          |
# | SUBWORKFLOW       | keyed           | True           | 键值对集合                 | true           |
# | DYNAMIC_INJECTOR  | object          | True           | 注入清单                   | true           |
# | DATA_TRANSFORM    | dataset         | True           | 数据集（记录集合）          | true           |
# | OUTPUT_DISPLAY    | （未登记）       | —              | 上游输出透传               | true           |
# | PLUGIN            | （未登记）       | —              | 前端未建模（BACKEND_ONLY）  | —              |
# | SUPERVISOR        | （未登记）       | —              | 前端未建模（BACKEND_ONLY）  | —              |
# | LLM               | object          | True           | 结构化对象                 | true           |
# | CODE              | object          | True           | 函数返回值（result）        | true           |
#
# OUTPUT_DISPLAY 差异说明：其输出为上游命名输出的动态透传（类别随被透传值变化，
# 无法静态判定），故本表不登记——校验侧查询返回 None 即安全跳过类别规则；
# 前端 referable=true 仅用于编辑期候选展示，两侧语义不冲突（透传输出仍可被引用，
# 仅不做类别级静态判定）。
#
# LLM/CODE 登记说明（llm_code_nodes_v2 修订 DC-13-4/DC-13-5，取代 llm_and_code_nodes
# / CHG-05 v1 拟定）：NodeType 全量 14 类，本表现登记 10 类；LLM 缺省产出
# {"content": <字符串>}（声明 output_schema 时为符合结构的对象），可被下游引用
# （referable=True，值类别 object）；CODE 产出命名输出 {"result": <value>}
# （output_name 缺省取 node_id），下游可引用（referable=True，值类别 object——v1 的
# referable=False 因「不执行」前提失效，v2 design.md :337）；前端契约表同步
# referable=true（nodeOutputContracts.ts，llm_code_nodes_v2 P6 已落）。
# =============================================================================

from dataclasses import dataclass
from typing import Dict, Optional

from orchestration_platform.domain.models.enums import NodeType

# value_kind 取值域："object" | "dataset" | "keyed" | "scalar" | "signal"


@dataclass(frozen=True)
class OutputCategoryInfo:
    """输出类别登记项（与前端 nodeOutputContracts 对齐记录）。"""

    referable: bool
    value_kind: str


NODE_OUTPUT_CATEGORIES: Dict[NodeType, OutputCategoryInfo] = {
    NodeType.AGENT: OutputCategoryInfo(referable=True, value_kind="object"),
    NodeType.DATA_SOURCE: OutputCategoryInfo(referable=True, value_kind="dataset"),
    NodeType.DATA_TRANSFORM: OutputCategoryInfo(referable=True, value_kind="dataset"),
    NodeType.TOOL: OutputCategoryInfo(referable=True, value_kind="object"),
    NodeType.TRIGGER: OutputCategoryInfo(referable=True, value_kind="keyed"),
    NodeType.SUBWORKFLOW: OutputCategoryInfo(referable=True, value_kind="keyed"),
    NodeType.DYNAMIC_INJECTOR: OutputCategoryInfo(referable=True, value_kind="object"),
    NodeType.CONTROL_FLOW: OutputCategoryInfo(referable=False, value_kind="signal"),
    # 大模型/代码节点输出契约（llm_code_nodes_v2 / DC-13-4/DC-13-5）：
    # LLM 缺省产出 {"content": <字符串>}（声明 output_schema 时为结构化对象），可引用；
    # CODE 产出命名输出 {"result": <value>}，可引用（v1 referable=False 因「不执行」前提失效）。
    NodeType.LLM: OutputCategoryInfo(referable=True, value_kind="object"),
    NodeType.CODE: OutputCategoryInfo(referable=True, value_kind="object"),
}


def resolve_output_category(node_type: NodeType) -> Optional[OutputCategoryInfo]:
    """按节点类型查输出类别；未登记类型返回 None（校验侧安全降级为不过滤）。"""
    return NODE_OUTPUT_CATEGORIES.get(node_type)
