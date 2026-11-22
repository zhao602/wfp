# =============================================================================
# 运行日志脱敏与截断用例（tests/unit/test_run_log_redaction_truncation.py）
#
# 覆盖验收（tasks T3.3 / INV-02、INV-03）：
#   1. 递归脱敏（嵌套 dict/list）；值模式命中 4 类形态；堆栈文本脱敏；
#   2. redaction_enabled=False 时字段黑名单兜底（红线不可关）；
#   3. 截断标记与字节界；二进制/循环引用摘要；
#   4. 不修改入参；
#   5. 硬门禁：通读脱敏输出无明文凭据。
# =============================================================================

import copy

import pytest

from orchestration_platform.configuration.settings import Settings
from orchestration_platform.observability.run_log.redactor import RunLogRedactor
from orchestration_platform.observability.run_log.truncation import Truncator

BEARER_SECRET = "eyJhbGciOiJIUzI1NiJ9.realpayloadpart.signature"
DB_PASSWORD = "sup3rs3cret"
API_KEY = "sk-ant-api03-real-secret-value-123456"
PEM_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7x9mPlausibleKeyBody\n"
    "-----END RSA PRIVATE KEY-----"
)


@pytest.fixture(autouse=True)
def _clean_log_env(monkeypatch):
    for key in (
        "LOG_REDACTION_ENABLED", "LOG_MAX_PAYLOAD_BYTES", "LOG_DIR",
        "LOG_ROTATION_ENABLED", "LOG_ASYNC_ENABLED", "LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)


def _settings(**overrides) -> Settings:
    values: dict = {"log_redaction_enabled": True}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item)


class TestRecursiveFieldBlacklist:
    """字段名黑名单递归脱敏，不修改入参。"""

    def test_nested_dict_and_list_redacted(self):
        redactor = RunLogRedactor(_settings())
        payload = {
            "input": {
                "password": "hunter2",
                "safe": "keep-me",
                "items": [{"api_key": "k-1"}, {"access_key": "k-2"}],
            }
        }
        result = redactor.redact(payload)
        assert result["input"]["password"] == "[REDACTED]"
        assert result["input"]["safe"] == "keep-me"
        assert result["input"]["items"][0]["api_key"] == "[REDACTED]"
        assert result["input"]["items"][1]["access_key"] == "[REDACTED]"

    def test_does_not_mutate_input(self):
        redactor = RunLogRedactor(_settings())
        payload = {"password": "hunter2", "nested": {"token": "t-1"}}
        snapshot = copy.deepcopy(payload)
        redactor.redact(payload)
        assert payload == snapshot


class TestValuePatterns:
    """值模式命中 4 类形态：Bearer / 连接串口令 / 私钥块 / 长随机串。"""

    def test_bearer_token_form(self):
        redactor = RunLogRedactor(_settings())
        result = redactor.redact_text(f"Authorization: Bearer {BEARER_SECRET} 过期")
        assert BEARER_SECRET not in result
        assert "Bearer [REDACTED]" in result

    def test_connection_string_password_form(self):
        redactor = RunLogRedactor(_settings())
        result = redactor.redact_text(
            "postgres://svc-admin:" + DB_PASSWORD + "@db.internal:5432/app"
        )
        assert DB_PASSWORD not in result
        assert "postgres://svc-admin:[REDACTED]@db.internal:5432/app" == result

    def test_private_key_block_form(self):
        redactor = RunLogRedactor(_settings())
        result = redactor.redact_text("加载失败：\n" + PEM_KEY)
        assert "MIIEpAIBAAKCAQEA" not in result
        assert "[REDACTED]" in result

    def test_long_random_string_form(self):
        redactor = RunLogRedactor(_settings())
        long_random = "f3d2a9c81b7e4506af23cd98de107b5c6a4e8f21bc73d95e"
        result = redactor.redact_text("疑似密钥 " + long_random + " 出现")
        assert long_random not in result
        assert "[REDACTED]" in result

    def test_key_value_password_in_text(self):
        redactor = RunLogRedactor(_settings())
        result = redactor.redact_text("连接失败：password=hunter2 重试")
        assert "hunter2" not in result
        assert "password=[REDACTED]" in result

    def test_normal_text_untouched(self):
        redactor = RunLogRedactor(_settings())
        text = "节点执行成功，耗时 123ms，轮次 2"
        assert redactor.redact_text(text) == text


