# =============================================================================
# 引用表达式解析器（domain/values/reference_expression.py）
#
# 职责：统一节点输入引用表达式的语法解析（后端单一权威，
#       unified_node_input DC-02 + upstream_input_ref DC-01 形态 B 扩展）。
# 设计意图：
#   1. 引用表达式两种形态，消歧只看首段（第一个 `.` 之前，DC-01）：
#      形态 A（基线）= 「命名输出名[.字段路径]」（与现状兼容，INV-05②）；
#      形态 B（扩展）= 「来源节点node_id::输入位置名[.字段路径]」——
#      跨节点引用上游节点某输入项的绑定值（一次透传不递归，DC-03）；
#   2. 纯函数、零依赖（domain 层最底层），供加载期校验器与引擎装配双侧复用；
#   3. 语法非法以 None 表达（不抛异常），调用方转为校验报告或装配失败；
#   4. 引用仅为数据路径，禁止作为可执行代码求值（INV-09）；
#   5. 既有合法表达式零回归（INV-E01 阻断级）：首段不含 `::` 的表达式，
#      解析结果与原实现逐字段等价（kind="output" / input_position=None）。
# =============================================================================

import re
from dataclasses import dataclass
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# 文法常量（DC-02 / upstream_input_ref DC-01 权威声明，供校验器与测试复用）
# ---------------------------------------------------------------------------

# 命名输出名（形态 A 引用表达式首段）：ASCII 小写字母开头，仅小写字母/数字/下划线，
# 1–64 字符（沿用既有 output_name 约束；点号在命名输出名中非法，故首段与字段路径无歧义）
OUTPUT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# 字段路径段数上限（含 0 段基线形态；spec §9.1.2 段数 1..8）
MAX_PATH_SEGMENTS = 8

# 引用表达式总长上限（净化后）
MAX_EXPRESSION_LENGTH = 256

# 形态 B 作用域限定符（前后端对齐权威常量；design §2.2 文法 `::`）
INPUT_POSITION_SEPARATOR = "::"

# 来源节点标识（形态 B 首段 `::` 之前）：沿用 node_id 既有约束
# （字母/数字/下划线，1–64 字符；design §2.2 `NODE_ID := ^[A-Za-z0-9_]{1,64}$`）
NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,64}$")


@dataclass(frozen=True)
class ParsedReference:
    """解析后的引用表达式。

    形态 A（kind == "output"）：output_name = 命名输出名；
        path_segments = 输出内字段路径段（0 段 = 整对象）；input_position 恒为 None。
    形态 B（kind == "input_position"）：output_name = 来源节点 node_id（作用域段）；
        input_position = 输入位置名；path_segments = 绑定值内字段路径段（0 段 = 整值）。
    """

    output_name: str
    path_segments: Tuple[str, ...]
    kind: str = "output"
    input_position: Optional[str] = None


def _parse_output_reference(trimmed: str) -> Optional[ParsedReference]:
    """形态 A 解析（unified_node_input DC-02 既有实现原样抽出，逐行保留）。

    规则：首段匹配 OUTPUT_NAME_PATTERN；余段（字段路径）非空、不含点号与空白；
    段数 ≤ MAX_PATH_SEGMENTS。返回 kind="output"、input_position=None。
    """
    segments = trimmed.split(".")
    output_name = segments[0]
    if OUTPUT_NAME_PATTERN.match(output_name) is None:
        return None

    path_segments = tuple(segments[1:])
    if len(path_segments) > MAX_PATH_SEGMENTS:
        return None
    for segment in path_segments:
        # 非空、不含空白（点号已被 split 消除，不重复判断）
        if not segment:
            return None
        for char in segment:
            if char.isspace():
                return None

    return ParsedReference(output_name=output_name, path_segments=path_segments)


def _parse_input_position_reference(
    trimmed: str, first_segment: str
) -> Optional[ParsedReference]:
    """形态 B 解析（upstream_input_ref DC-01）：`NODE_ID::POSITION_NAME[.字段路径]`。

    入参 first_segment 为首段（调用方已判定含 `::`）；按**首个** `::` 拆分。
    规则（design §2.2 文法）：
        1. NODE_ID 须匹配 NODE_ID_PATTERN（沿用 node_id 既有约束）；
        2. POSITION_NAME 非空、不含点号与空白、不含 `::`；
        3. 余段（字段路径）复用形态 A 段校验（非空、不含空白、段数 ≤ 上限）；
        任一不满足返回 None。返回 kind="input_position"、output_name=NODE_ID。
    """
    node_id, _, remainder = trimmed.partition(INPUT_POSITION_SEPARATOR)
    if NODE_ID_PATTERN.match(node_id) is None:
        return None

    remainder_segments = remainder.split(".")
    position_name = remainder_segments[0]
    if not position_name:
        return None
    if INPUT_POSITION_SEPARATOR in position_name:
        return None
    for char in position_name:
        if char.isspace():
            return None

    path_segments = tuple(remainder_segments[1:])
    if len(path_segments) > MAX_PATH_SEGMENTS:
        return None
    for segment in path_segments:
        # 非空、不含空白（点号已被 split 消除，不重复判断）
        if not segment:
            return None
        for char in segment:
            if char.isspace():
                return None

    return ParsedReference(
        output_name=node_id,
        path_segments=path_segments,
        kind="input_position",
        input_position=position_name,
    )


def parse_reference(expression: str) -> Optional[ParsedReference]:
    """解析引用表达式；语法非法返回 None（不抛异常）。

    规则（spec §9.1 / DC-02 / upstream_input_ref DC-01）：
        1. 输入先 trim 净化（首尾空白）；
        2. 消歧只看首段：first_segment = trimmed.split(".")[0]；
           首段含 `::` → 形态 B（节点输入位置引用）；
           否则 → 形态 A（命名输出引用，含字段路径段内 `::` 的既有合法形态）；
        3. 表达式总长 ≤ MAX_EXPRESSION_LENGTH；
        任一不满足返回 None。

    解析优先（基线优先，INV-E05 单一解析权威）：不含点号 → 整对象
    （path_segments == ()）；含点号 → 首段为命名输出名（或 node_id）、余段为字段路径。

    消歧边界（INV-E01 阻断）：字段路径段允许含冒号（`result.a::b` 合法走形态 A），
    故判定仅取第一个 `.` 之前的首段，不可全局搜 `::`。
    """
    if not isinstance(expression, str):
        return None

    trimmed = expression.strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_EXPRESSION_LENGTH:
        return None

    first_segment = trimmed.split(".")[0]
    if INPUT_POSITION_SEPARATOR in first_segment:
        return _parse_input_position_reference(trimmed, first_segment)
    return _parse_output_reference(trimmed)
