# =============================================================================
# JSON Path 子集提取器（engine/nodes/data_transform_ops/json_path.py）
#
# 职责：以受限 JSON Path 子集从嵌套 JSON 结构精准提取字段值
#       （expand_data_transform_ops design DC-05 / CHG-B07 / T5.1，AC-14）。
# 语法子集（**非完整 JSONPath 规范，显式声明**）：
#   $        根（identity）
#   .键名    对象键（键名不含 `.` 与 `[`；不支持 $['key'] 引号形态）
#   [下标]   数组下标（仅非负整数；不支持负下标）
#   [*]      取全部元素（fan-out；路径含 [*] 时结果为列表）
# 设计口径（DC-05）：
#   - 独立自实现（手写 tokenizer + 递归求值），**不复用、不修改**
#     RestrictedExpressionEvaluator（INV-14 隔离；其语法族为布尔/比较白名单，
#     与路径语法不同族）；
#   - 仅数据定位，逐段匹配，**无通配穿越、无代码求值语义**（INV-07）；
#   - 缺失路径 → found=False + 计数（missing_hits），不抛出、不中断链；
#   - JSON 文本长度上限（max_text_len，超限按缺失计，不解析）。
# =============================================================================

import json
from typing import Any, List, NamedTuple, Union

from orchestration_platform.engine.nodes.data_transform_ops.registry import (
    MAX_JSON_TEXT_LENGTH,
)


class JsonPathSyntaxError(ValueError):
    """JSON Path 语法非法（子集外形态；调用方按配置期错误映射拒绝）。"""


class JsonPathResult(NamedTuple):
    """单次提取结果。

    found        —— 是否命中（至少一个值被匹配）
    value        —— 提取值：路径含 [*] 时为列表（可能为空列表），
                    否则为单值；found=False 时为占位（单值路径 None /
                    通配路径 []），调用方以 found 为准
    missing_hits —— 缺失计数（路径未命中的分支数；[*] fan-out 部分缺失计入）
    """

    found: bool
    value: Any
    missing_hits: int


class _KeySegment(NamedTuple):
    """对象键段（.键名）。"""

    key: str


class _IndexSegment(NamedTuple):
    """数组下标段（[下标]，非负整数）。"""

    index: int


class _WildcardSegment(NamedTuple):
    """通配段（[*]，取全部元素）。"""


_PathSegment = Union[_KeySegment, _IndexSegment, _WildcardSegment]


def parse_json_path(path: str) -> List[_PathSegment]:
    """tokenizer：`$` / `.键名` / `[下标]` / `[*]` 子集 → 路径段序列。

    语法非法抛 JsonPathSyntaxError（配置期拒绝，调用方映射
    DATA_TRANSFORM_INVALID_STEP_PARAMS）。
    """
    if not isinstance(path, str) or not path:
        raise JsonPathSyntaxError("JSON Path 表达式为空")
    if not path.startswith("$"):
        raise JsonPathSyntaxError(f"JSON Path 须以 $ 起始：{path!r}")

    segments: List[_PathSegment] = []
    index = 1
    length = len(path)
    while index < length:
        char = path[index]
        if char == ".":
            # .键名：取到下一个 . / [ / ] 为止（键名不含 . [ ]，子集限制）
            end = index + 1
            while end < length and path[end] not in ".[]":
                end += 1
            key = path[index + 1 : end]
            if not key:
                raise JsonPathSyntaxError(f"JSON Path 空键名：{path!r}")
            segments.append(_KeySegment(key))
            index = end
        elif char == "[":
            close = path.find("]", index)
            if close == -1:
                raise JsonPathSyntaxError(f"JSON Path 括号未闭合：{path!r}")
            inner = path[index + 1 : close]
            if inner == "*":
                segments.append(_WildcardSegment())
            elif inner.isascii() and inner.isdigit():
                segments.append(_IndexSegment(int(inner)))
            else:
                raise JsonPathSyntaxError(
                    f"JSON Path 下标须为非负整数或 *：[{inner}]"
                    f"（不支持负下标/引号键，子集限制）"
                )
            index = close + 1
        else:
            raise JsonPathSyntaxError(
                f"JSON Path 非法字符 {char!r} 于位置 {index}（须为 . 或 [）：{path!r}"
            )
    return segments


def evaluate_json_path(
    segments: List[_PathSegment],
    value: Any,
    *,
    max_text_len: int = MAX_JSON_TEXT_LENGTH,
) -> JsonPathResult:
    """递归求值：对已 tokenize 的路径段序列在 value 上求值。

    - value 为 str：先按 JSON 文本解析（超 max_text_len 或解析失败 → 缺失计）；
    - value 为 dict/list：视为已解析结构直接求值；其他标量：仅 $ 可命中。
    - 路径含 [*] → 结果为列表；否则为单值（至多一个匹配）。
    """
    has_wildcard = any(isinstance(segment, _WildcardSegment) for segment in segments)

    root = value
    if isinstance(value, str):
        if len(value) > max_text_len:
            return JsonPathResult(False, [] if has_wildcard else None, 1)
        try:
            root = json.loads(value)
        except (ValueError, TypeError):
            return JsonPathResult(False, [] if has_wildcard else None, 1)

    current: List[Any] = [root]
    missing_hits = 0
    for segment in segments:
        next_values: List[Any] = []
        if isinstance(segment, _KeySegment):
            for item in current:
                if isinstance(item, dict) and segment.key in item:
                    next_values.append(item[segment.key])
                else:
                    missing_hits += 1
        elif isinstance(segment, _IndexSegment):
            for item in current:
                if isinstance(item, list) and 0 <= segment.index < len(item):
                    next_values.append(item[segment.index])
                else:
                    missing_hits += 1
        else:  # _WildcardSegment
            for item in current:
                if isinstance(item, list):
                    next_values.extend(item)
                else:
                    missing_hits += 1
        current = next_values

    if not current:
        # 零匹配：found=False（通配路径空列表亦按缺失计，AC-14 缺失策略）
        return JsonPathResult(False, [] if has_wildcard else None, missing_hits)
    if has_wildcard:
        return JsonPathResult(True, current, missing_hits)
    return JsonPathResult(True, current[0], missing_hits)


def extract_json_path(
    path: str,
    value: Any,
    *,
    max_text_len: int = MAX_JSON_TEXT_LENGTH,
) -> JsonPathResult:
    """便捷入口：tokenize + 求值（tasks.md T5.1 契约签名）。

    语法非法抛 JsonPathSyntaxError；缺失 → found=False + 计数。
    """
    return evaluate_json_path(parse_json_path(path), value, max_text_len=max_text_len)
