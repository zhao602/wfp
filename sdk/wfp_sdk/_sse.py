# =============================================================================
# wfp-sdk SSE 帧解析（wfp_sdk/_sse.py）
#
# 职责：解析平台 text/event-stream 帧（docs/sdk-contract.md §5）：
#       "data: <json>\n\n" 事件帧 + 终态收尾帧
#       data: {"event_type": "STREAM_END", "instance_id": ...}。
# 纪律：帧格式冻结（§5）；STREAM_END 收尾帧不向调用方产出（迭代自然结束）。
# =============================================================================

import json
from typing import Any, Optional

# STREAM_END 收尾帧哨兵（与事件 dict 判别用；非 None 以区分「非 data 行」）
STREAM_END = "STREAM_END"

_DATA_PREFIX = "data:"


def parse_sse_frame(line: str) -> Optional[Any]:
    """解析单行 SSE 帧。

    返回：
        dict          —— 事件帧的 JSON 对象；
        STREAM_END    —— 终态收尾帧哨兵（调用方应结束迭代）；
        None          —— 非 data 行 / 空 JSON / 解析失败（跳过）。
    """
    stripped = line.strip()
    if not stripped.startswith(_DATA_PREFIX):
        return None
    payload = stripped[len(_DATA_PREFIX):].strip()
    if not payload:
        return None
    try:
        event = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(event, dict) and event.get("event_type") == STREAM_END:
        return STREAM_END
    if isinstance(event, dict):
        return event
    return None
