# =============================================================================
# StructuredLogger.log_record 用例（tests/unit/test_structured_logger_log_record.py）
#
# 覆盖验收（tasks T5.3 / design DC-01、接口清单 :638、:824 / INV-D-09）：
#   1. log_record 输出一行 JSON（ensure_ascii=False，中文不转义），不做字段裁剪；
#   2. log() 既有签名与行为不变（兼容用例，INV-D-09）；
#   3. 文件头注释解除 B15 清理候选定位（DC-01）。
# =============================================================================

import json
import logging
import uuid
from pathlib import Path
from typing import List, Tuple

import pytest

import orchestration_platform.observability.structured_logger as structured_logger_module
from orchestration_platform.observability.structured_logger import StructuredLogger


class _CaptureHandler(logging.Handler):
    """捕获日志行（不落盘）。"""

    def __init__(self) -> None:
        super().__init__()
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def capture() -> Tuple[_CaptureHandler, str]:
    handler = _CaptureHandler()
    name = f"test.structured_logger.{uuid.uuid4().hex}"
    target = logging.getLogger(name)
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    target.propagate = False
    yield handler, name
    target.removeHandler(handler)


class TestLogRecord:
    """log_record：单行 JSON、不裁剪、中文不转义（design :638、:824）。"""

    def test_log_record_emits_single_json_line(self, capture):
        handler, name = capture
        logger = StructuredLogger(name)
        payload = {"schema_version": "1.0", "record_type": "node_run", "run_id": "r1", "seq": 7}
        logger.log_record(payload)
        assert len(handler.lines) == 1
        assert json.loads(handler.lines[0]) == payload

    def test_log_record_keeps_chinese_unescaped(self, capture):
        handler, name = capture
        logger = StructuredLogger(name)
        logger.log_record({"message": "工作流执行成功", "node": "节点一"})
        line = handler.lines[0]
        assert "工作流执行成功" in line
        assert "\\u" not in line

    def test_log_record_does_not_trim_fields(self, capture):
        handler, name = capture
        logger = StructuredLogger(name)
        payload = {"a": 1, "b": [1, 2, {"c": None}], "d": "x" * 100}
        logger.log_record(payload)
        assert json.loads(handler.lines[0]) == payload

    def test_log_record_handles_unserializable_via_default_str(self, capture):
        handler, name = capture
        logger = StructuredLogger(name)
        logger.log_record({"when": datetime_placeholder()})
        line = handler.lines[0]
        assert "2026-10-24" in json.loads(line)["when"]


def datetime_placeholder() -> str:
    return "2026-10-24T00:00:00+00:00"


class TestLogCompat:
    """log() 兼容：既有签名与行为不变（INV-D-09）。"""

    def test_log_signature_and_behavior_unchanged(self, capture):
        handler, name = capture
        logger = StructuredLogger(name)
        logger.log(
            "info",
            "hello",
            trace_id="t1",
            instance_id="i1",
            node_id="n1",
            round_index=2,
            duration_ms=33,
            custom_flag=True,
        )
        assert len(handler.lines) == 1
        record = json.loads(handler.lines[0])
        assert record["message"] == "hello"
        assert record["trace_id"] == "t1"
        assert record["instance_id"] == "i1"
        assert record["node_id"] == "n1"
        assert record["round_index"] == 2
        assert record["duration_ms"] == 33
        assert record["custom_flag"] is True
        assert "timestamp" in record
        assert record["level"] == "info"

    def test_header_comment_unblocks_b15(self):
        source = Path(structured_logger_module.__file__).read_text(encoding="utf-8")
        header_text = "\n".join(source.split("\n")[:10])
        assert "B档候选孤儿" not in header_text
        assert "生产调用点" in header_text
