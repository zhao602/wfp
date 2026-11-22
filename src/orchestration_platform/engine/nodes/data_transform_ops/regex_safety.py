# =============================================================================
# 正则安全三层模块（engine/nodes/data_transform_ops/regex_safety.py）
#
# 职责：为 regex_extract / regex_replace（及自定义脱敏模式）提供执行前安全
#       防护（expand_data_transform_ops design DC-07 / CHG-B08 / T4.1，AC-11）。
# 三层防护：
#   1. 静态拒绝：已知高危形态（嵌套量词如 (a+)+、可疑超大重复计数如 {100000}）
#      + 模式长度上限（超限拒绝）；
#   2. 输入长度上限：被处理字段值超长 → DATA_TRANSFORM_REGEX_INPUT_TOO_LONG
#      （由调用方按错误码 fail-fast）；
#   3. 复杂度限制：执行前以启发式估算风险（量词总数上限）。
# 残余风险显式声明（INV-07/INV-08）：本模块**不承诺完全免受 ReDoS**
# （Python 标准库 re 无原生超时；win32 无 SIGALRM，无法做信号超时兜底）；
# 验收仅覆盖「高危被拒 + 超长被拦」。
# =============================================================================

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from orchestration_platform.engine.nodes.data_transform_ops.registry import (
    MAX_REGEX_INPUT_LENGTH,
    MAX_REGEX_PATTERN_LENGTH,
)

# 可疑超大重复计数阈值（如 {100000}；超过即静态拒绝）
_MAX_REPEAT_COUNT = 1000

# 复杂度启发式：模式中量词算子总数上限（第三层）
_MAX_QUANTIFIER_OPS = 50

# 重复计数形态：{n} / {n,} / {n,m}
_REPEAT_COUNT_RE = re.compile(r"\{(\d+)(?:,(\d*))?\}")

# 嵌套量词形态（组内容含量词且组本身被量词修饰）：
# 逐字符扫描配对括号（处理转义与字符类），见 _iter_quantified_groups。
_QUANTIFIER_CHARS = "+*"


@dataclass(frozen=True)
class RegexSafetyVerdict:
    """正则模式安全判定结果（safe=False 时 reason 给出拒绝原因）。"""

    safe: bool
    reason: Optional[str] = None


def check_regex_safety(pattern: str) -> RegexSafetyVerdict:
    """正则三层安全第 1/3 层：模式静态检查（不承诺完全免 ReDoS）。

    拒绝项（safe=False）：
      - 模式长度超上限（> MAX_REGEX_PATTERN_LENGTH）；
      - 编译失败（非法正则，配置期即拒）；
      - 嵌套量词（如 (a+)+、(a*)*、(?:a+)+、(a{2,})+ 等）；
      - 可疑超大重复计数（如 {100000}，阈值 _MAX_REPEAT_COUNT）；
      - 复杂度启发式超限（量词算子总数 > _MAX_QUANTIFIER_OPS）。
    """
    if not pattern:
        return RegexSafetyVerdict(False, "正则模式为空")

    if len(pattern) > MAX_REGEX_PATTERN_LENGTH:
        return RegexSafetyVerdict(
            False,
            f"正则模式长度 {len(pattern)} 超上限 {MAX_REGEX_PATTERN_LENGTH}",
        )

    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return RegexSafetyVerdict(False, f"正则编译失败：{exc}")

    # 嵌套量词静态拒绝
    for group_body, quantifier in _iter_quantified_groups(pattern):
        if _has_inner_quantifier(group_body):
            return RegexSafetyVerdict(
                False,
                f"检测到嵌套量词高危形态（组 ({group_body}){quantifier}）",
            )

    # 可疑超大重复计数
    for match in _REPEAT_COUNT_RE.finditer(pattern):
        low = int(match.group(1))
        high_text = match.group(2)
        high = int(high_text) if high_text else None
        if low > _MAX_REPEAT_COUNT or (high is not None and high > _MAX_REPEAT_COUNT):
            return RegexSafetyVerdict(
                False,
                f"可疑超大重复计数 {match.group(0)}（阈值 {_MAX_REPEAT_COUNT}）",
            )

    # 复杂度启发式（第三层）
    quantifier_ops = _count_quantifier_ops(pattern)
    if quantifier_ops > _MAX_QUANTIFIER_OPS:
        return RegexSafetyVerdict(
            False,
            f"模式复杂度过高（量词算子 {quantifier_ops} 个，上限 {_MAX_QUANTIFIER_OPS}，启发式）",
        )

    # 编译结果仅用于语法校验，不缓存（无状态纯函数）
    del compiled
    return RegexSafetyVerdict(True)


def check_regex_input_length(text: str) -> bool:
    """第 2 层：被处理字段值是否超长（True = 超长，调用方 fail-fast）。"""
    return len(text) > MAX_REGEX_INPUT_LENGTH