class TestStackRedaction:
    """堆栈文本同规则脱敏。"""

    def test_redact_text_on_traceback(self):
        redactor = RunLogRedactor(_settings())
        stack = (
            "Traceback (most recent call last):\n"
            '  File "executor.py", line 88, in run\n'
            "RuntimeError: auth failed for password=hunter2\n"
            f"  request header: Bearer {BEARER_SECRET}\n"
        )
        result = redactor.redact_text(stack)
        assert "hunter2" not in result
        assert BEARER_SECRET not in result
        assert "Traceback (most recent call last):" in result
        assert "RuntimeError: auth failed for password=[REDACTED]" in result


class TestDisabledFallback:
    """redaction_enabled=False：值模式关闭，字段名黑名单兜底仍生效（红线不可关）。"""

    def test_blacklist_still_applies_when_disabled(self):
        redactor = RunLogRedactor(_settings(log_redaction_enabled=False))
        assert redactor.redaction_enabled is False
        payload = {"password": "hunter2", "note": "postgres://u:p@h/db"}
        result = redactor.redact(payload)
        assert result["password"] == "[REDACTED]"
        assert result["note"] == "postgres://u:p@h/db"

    def test_redact_text_disabled_returns_unchanged(self):
        redactor = RunLogRedactor(_settings(log_redaction_enabled=False))
        text = "token=super-secret-value"
        assert redactor.redact_text(text) == text


class TestRedactionFailure:
    """规则执行异常：整体占位 + payload_note + 不外抛。"""

    def test_failure_placeholder_and_payload_note(self, monkeypatch):
        redactor = RunLogRedactor(_settings())

        def _boom(text: str) -> str:
            raise RuntimeError("pattern engine failure")

        monkeypatch.setattr(redactor, "_apply_text_patterns", _boom)
        result = redactor.redact({"x": "y"})
        assert result == "[REDACTION_FAILED]"
        assert redactor.payload_note is not None

    def test_failure_placeholder_for_text(self, monkeypatch):
        redactor = RunLogRedactor(_settings())

        def _boom(text: str) -> str:
            raise RuntimeError("pattern engine failure")

        monkeypatch.setattr(redactor, "_apply_text_patterns", _boom)
        assert redactor.redact_text("hello") == "[REDACTION_FAILED]"
        assert redactor.payload_note is not None


class TestTruncator:
    """截断标记与二进制摘要。"""

    def test_within_limit_returns_original(self):
        truncator = Truncator()
        value = {"a": "b"}
        result, truncated = truncator.truncate_field(value, 65536)
        assert result == value
        assert truncated is False

    def test_over_limit_truncates_with_marker(self):
        truncator = Truncator()
        text = "你" * 200  # 3 字节/字符，序列化后 600+ 字节
        result, truncated = truncator.truncate_field(text, 100)
        assert truncated is True
        assert result.endswith("...[TRUNCATED]")
        assert len(result.encode("utf-8")) <= 100
        assert result != text

    def test_bytes_summarized(self):
        truncator = Truncator()
        summary, truncated = truncator.truncate_field(b"\x00\x01\x02", 65536)
        assert truncated is True
        assert summary.startswith("<binary:bytes")
        assert "len=3" in summary
        assert "sha256=" in summary

    def test_circular_reference_summarized(self):
        truncator = Truncator()
        loop = []
        loop.append(loop)
        summary, truncated = truncator.truncate_field(loop, 65536)
        assert truncated is True
        assert "<unserializable:list" in summary

    def test_summarize_binary_object(self):
        truncator = Truncator()
        summary = truncator.summarize_binary(object())
        assert "<unserializable:object" in summary
        assert "sha256=" in summary

    def test_truncate_does_not_mutate_input(self):
        truncator = Truncator()
        payload = {"deep": {"text": "x" * 500}}
        snapshot = copy.deepcopy(payload)
        truncator.truncate_field(payload, 64)
        assert payload == snapshot


class TestNoPlaintextCredentialsGate:
    """硬门禁：通读脱敏输出（含键名）无明文凭据。"""

    def test_redacted_output_contains_no_plaintext_secrets(self):
        redactor = RunLogRedactor(_settings())
        record = {
            "input": {
                "api_key": API_KEY,
                "db_url": "postgres://svc:" + DB_PASSWORD + "@db.internal:5432/app",
                "note": f"use Bearer {BEARER_SECRET} for calls",
                "pem": PEM_KEY,
            },
            "error": {"error_message": "auth failed for password=hunter2"},
        }
        snapshot = copy.deepcopy(record)
        redacted = redactor.redact(record)
        assert record == snapshot  # 不修改入参
        blob = "\n".join(_walk_strings(redacted))
        for secret in (API_KEY, DB_PASSWORD, "hunter2", BEARER_SECRET, "MIIEpAIBAAKCAQEA"):
            assert secret not in blob, f"明文凭据泄漏：{secret}"
