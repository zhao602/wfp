# =============================================================================
# 运行日志脱敏器（observability/run_log/redactor.py，workflow_run_logging CHG-04）
#
# 职责：运行日志载荷脱敏（RunLogRedactor：redact / redact_text）。
# 设计意图（design 脱敏策略、INV-02/03、INV-D-02/03）：
#   1. 字段名黑名单兜底复用 application/log_sanitizer.py 的 7 条正则
#      （红线，log_redaction_enabled=False 时仍生效，不可关）；
#   2. 叠加值模式匹配（Bearer 令牌、连接串口令、key=value 形态、
#      PEM 私钥块、长随机串疑似密钥），由开关控制；
#   3. 规则执行异常按最小暴露原则整体占位 [REDACTION_FAILED]，
#      并记录 payload_note + WARN；
#   4. 不修改入参（返回副本）。
# =============================================================================

import logging
import re
from typing import Any, Optional

from orchestration_platform.application.log_sanitizer import LogSanitizer
from orchestration_platform.configuration.settings import Settings

logger = logging.getLogger(__name__)

_REDACTED_PLACEHOLDER = "[REDACTED]"
_REDACTION_FAILED_PLACEHOLDER = "[REDACTION_FAILED]"

# 值形态脱敏规则（叠加在字段名黑名单之上，替换顺序敏感）
_PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIAL_PATTERN = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/@\s]+):([^@/\s]+)@")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|auth)"
    r"\s*([=:])\s*[^\s&,;'\"()]+"
)
_LONG_RANDOM_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{40,}(?![A-Za-z0-9_-])")


class RunLogRedactor:
    """运行日志脱敏器：字段名黑名单（红线）+ 值模式匹配（开关）。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        if settings is None:
            from orchestration_platform.configuration.settings import get_settings

            settings = get_settings()
        self._enabled = bool(settings.log_redaction_enabled)
        self.payload_note: Optional[str] = None

    @property
    def redaction_enabled(self) -> bool:
        return self._enabled

    def redact(self, value: Any) -> Any:
        """递归脱敏：先字段名黑名单（不可关），再值模式（开关控制）；不修改入参。"""
        self.payload_note = None
        try:
            if isinstance(value, dict):
                sanitized = LogSanitizer.sanitize(value)
                if not self._enabled:
                    return sanitized
                return self._apply_value_patterns(sanitized)
            if isinstance(value, list):
                return [self.redact(item) for item in value]
            if isinstance(value, str):
                if not self._enabled:
                    return value
                return self._apply_text_patterns(value)
            return value
        except Exception:
            self.payload_note = "脱敏执行异常，载荷已整体占位"
            logger.warning("运行日志脱敏执行异常，载荷已整体占位", exc_info=True)
            return _REDACTION_FAILED_PLACEHOLDER

    def redact_text(self, text: str) -> str:
        """对纯文本（error_message/error_stack）做同规则值模式替换。"""
        if not text or not self._enabled:
            return text
        try:
            return self._apply_text_patterns(text)
        except Exception:
            self.payload_note = "脱敏执行异常，文本已整体占位"
            logger.warning("运行日志文本脱敏异常，已整体占位", exc_info=True)
            return _REDACTION_FAILED_PLACEHOLDER

    def _apply_value_patterns(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._apply_text_patterns(value)
        if isinstance(value, dict):
            return {key: self._apply_value_patterns(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._apply_value_patterns(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._apply_value_patterns(item) for item in value)
        return value

    def _apply_text_patterns(self, text: str) -> str:
        result = _PRIVATE_KEY_BLOCK_PATTERN.sub(_REDACTED_PLACEHOLDER, text)
        result = _BEARER_PATTERN.sub("Bearer " + _REDACTED_PLACEHOLDER, result)
        result = _URL_CREDENTIAL_PATTERN.sub(r"\1:" + _REDACTED_PLACEHOLDER + "@", result)
        result = _KEY_VALUE_PATTERN.sub(r"\1\2" + _REDACTED_PLACEHOLDER, result)
        result = _LONG_RANDOM_PATTERN.sub(_REDACTED_PLACEHOLDER, result)
        return result
