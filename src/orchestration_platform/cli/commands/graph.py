# =============================================================================
# graph 命令（cli/commands/graph.py）
#
# 职责（arch_review_v2 5.3，CHG-20）：
#   GraphCompiler 编译产物 → mermaid flowchart TD 文本；
#   节点形状按 NodeType 映射、条件边带标签（sanitize 后输出）。
#
# exit code：成功 0 / 失败 2（任务 5.3 口径，与 validate 的区分不同）。
# =============================================================================

from typing import Dict, List, Tuple

from orchestration_platform.cli.commands import (
    WorkflowInvalidError,
    WorkflowLoadError,
    compile_definition,
    parse_workflow_text,
    read_workflow_file,
)
from orchestration_platform.cli.output import (
    EXIT_LOAD_ERROR,
    EXIT_OK,
    emit_error,
    emit_json,
    emit_text,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.engine.abstractions.compiled_workflow import CompiledWorkflow

# mermaid 节点形状模板：{label} 为占位（节点标识·类型）。
# 形状语义：入口/触发 stadium、业务节点 rect、数据容器 cylinder、
# 控制流 rhombus、子流程/LLM subroutine、代码/插件 asymmetric、
# 监督者 double_circle、输出展示 circle、动态注入/向量库 hexagon 等。
_NODE_SHAPES: Dict[NodeType, str] = {
    NodeType.TRIGGER: "{id}([{label}])",
    NodeType.AGENT: "{id}[{label}]",
    NodeType.TOOL: "{id}({label})",
    NodeType.DATA_SOURCE: "{id}[({label})]",
    NodeType.CONTROL_FLOW: "{id}{{{label}}}",
    NodeType.SUBWORKFLOW: "{id}[[{label}]]",
    NodeType.DYNAMIC_INJECTOR: "{id}{{{label}}}",
    NodeType.DATA_TRANSFORM: "{id}[/{label}/]",
    NodeType.PLUGIN: "{id}>{label}]",
    NodeType.SUPERVISOR: "{id}((({label})))",
    NodeType.OUTPUT_DISPLAY: "{id}(({label}))",
    NodeType.VECTOR_KNOWLEDGE_BASE: "{id}{{{label}}}",
    NodeType.LLM: "{id}[[{label}]]",
    NodeType.CODE: "{id}>{label}]",
}
_DEFAULT_SHAPE = "{id}[{label}]"

# mermaid 标签/条件文本的危险字符替换表（防注入破坏图结构）
_LABEL_REPLACEMENTS = {
    '"': "",
    "'": "",
    "|": "｜",
    "[": "［",
    "]": "］",
    "{": "｛",
    "}": "｝",
    "(": "（",
    ")": "）",
    "<": "＜",
    ">": "＞",
    "#": "",
    "\n": " ",
    "\r": " ",
}


def _sanitize_text(text: str) -> str:
    """mermaid 标签安全化：替换破坏图语法的字符。"""
    for bad, good in _LABEL_REPLACEMENTS.items():
        text = text.replace(bad, good)
    return text.strip()


def _safe_node_id(node_id: str, used: Dict[str, str]) -> str:
    """节点标识 → mermaid 安全 id（非 word 字符替换为 _，冲突追加序号）。"""
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in node_id)
    if not safe:
        safe = "node"
    candidate = safe
    index = 2
    while candidate in used and used[candidate] != node_id:
        candidate = f"{safe}_{index}"
        index += 1
    used[candidate] = node_id
    return candidate


def _condition_label_lookup(compiled: CompiledWorkflow) -> Dict[Tuple[str, str], str]:
    """(source, target) → 条件表达式。

    分支元数据的 edge_id 为编译期合成值（cond_{node}_{target}），与声明边
    edge_id 不一致，故按 (source, target) 二元组匹配；普通边显式 condition
    优先级更高（后写入覆盖）。
    """
    lookup: Dict[Tuple[str, str], str] = {}
    for metas in compiled.conditional_edges.values():
        for meta in metas:
            if meta.expression:
                lookup[(meta.source_node_id, meta.target_node_id)] = meta.expression
    for edge in compiled.edges:
        if edge.condition:
            lookup[(edge.source_node_id, edge.target_node_id)] = edge.condition
    return lookup


def render_mermaid(compiled: CompiledWorkflow) -> str:
    """CompiledWorkflow → mermaid flowchart TD 文本（确定性输出：拓扑序节点、声明序边）。"""
    lines: List[str] = ["flowchart TD"]
    id_map: Dict[str, str] = {}

    for node in compiled.nodes:
        safe_id = _safe_node_id(node.node_id, id_map)
        label = _sanitize_text(f"{node.node_id}·{node.node_type.value}")
        template = _NODE_SHAPES.get(node.node_type, _DEFAULT_SHAPE)
        lines.append(f"    {template.format(id=safe_id, label=label)}")

    conditions = _condition_label_lookup(compiled)
    for edge in compiled.edges:
        source = _safe_node_id(edge.source_node_id, id_map)
        target = _safe_node_id(edge.target_node_id, id_map)
        condition = conditions.get((edge.source_node_id, edge.target_node_id))
        if condition:
            lines.append(f"    {source} -->|{_sanitize_text(condition)}| {target}")
        else:
            lines.append(f"    {source} --> {target}")

    return "\n".join(lines)


def run_graph(args) -> int:
    """graph 命令入口：解析 → 编译 → mermaid 输出（失败一律 exit 2）。"""
    import asyncio

    try:
        text = read_workflow_file(args.target)
        definition = parse_workflow_text(text)
        compiled = asyncio.run(compile_definition(definition))
    except (WorkflowLoadError, WorkflowInvalidError) as error:
        emit_error(str(error))
        return EXIT_LOAD_ERROR

    mermaid = render_mermaid(compiled)

    if getattr(args, "json", False):
        emit_json(
            {
                "definition_id": compiled.definition_id,
                "definition_version": compiled.definition_version,
                "mermaid": mermaid,
            }
        )
    else:
        emit_text(mermaid)
    return EXIT_OK
