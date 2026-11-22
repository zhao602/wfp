# =============================================================================
# 引擎输入装配服务（engine/input_assembly.py）
#
# 职责：节点输入装配规则唯一权威（unified_node_input DC-01 L2 / CHG-B03）。
# 设计意图：
#   1. 纯函数模块：入口接收调用方单次取得的共享状态快照（INV-D02 同快照），
#      不 import langgraph、不写状态、无 IO（INV-D01 / INV-03）；
#   2. 统一四态语义（FR-14）：键缺失 → 失败；键在值为 null → 正常传递；
#      键在值为空集合 → 正常传递；正常值 → 正常装配——
#      区分依赖 in 判断而非 .get() is None（subworkflow 先例）；
#   3. 一切失败经 AssemblyFailure 返回，不抛异常；引用级失败的 error_code 可被
#      调用方覆盖为既有类型码（DC-05：trigger/tool/subworkflow 显式传既有码，
#      保持既有测试与可观测契约；统一码为新场景缺省与收敛方向）；
#   4. 基线形态（顶格键，无字段路径）的装配结果与既有 .get() 读取逐字节等价
#      （INV-05①②零迁移）；字段路径为读取侧扩展，逐段在 dict 上取值；
#   5. 类型不匹配（路径中间值非 dict / expected_kind 校验失败）使用统一码
#      NODE_INPUT_TYPE_MISMATCH，不参与既有码覆盖（新增场景，DC-05）；
#   6. 形态 B（上游输入项引用 A::x，upstream_input_ref T4.2 / DC-03）：
#      查来源节点定义 → 取输入位置绑定 → 一次解析（不递归，INV-E13）→
#      路径下钻；node_definitions 未注入（缺省 None）时形态 B 一律失败、
#      形态 A 结果与扩展前一致（INV-E09 / INV-E02）。
# =============================================================================

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from orchestration_platform.domain.errors import (
    NODE_INPUT_REFERENCE_MISSING,
    NODE_INPUT_TYPE_MISMATCH,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.node_configurations import AgentInputEntry
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.values.reference_expression import (
    ParsedReference,
    parse_reference,
)
from orchestration_platform.domain.validation.node_configuration_validator import (
    extract_input_position_bindings,
)


@dataclass
class AssemblySuccess:
    """装配成功结果：values（键值/选择集形态）或 value（单一引用形态）。"""

    values: Dict[str, JsonValue] = field(default_factory=dict)
    value: Optional[JsonValue] = None


@dataclass
class AssemblyFailure:
    """装配失败结果：错误码（可被调用方覆盖为既有码）+ 含定位信息的错误消息。"""

    error_code: str
    error_message: str


# 装配函数族统一返回类型（判别方式：isinstance）
AssemblyOutcome = Union[AssemblySuccess, AssemblyFailure]


def _describe_value_kind(value: JsonValue) -> str:
    """描述值的运行时类别（与 NODE_OUTPUT_CATEGORIES 的 value_kind 取值域对齐）。

    dict → object；list → dataset；标量（str/int/float/bool/None）→ scalar。
    """
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "dataset"
    return "scalar"


def resolve_reference(
    reference: str,
    outputs_snapshot: Dict[str, JsonValue],
    *,
    node_definitions: Optional[Dict[str, NodeDefinition]] = None,
) -> AssemblyOutcome:
    """解析单条引用表达式并从快照取值（四态区分，design §2.1.3(1)）。

    判定顺序：
        1. 语法非法（parse_reference 为 None）→ AssemblyFailure(
           NODE_INPUT_REFERENCE_MISSING，消息含原始表达式与语法原因)；
        2. 形态 B（上游输入项引用 A::x，upstream_input_ref T4.2/DC-03）→
           转入 _resolve_input_position_reference 装配分支（查来源节点 →
           取绑定 → 一次解析不递归 → 路径下钻；node_definitions 缺省
           None 时一律失败，INV-E09）；
        3. 命名输出名不在快照（键缺失）→ AssemblyFailure(
           NODE_INPUT_REFERENCE_MISSING，消息含命名输出名与引用定位)；
        4. 无字段路径（基线形态）→ AssemblySuccess(整对象值，null 值正常装配)；
        5. 有字段路径 → 逐段在 dict 上取值；中间值非 dict 或段缺失
           → AssemblyFailure(NODE_INPUT_TYPE_MISMATCH，
           消息含期望类别 object 与实际类别，AC-07 场景 2)。

    形态 A 分支逐行不变（INV-E02）；node_definitions 仅形态 B 分支消费。
    """
    parsed = parse_reference(reference)
    if parsed is None:
        return AssemblyFailure(
            error_code=NODE_INPUT_REFERENCE_MISSING,
            error_message=(
                f"引用表达式语法非法：{reference!r}"
                "（合法形态：命名输出名，或命名输出名.字段路径）"
            ),
        )
    if parsed.kind == "input_position":
        return _resolve_input_position_reference(
            reference, parsed, outputs_snapshot, node_definitions
        )
    if parsed.output_name not in outputs_snapshot:
        return AssemblyFailure(
            error_code=NODE_INPUT_REFERENCE_MISSING,
            error_message=f"引用的命名输出 {parsed.output_name} 不存在（引用表达式：{reference}）",
        )

    current: JsonValue = outputs_snapshot[parsed.output_name]
    for index, segment in enumerate(parsed.path_segments):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            actual_kind = _describe_value_kind(current)
            return AssemblyFailure(
                error_code=NODE_INPUT_TYPE_MISMATCH,
                error_message=(
                    f"引用表达式 {reference} 字段路径第 {index + 1} 段「{segment}」取值失败："
                    f"期望类别 object，实际类别 {actual_kind}"
                ),
            )
    return AssemblySuccess(value=current)


def _resolve_input_position_reference(
    reference: str,
    parsed: ParsedReference,
    outputs_snapshot: Dict[str, JsonValue],
    node_definitions: Optional[Dict[str, NodeDefinition]],
) -> AssemblyOutcome:
    """形态 B（上游输入项引用）装配分支（upstream_input_ref T4.2 / DC-03 状态机）。

    判定顺序（design §2.1.3(2)）：
        1. 未注入 node_definitions → NODE_INPUT_REFERENCE_MISSING
           （形态 B 须由调度器注入节点定义索引，INV-E09）；
        2. 来源节点（parsed.output_name）不在索引 → NODE_INPUT_REFERENCE_MISSING；
        3. 来源节点无该输入位置的绑定 / 绑定为空 → NODE_INPUT_REFERENCE_MISSING；
        4. 绑定本身为形态 B → NODE_INPUT_REFERENCE_MISSING（消息含
           「不支持递归穿透」；装配深度恒为 1，INV-E13）；
        5. 绑定为形态 A → 一次 resolve_reference(binding, outputs_snapshot)
           得值 V（不传 node_definitions——结构性保证不递归）；
        6. 引用带字段路径 → 在 V 上逐段下钻（与既有下钻同口径）；
           中间值非 dict → NODE_INPUT_TYPE_MISMATCH。
    """
    scope_node_id = parsed.output_name  # 形态 B 首段 = 来源节点标识
    if node_definitions is None:
        return AssemblyFailure(
            error_code=NODE_INPUT_REFERENCE_MISSING,
            error_message=(
                f"引用的上游输入项不可解析：{reference}"
                "（未注入节点定义索引，形态 B 引用须由调度器注入 node_definitions）"
            ),
        )
    scope_node = node_definitions.get(scope_node_id)
    if scope_node is None:
        return AssemblyFailure(
            error_code=NODE_INPUT_REFERENCE_MISSING,
            error_message=(
                f"引用的上游输入项不存在：{reference}"
                f"（来源节点 {scope_node_id} 不在本工作流定义内）"
            ),
        )
    binding = extract_input_position_bindings(scope_node).get(parsed.input_position)
    if not binding:
        return AssemblyFailure(
            error_code=NODE_INPUT_REFERENCE_MISSING,
            error_message=(
                f"引用的上游输入项不存在：{reference}"
                f"（来源节点 {scope_node_id} 的输入位置「{parsed.input_position}」无绑定引用）"
            ),
        )
    binding_parsed = parse_reference(binding)
    if binding_parsed is not None and binding_parsed.kind == "input_position":
        return AssemblyFailure(
            error_code=NODE_INPUT_REFERENCE_MISSING,
            error_message=(
                f"引用的上游输入项不可递归解析：{reference}"
                f"（来源节点 {scope_node_id} 的输入位置「{parsed.input_position}」"
                f"绑定为形态 B 引用 {binding}，不支持递归穿透）"
            ),
        )
    bound_outcome = resolve_reference(binding, outputs_snapshot)
    if isinstance(bound_outcome, AssemblyFailure):
        return AssemblyFailure(
            error_code=bound_outcome.error_code,
            error_message=(
                f"引用的上游输入项装配失败：{reference}"
                f"（来源绑定 {binding} 解析失败：{bound_outcome.error_message}）"
            ),
        )

    current: JsonValue = bound_outcome.value
    for index, segment in enumerate(parsed.path_segments):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            actual_kind = _describe_value_kind(current)
            return AssemblyFailure(
                error_code=NODE_INPUT_TYPE_MISMATCH,
                error_message=(
                    f"引用表达式 {reference} 字段路径第 {index + 1} 段「{segment}」取值失败："
                    f"期望类别 object，实际类别 {actual_kind}"
                ),
            )
    return AssemblySuccess(value=current)


def assemble_keyed_mapping(
    mapping: Dict[str, str],
    outputs_snapshot: Dict[str, JsonValue],
    *,
    error_code: str = NODE_INPUT_REFERENCE_MISSING,
    node_definitions: Optional[Dict[str, NodeDefinition]] = None,
) -> AssemblyOutcome:
    """键值映射形态装配（trigger input_template / tool argument_mapping /
    subworkflow input_mapping / plugin input_mapping 预留）。

    逐条目 resolve_reference，返回「输入位置名 → 已解析值」：
        - 引用级失败（语法非法/键缺失）：收集全部失败引用后整体失败
          （消息含缺失引用清单，供调用方按既有形态补充类型级前缀，DC-05；
          error_code 缺省统一码，可被调用方覆盖为既有类型码）；
        - 路径类型不匹配（新增场景）：立即失败并使用统一码
          NODE_INPUT_TYPE_MISMATCH（不参与既有码覆盖）；
        - node_definitions（upstream_input_ref T4.2）：形态 B 解析所需
          节点定义索引，透传至 resolve_reference（缺省 None → 形态 B
          一律失败，INV-E09）。
    """
    resolved: Dict[str, JsonValue] = {}
    missing_references: List[str] = []
    for param_name, reference in mapping.items():
        outcome = resolve_reference(
            reference, outputs_snapshot, node_definitions=node_definitions
        )
        if isinstance(outcome, AssemblyFailure):
            if outcome.error_code == NODE_INPUT_TYPE_MISMATCH:
                return outcome
            missing_references.append(reference)
            continue
        resolved[param_name] = outcome.value

    if missing_references:
        return AssemblyFailure(
            error_code=error_code,
            error_message=f"缺失字段：{', '.join(missing_references)}",
        )
    return AssemblySuccess(values=resolved)


def assemble_agent_inputs(
    entries: List[AgentInputEntry],
    outputs_snapshot: Dict[str, JsonValue],
    *,
    node_definitions: Optional[Dict[str, NodeDefinition]] = None,
) -> AssemblyOutcome:
    """Agent 选择集形态装配（DC-03 显式模式，失败 fail-fast）。

    逐条目 resolve_reference；成功值按「alias ?? 命名输出名」组织为 名字 → 值
    （FR-09）；任一条目失败 → 整体失败，错误码透传该条目的解析失败码
    （NODE_INPUT_REFERENCE_MISSING / NODE_INPUT_TYPE_MISMATCH），消息含条目
    引用与缺失目标名（spec §9.4.1；节点级 node_id 定位由调用方补充）。
    缺省名（upstream_input_ref T4.2/CHG-B04）：形态 A → 命名输出名；
    形态 B → 回退为 input_position（output_name 为来源节点标识，非稳定命名）。
    """
    resolved: Dict[str, JsonValue] = {}
    for entry in entries:
        outcome = resolve_reference(
            entry.reference, outputs_snapshot, node_definitions=node_definitions
        )
        if isinstance(outcome, AssemblyFailure):
            return AssemblyFailure(
                error_code=outcome.error_code,
                error_message=f"输入条目装配失败：{outcome.error_message}",
            )
        parsed = parse_reference(entry.reference)
        if entry.alias:
            name = entry.alias
        elif parsed is not None:
            # 形态 B 缺省名回退为输入位置名（CHG-B04；input_position 为稳定寻址键）
            name = (
                parsed.input_position
                if parsed.kind == "input_position"
                else parsed.output_name
            )
        else:
            name = entry.reference
        resolved[name] = outcome.value
    return AssemblySuccess(values=resolved)


def assemble_single_reference(
    reference: Optional[str],
    outputs_snapshot: Dict[str, JsonValue],
    *,
    missing_is_none: bool = False,
    expected_kind: Optional[str] = None,
    node_definitions: Optional[Dict[str, NodeDefinition]] = None,
) -> AssemblyOutcome:
    """单一引用形态装配（data_transform 主/副引用 / output_display 容错特例）。

    - reference 为 None → AssemblySuccess(None)（未配置引用，调用方走既有缺省分支）；
    - missing_is_none=True → 解析失败（缺失/类型不匹配）均降级为
      AssemblySuccess(None)（output_display 容错特例，DC-05/FR-13：节点成功
      以占位展示，既有 Q-1 语义不回退）；
    - expected_kind（如 "dataset"）→ 装配值运行时类别不符时失败
      （NODE_INPUT_TYPE_MISMATCH，消息含期望类别与实际类别，DC-10）；
      null 值为非 list，expected_kind="dataset" 时同样失败（DC-10 态 2）；
    - node_definitions（upstream_input_ref T4.2）：形态 B 解析所需节点定义
      索引，透传至 resolve_reference（缺省 None → 形态 B 一律失败，INV-E09）。
    """
    if reference is None:
        return AssemblySuccess(value=None)

    outcome = resolve_reference(
        reference, outputs_snapshot, node_definitions=node_definitions
    )
    if isinstance(outcome, AssemblyFailure):
        if missing_is_none:
            return AssemblySuccess(value=None)
        return outcome

    if expected_kind is not None:
        actual_kind = _describe_value_kind(outcome.value)
        if actual_kind != expected_kind:
            return AssemblyFailure(
                error_code=NODE_INPUT_TYPE_MISMATCH,
                error_message=(
                    f"引用表达式 {reference} 类别不匹配："
                    f"期望类别 {expected_kind}，实际类别 {actual_kind}"
                ),
            )
    return AssemblySuccess(value=outcome.value)


def assemble_reference_collection(
    references: List[str],
    outputs_snapshot: Dict[str, JsonValue],
    *,
    missing_is_none: bool = False,
) -> AssemblyOutcome:
    """多引用集合装配（output_display 多选形态，OP-06 / CHG-03）。

    逐条 resolve_reference；与 assemble_agent_inputs 的 fail-fast 不同，
    output_display 语义为"逐条容错"：
      - missing_is_none=True（output_display 调用侧）→ 任一条解析失败（语法
        非法/键缺失/类型不匹配）均以 None 占位并继续，全部条目并入结果
        （INV-12 不回退，DC-07）；
      - missing_is_none=False → 首条失败即整体失败（透传该条 error_code），
        供未来可能复用（不在本任务调用路径）。
    组织形态：引用原文 → 值（保序，键取引用表达式原文，与勾选顺序一致）。
    单元素调用方不应走本入口（executor 对 1 条走 assemble_single_reference，
    保证单条结果与扩展前逐字节一致，DC-06/AC-09）。
    """
    resolved: Dict[str, JsonValue] = {}
    for reference in references:
        outcome = resolve_reference(reference, outputs_snapshot)
        if isinstance(outcome, AssemblyFailure):
            if missing_is_none:
                resolved[reference] = None
                continue
            return outcome
        resolved[reference] = outcome.value
    return AssemblySuccess(values=resolved)


# =============================================================================
# llm_and_code_nodes（CHG-09 / DC-07 / FR-07~FR-09 / FR-43 / AC-10~AC-12 / AC-46）
# =============================================================================


def assemble_user_message(
    inputs: Optional[List[AgentInputEntry]],
    outputs_snapshot: Dict[str, JsonValue],
    user_prompt: str,
) -> Union[str, AssemblyFailure]:
    """组装大模型节点用户消息文本（llm_and_code_nodes CHG-09 / DC-07）。

    上下文文本三态（与 agent 节点 _build_user_message 同口径，AC-46 对照）：
        - inputs is None → 隐式全量：outputs_snapshot 全量平铺为
          「名: 值」并按换行连接；
        - inputs == []  → 显式空清单：上下文文本为空串；
        - 非空 → 复用 assemble_agent_inputs 装配，成功值按
          「alias ?? 命名输出名: 值」平铺（清单外输出不进入，FR-09）。

    最终文本顺序固定：「上下文文本」+ "\\n" + user_prompt；
    两者皆空 → 空串；仅一方存在 → 只取该方（不产生前后空行）。
    {{...}} 一律按普通文本原样保留，无替换逻辑（AC-12）。

    装配失败返回 AssemblyFailure（error_code + 定位信息），由调用方构造
    失败结果（fail-fast；错误码为 NODE_INPUT_REFERENCE_MISSING /
    NODE_INPUT_TYPE_MISMATCH 透传）。
    """
    if inputs is None:
        context_text = "\n".join(
            f"{name}: {value}" for name, value in outputs_snapshot.items()
        )
    elif not inputs:
        context_text = ""
    else:
        outcome = assemble_agent_inputs(inputs, outputs_snapshot)
        if isinstance(outcome, AssemblyFailure):
            return outcome
        context_text = "\n".join(
            f"{name}: {value}" for name, value in outcome.values.items()
        )

    if context_text and user_prompt:
        return f"{context_text}\n{user_prompt}"
    return context_text or user_prompt


# =============================================================================
# llm_code_nodes_v2（任务 3.1 / CHG-06 / DC-04-7 / OP-03 / FR-14 / AC-15 / ZD-04）
# =============================================================================


def assemble_params(
    inputs: Optional[List[AgentInputEntry]],
    outputs_snapshot: Dict[str, JsonValue],
) -> AssemblyOutcome:
    """组装代码节点 params 注入字典（llm_code_nodes_v2 CHG-06 / DC-04-7 / OP-03）。

    三态语义（与 assemble_user_message 同口径，design §4.1）：
        - inputs is None → 隐式全量：outputs_snapshot 全量平铺（浅拷贝为新
          dict，键 = 命名输出名；值原样透传，不深拷贝、不类型转换）；
        - inputs == []  → 显式空清单：params == {}；
        - 非空 → 复用 assemble_agent_inputs 逐条 resolve_reference 装配，
          键 = alias 优先、缺省为被引用命名输出名（非引用表达式原文）。

    值域为 JsonValue（不引入 Any 散逸）；失败透传 AssemblyFailure
    （NODE_INPUT_REFERENCE_MISSING / NODE_INPUT_TYPE_MISMATCH），脚本不执行。
    只读语义为契约级约定（普通 dict，不做 MappingProxyType）：脚本内对
    params 的重赋值仅影响自身命名空间（design §4.1 / D-08）。
    """
    if inputs is None:
        return AssemblySuccess(values=dict(outputs_snapshot))
    if not inputs:
        return AssemblySuccess(values={})
    return assemble_agent_inputs(inputs, outputs_snapshot)
