# =============================================================================
# 输出结构校验纯函数（engine/nodes/output_schema.py）
#
# 职责：大模型节点结构化输出的 schema 校验（llm_and_code_nodes CHG-10 /
# DC-09 / FR-10 / FR-22 / AC-24）。
# 设计意图：
#   1. 纯函数：只依赖入参，无 I/O、无状态、不向外抛异常；
#   2. 类型字符串映射与 agent 节点既有口径一致
#      （agent_node_executor._check_type 同表，另支持 "null"）；
#   3. 返回 None 表示合规，否则返回字段级可读原因
#      （缺字段 → 指明字段名；类型不符 → 指明字段名与期望/实际）；
#   4. schema 为 None/空 → 视为未声明（返回 None，不做任何约束）。
# =============================================================================

from typing import Any, Dict, Optional

# 类型字符串映射（与 agent 节点既有口径一致，INV 口径对照见文件头 2）
_TYPE_MAP: Dict[str, Any] = {
    "string": str,
    "str": str,
    "integer": int,
    "int": int,
    "number": (int, float),
    "float": float,
    "boolean": bool,
    "bool": bool,
    "object": dict,
    "dict": dict,
    "array": list,
    "list": list,
    "null": type(None),
}


def _check_type(value: Any, expected_type: str) -> bool:
    """检查值是否符合期望类型字符串（与 agent 既有映射逐项一致）。"""
    expected = _TYPE_MAP.get(expected_type.lower())
    if expected is None:
        return True
    return isinstance(value, expected)


def validate_output_schema(
    output: Dict[str, Any],
    schema: Optional[Dict[str, str]],
) -> Optional[str]:
    """校验输出是否符合结构约束（字段名 -> 类型字符串，纯函数）。

    返回 None 表示合规；否则返回字段级可读原因：
        - 缺字段 → 「缺少必填字段：{字段名}」；
        - 类型不符 → 「字段 {字段名} 类型不匹配：期望 {期望}，实际 {实际类型名}」。
    schema 为 None/空 → 视为未声明，直接返回 None（AC-24 缺省态）。
    """
    if not schema:
        return None
    for field_name, expected_type in schema.items():
        if field_name not in output:
            return f"缺少必填字段：{field_name}"
        actual_value = output[field_name]
        if not _check_type(actual_value, expected_type):
            return (
                f"字段 {field_name} 类型不匹配："
                f"期望 {expected_type}，实际 {type(actual_value).__name__}"
            )
    return None
