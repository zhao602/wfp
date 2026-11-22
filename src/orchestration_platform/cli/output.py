# =============================================================================
# CLI 输出约定（cli/output.py）
#
# 职责（arch_review_v2 5.1，CHG-17/22，DC-11）：
#   1. exit code 约定单一权威：
#        0 = 成功（diff 有变更也是 0，信息非错误）
#        1 = 校验或执行失败
#        2 = 加载异常
#   2. 人读文本 / --json 机器读的统一输出口径（stdout 载荷、stderr 错误）。
# =============================================================================

import json
import sys
from typing import Any

# exit code 约定（与 tasks.md 5.1 逐字对齐，禁止散落魔法值）
EXIT_OK = 0          # 成功（diff 有变更也是 0，信息非错误）
EXIT_FAIL = 1        # 校验或执行失败
EXIT_LOAD_ERROR = 2  # 加载异常


def emit_text(message: str) -> None:
    """输出人读文本载荷到 stdout。"""
    print(message)


def emit_json(payload: Any) -> None:
    """输出机器可读 JSON 载荷到 stdout（ensure_ascii=False 保留中文可读性）。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def emit_error(message: str) -> None:
    """输出错误/诊断信息到 stderr（不污染 stdout 载荷，便于管道消费）。"""
    print(message, file=sys.stderr)
