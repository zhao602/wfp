# =============================================================================
# 日志脱敏器（application/log_sanitizer.py）
#
# 职责：对事件载荷进行脱敏处理，防止密钥、令牌等敏感信息落盘。
# 设计意图（对应 spec 4.3.3、design 2.5.4）：
#   1. 事件载荷经脱敏后才写入仓储；
#   2. 按字段名模式匹配敏感字段，替换为占位符；
#   3. 递归处理嵌套字典与列表。
# =============================================================================

import re
from typing import Any, Dict

# 敏感字段名模式（不区分大小写）
_SENSITIVE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r".*password.*",
        r".*secret.*",
        r".*token.*",
        r".*api_?key.*",
        r".*credential.*",
        r".*private_?key.*",
        r".*access_?key.*",
    )
]

_REDACTED_PLACEHOLDER = "[REDACTED]"


class LogSanitizer:
    """事件载荷脱敏器。"""

    @staticmethod
    def sanitize(payload: Dict[str, Any]) -> Dict[str, Any]:
        """对事件载荷进行脱敏，返回脱敏后的副本。

        递归处理嵌套字典与列表，匹配敏感字段名的值替换为 [REDACTED]。
        """
        return LogSanitizer._sanitize_value(payload)

    @staticmethod
    def _sanitize_value(value: Any) -> Any:
        """递归脱敏单个值。"""
        if isinstance(value, dict):
            return {
                key: LogSanitizer._sanitize_field(key, val)
                for key, val in value.items()
            }
        if isinstance(value, list):
            return [LogSanitizer._sanitize_value(item) for item in value]
        return value

    @staticmethod
    def _sanitize_field(field_name: str, value: Any) -> Any:
        """检查字段名是否敏感，敏感则替换为占位符。"""
        for pattern in _SENSITIVE_PATTERNS:
            if pattern.match(field_name):
                return _REDACTED_PLACEHOLDER
        return LogSanitizer._sanitize_value(value)