def _iter_quantified_groups(pattern: str) -> List[Tuple[str, str]]:
    """枚举「被量词修饰的分组」的（组内容, 量词）。

    逐字符扫描，正确处理反斜杠转义与 [...] 字符类；
    对配对括号回溯定位组内容（含嵌套组的整体内容）。
    """
    results: List[Tuple[str, str]] = []
    stack: List[int] = []  # 各层 '(' 的起始下标
    index = 0
    length = len(pattern)

    while index < length:
        char = pattern[index]
        if char == "\\":
            index += 2  # 转义字符跳过（含 \(
            continue
        if char == "[":
            # 字符类：扫到未转义的 ]（首个 ] 为字面量仅在首位，简化处理）
            index += 1
            if index < length and pattern[index] == "^":
                index += 1
            if index < length and pattern[index] == "]":
                index += 1
            while index < length and pattern[index] != "]":
                if pattern[index] == "\\":
                    index += 1
                index += 1
            index += 1
            continue
        if char == "(":
            stack.append(index)
        elif char == ")" and stack:
            start = stack.pop()
            # 组结束：检查后随量词（+ * 或 {n,m} 形态）
            rest = pattern[index + 1 :]
            quantifier = ""
            if rest[:1] in ("+", "*"):
                quantifier = rest[0]
                if rest[1:2] == "?":  # 懒惰修饰
                    quantifier += "?"
            elif rest[:1] == "{":
                brace = rest.find("}")
                if brace != -1 and _REPEAT_COUNT_RE.match(rest):
                    quantifier = rest[: brace + 1]
            if quantifier:
                results.append((pattern[start + 1 : index], quantifier))
        index += 1

    return results


def _has_inner_quantifier(group_body: str) -> bool:
    """判断组内容是否含有效量词算子（排除组标记 ? 与字符类/转义）。

    组标记（?: ?= ?! ?< ?P 等）不构成量词；内容中未转义的
    + / * / ? / {n,m} 视为内层量词（嵌套量词判定依据）；
    量词后的 ? 为懒惰修饰、组开括号后的 ? 为组标记，均不构成新量词。
    """
    body = group_body
    # 剥离组标记（?: / ?= / ?! / ?< / ?P<name>）
    if body.startswith("?"):
        if body[:2] in ("?:", "?=", "?!"):
            body = body[2:]
        elif body[:2] in ("?<", "?P"):
            close = body.find(">")
            if close != -1:
                body = body[close + 1 :]
            else:
                body = body[2:]
        else:
            body = body[1:]

    index = 0
    length = len(body)
    prev_kind = ""  # 前一有效单元：""（起始/组开括号后）/ "atom" / "quantifier"
    while index < length:
        char = body[index]
        if char == "\\":
            index += 2
            prev_kind = "atom"
            continue
        if char == "[":
            index += 1
            if index < length and body[index] == "^":
                index += 1
            if index < length and body[index] == "]":
                index += 1
            while index < length and body[index] != "]":
                if body[index] == "\\":
                    index += 1
                index += 1
            index += 1
            prev_kind = "atom"
            continue
        if char in _QUANTIFIER_CHARS:
            return True
        if char == "{":
            # {n,m} 形态（合法重复计数视为量词；非法形态为字面量，由编译失败兜底）
            if _REPEAT_COUNT_RE.match(body[index:]):
                return True
            prev_kind = "atom"
            index += 1
            continue
        if char == "?":
            # 组开括号后的 ? 为组标记；量词后的 ? 为懒惰修饰 —— 均非新量词
            if prev_kind == "atom":
                return True
            prev_kind = "quantifier"
            index += 1
            continue
        if char == "(":
            prev_kind = ""
            index += 1
            continue
        prev_kind = "atom"
        index += 1
    return False


def _count_quantifier_ops(pattern: str) -> int:
    """复杂度启发式：统计未转义、字符类外的量词算子数（+ * ? {n,m}）。

    组开括号后的 ? 为组标记、量词后的 ? 为懒惰修饰，均不计入。
    """
    count = 0
    index = 0
    length = len(pattern)
    prev_kind = ""  # 前一有效单元：""（起始/组开括号后）/ "atom" / "quantifier"

    while index < length:
        char = pattern[index]
        if char == "\\":
            index += 2
            prev_kind = "atom"
            continue
        if char == "[":
            index += 1
            if index < length and pattern[index] == "^":
                index += 1
            if index < length and pattern[index] == "]":
                index += 1
            while index < length and pattern[index] != "]":
                if pattern[index] == "\\":
                    index += 1
                index += 1
            index += 1
            prev_kind = "atom"
            continue
        if char == "{":
            match = _REPEAT_COUNT_RE.match(pattern[index:])
            if match:
                count += 1
                index += match.end()
                prev_kind = "quantifier"
                continue
            prev_kind = "atom"
            index += 1
            continue
        if char in "+*":
            count += 1
            prev_kind = "quantifier"
            index += 1
            continue
        if char == "?":
            if prev_kind == "atom":
                count += 1
                prev_kind = "quantifier"
            else:
                # 组标记（prev=""）或懒惰修饰（prev="quantifier"）
                prev_kind = "quantifier"
            index += 1
            continue
        if char == "(":
            prev_kind = ""
            index += 1
            continue
        prev_kind = "atom"
        index += 1

    return count
