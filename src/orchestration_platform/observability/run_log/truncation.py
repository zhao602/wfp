# =============================================================================
# 运行日志截断与摘要（observability/run_log/truncation.py，workflow_run_logging CHG-05）
#
# 职责：字段级截断与二进制摘要（Truncator：truncate_field / summarize_binary）。
# 设计意图（design 截断策略、INV-03、INV-D-03）：
#   1. 先序列化计长，超 log_max_payload_bytes 保留前缀（utf-8 字节界安全切）
#      并返回 (值, truncated: bool)；
#   2. bytes / 不可序列化 / 循环引用产出摘要字符串（类型名 + 长度 + sha256 前缀），
#      不抛异常；
#   3. 截断在脱敏之后执行（调用方保证顺序，见 recorder）。
# =============================================================================

import hashlib
import json
from typing import Any, Tuple


class Truncator:
    """运行日志字段截断器。"""

    _TRUNCATION_MARKER = "...[TRUNCATED]"

    def truncate_field(self, value: Any, max_bytes: int) -> Tuple[Any, bool]:
        """按序列化字节数截断字段值，返回 (值, 是否截断)。"""
        if isinstance(value, (bytes, bytearray)):
            return self.summarize_binary(value), True
        try:
            serialized = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            # 循环引用 / 真不可序列化：最小暴露为摘要
            return self.summarize_binary(value), True
        raw = serialized.encode("utf-8")
        if len(raw) <= max_bytes:
            return value, False
        marker = self._TRUNCATION_MARKER
        cut = max(0, max_bytes - len(marker.encode("utf-8")))
        prefix = raw[:cut].decode("utf-8", errors="ignore")
        return prefix + marker, True

    def summarize_binary(self, value: Any) -> str:
        """生成二进制/不可序列化值的摘要：类型名 + 长度 + sha256 前缀。"""
        type_name = type(value).__name__
        if isinstance(value, (bytes, bytearray)):
            digest = hashlib.sha256(bytes(value)).hexdigest()[:16]
            return f"<binary:{type_name} len={len(value)} sha256={digest}>"
        try:
            text = str(value)
            digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
            return f"<unserializable:{type_name} len={len(text)} sha256={digest}>"
        except Exception:
            return f"<unserializable:{type_name} sha256=unavailable>"
