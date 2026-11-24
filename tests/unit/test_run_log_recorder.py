# =============================================================================
# 运行日志采集门面用例（tests/unit/test_run_log_recorder.py）
#
# 覆盖验收（tasks T5.3 / design 接口组 R、DC-09、时序 :510/:536/:538、E-9 / INV-01、INV-02、INV-03）：
#   1. 红线（必过门禁）：门面全方法在 sink/redactor/truncator 抛异常时绝不抛出；
#   2. 字段规范符合性（design 2.0.1）：必填齐备、运行中不伪造结束字段（E-9）、可 json 序列化；
#   3. 级别映射（DC-09）：开始/成功 INFO、失败/超时 ERROR、未知终态 WARN；
#   4. 兜底：tenant→log_tenant_default、category→default；run_id/trace_id 一致性；枚举值域；
#   5. 脱敏/截断联动（truncated/payload_note）与中文保留。
# =============================================================================

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest

from orchestration_platform.configuration.settings import Settings
from orchestration_platform.observability.run_log.fields import (
    BUSINESS_CATEGORIES,
    LEVELS,
    NODE_RUN_STATUSES,
    STATUS_RUNNING,
    WORKFLOW_RUN_STATUSES,
)
from orchestration_platform.observability.run_log.recorder import WorkflowRunLogger
from orchestration_platform.observability.run_log.redactor import RunLogRedactor
from orchestration_platform.observability.run_log.truncation import Truncator

_LOG_ENV_KEYS = (
    "LOG_LEVEL",
    "LOG_DIR",
    "LOG_ROTATION_ENABLED",
    "LOG_ROTATION_WHEN",
    "LOG_ROTATION_MAX_BYTES",
    "LOG_RETENTION_DAYS",
    "LOG_MAX_PAYLOAD_BYTES",
    "LOG_REDACTION_ENABLED",
    "LOG_ASYNC_ENABLED",
    "LOG_QUEUE_MAX_SIZE",
    "LOG_TENANT_DEFAULT",
)


@pytest.fixture(autouse=True)
def _clean_log_env(monkeypatch):
    """隔离环境变量，避免本机 .env/环境注入干扰（R-C）。"""
    for key in _LOG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def _settings(**overrides: Any) -> Settings:
    values: Dict[str, Any] = {"log_dir": "./logs"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


class _RecordingSink:
    """记录型假 sink：捕获 emit 记录并统计 flush/close。"""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []
        self.flushed = 0
        self.closed = 0

    def emit(self, record: Dict[str, Any]) -> None:
        self.records.append(record)

    def flush(self, timeout: float = 1.0) -> None:
        self.flushed += 1

    def close(self) -> None:
        self.closed += 1


class _RaisingSink:
    """全方法抛异常的假 sink（红线用例）。"""

    def emit(self, record: Dict[str, Any]) -> None:
        raise RuntimeError("sink boom")

    def flush(self, timeout: float = 1.0) -> None:
        raise RuntimeError("flush boom")

    def close(self) -> None:
        raise RuntimeError("close boom")


def _recorder(
    sink: Optional[Any] = None, **overrides: Any
) -> "tuple[WorkflowRunLogger, Any]":
    settings = _settings(**overrides)
    actual_sink = sink if sink is not None else _RecordingSink()
    recorder = WorkflowRunLogger(
        run_id="run-0001",
        trace_id="trace-0001",
        tenant_id="tenant-a",
        business_category="manual",
        workflow_id="wf-001",
        definition_version=3,
        settings=settings,
        sink=actual_sink,
    )
    return recorder, actual_sink


def _result(success: bool = True, **overrides: Any) -> SimpleNamespace:
    payload: Dict[str, Any] = {
        "success": success,
        "output_value": {"answer": 42} if success else None,
        "duration_ms": 123 if success else 7,
        "error_code": None if success else "WF_NODE_EXECUTION_FAILED",
        "error_message": None if success else "bad",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestRecorderRedLine:
    """红线（必过门禁）：任何依赖抛异常时门面方法绝不抛出（INV-01）。"""

    def test_all_methods_survive_raising_sink(self):
        recorder, _ = _recorder(sink=_RaisingSink())
        recorder.begin_run()
        recorder.begin_node("n1", "llm", 0, {"q": 1})
        recorder.end_node(_result(True))
        recorder.record_node_failure("n1", "llm", 0, None, "NODE_X", "boom", "timeout")
        recorder.end_run(
            "succeeded",
            node_summary={"total": 1, "succeeded": 1, "failed": 0, "timeout": 0},
        )
        recorder.close()

    def test_all_methods_survive_raising_redactor(self, monkeypatch):
        def boom(self: RunLogRedactor, value: Any) -> Any:
            raise RuntimeError("redactor boom")

        monkeypatch.setattr(RunLogRedactor, "redact", boom)
        recorder, sink = _recorder()
        recorder.begin_run()
        recorder.begin_node("n1", "llm", 0)
        recorder.end_node(_result(True))
        recorder.record_node_failure("n1", "llm", 0, None, "NODE_X", "m", "failed")
        recorder.end_run("failed", error={"error_code": "WF_X", "error_message": "boom"})
        recorder.close()
        # 脱敏失败整条丢弃：绝不落未脱敏数据（INV-02）
        assert sink.records == []
        assert sink.flushed == 1

    def test_all_methods_survive_raising_truncator(self, monkeypatch):
        def boom(self: Truncator, value: Any, max_bytes: int) -> Any:
            raise RuntimeError("truncator boom")

        monkeypatch.setattr(Truncator, "truncate_field", boom)
        recorder, sink = _recorder()
        recorder.begin_run()
        recorder.begin_node("n1", "llm", 0, {"q": 1})
        recorder.end_node(_result(True))
        recorder.record_node_failure("n1", "llm", 0, None, "NODE_X", "boom", "timeout")
        recorder.end_run("succeeded")
        recorder.close()
        # 截断失败整条丢弃：触发截断的节点记录未落盘（INV-03，绝不落未截断超限载荷）；
        # 工作流记录不含截断字段（不触发截断器），正常落盘
        assert [r["record_type"] for r in sink.records] == [
            "workflow_run",
            "workflow_run",
        ]

    def test_constructor_survives_invalid_settings(self):
        # settings 缺失时走 get_settings()：不合法环境也能构造（容错口径）
        recorder = WorkflowRunLogger(run_id="r", trace_id="t")
        recorder.close()


class TestRecorderWorkflowRecords:
    """工作流级记录（design 2.0.1（1）（3）、E-9、DC-09）。"""

    def test_begin_run_running_record_omits_terminal_fields(self):
        recorder, sink = _recorder()
        started = datetime(2026, 10, 24, 8, 0, 0, tzinfo=timezone.utc)
        recorder.begin_run(started_at=started)
        assert len(sink.records) == 1
        record = sink.records[0]
        assert record["record_type"] == "workflow_run"
        assert record["schema_version"] == "1.0"
        assert record["status"] == "running"
        assert record["level"] == "INFO"
        assert record["run_id"] == "run-0001"
        assert record["trace_id"] == "trace-0001"
        assert record["tenant_id"] == "tenant-a"
        assert record["business_category"] == "manual"
        assert record["workflow_id"] == "wf-001"
        assert record["definition_version"] == 3
        assert record["started_at"] == started.isoformat()
        assert "finished_at" not in record
        assert "duration_ms" not in record
        json.dumps(record, ensure_ascii=False, default=str)

    def test_end_run_levels_summary_and_error_key(self):
        recorder, sink = _recorder()
        recorder.begin_run()
        summary = {"total": 3, "succeeded": 2, "failed": 1, "timeout": 0}
        finished = datetime(2026, 10, 24, 8, 0, 2, tzinfo=timezone.utc)
        recorder.end_run("succeeded", finished_at=finished, duration_ms=150, node_summary=summary)
        recorder.end_run("failed", error={"error_code": "WF_X", "error_message": "boom"})
        succeeded, failed = sink.records[1], sink.records[2]
        assert succeeded["level"] == "INFO" and succeeded["status"] == "succeeded"
        assert succeeded["node_summary"] == summary
        assert succeeded["duration_ms"] == 150
        assert succeeded["finished_at"] == finished.isoformat()
        assert failed["level"] == "ERROR" and failed["status"] == "failed"
        assert failed["error"]["error_code"] == "WF_X"
        # started_at 沿用 begin_run 登记的开始时间
        assert failed["started_at"] == succeeded["started_at"] == sink.records[0]["started_at"]

    def test_end_run_unknown_status_maps_warn(self):
        recorder, sink = _recorder()
        recorder.end_run("unknown")
        assert sink.records[0]["level"] == "WARN"


class TestRecorderNodeRecords:
    """节点级记录（design 2.0.1（1）（2）、E-9、DC-09/:510）。"""

    def test_node_lifecycle_records(self):
        recorder, sink = _recorder()
        recorder.begin_node("n1", "llm", 2, {"q": "hello"})
        recorder.end_node(_result(True), subworkflow_depth=1)
        begin_record, end_record = sink.records
        assert begin_record["record_type"] == "node_run"
        assert begin_record["status"] == "running" and begin_record["level"] == "INFO"
        assert begin_record["node_id"] == "n1"
        assert begin_record["node_type"] == "llm"
        assert begin_record["round_index"] == 2
        assert begin_record["input"] == {"q": "hello"}
        assert "finished_at" not in begin_record
        assert "duration_ms" not in begin_record
        assert end_record["status"] == "succeeded" and end_record["level"] == "INFO"
        assert end_record["node_id"] == "n1"
        assert end_record["output"] == {"answer": 42}
        assert end_record["duration_ms"] == 123
        assert end_record["subworkflow_depth"] == 1
        assert "error" not in end_record

    def test_end_node_failure_result(self):
        recorder, sink = _recorder()
        recorder.begin_node("n2", "tool", 0)
        recorder.end_node(_result(False))
        record = sink.records[1]
        assert record["status"] == "failed" and record["level"] == "ERROR"
        assert record["error"]["error_code"] == "WF_NODE_EXECUTION_FAILED"
        assert record["error"]["error_message"] == "bad"

    def test_record_node_failure_failed_and_timeout(self):
        recorder, sink = _recorder()
        recorder.record_node_failure(
            "n3",
            "agent",
            1,
            {"q": 1},
            error_code="WF_NODE_EXECUTION_FAILED",
            error_message="boom",
            status="failed",
            error_type="ValueError",
            error_stack="Traceback (most recent call last)",
        )
        first = sink.records[0]
        assert first["level"] == "ERROR" and first["status"] == "failed"
        assert first["error"]["error_type"] == "ValueError"
        assert first["error"]["error_stack"] == "Traceback (most recent call last)"
        assert first["input"] == {"q": 1}
        recorder.record_node_failure(
            "n4", "agent", 2, None, error_code="NODE_TIMEOUT", error_message="t/o", status="timeout"
        )
        second = sink.records[1]
        assert second["level"] == "ERROR" and second["status"] == "timeout"
        assert second["error"]["error_type"] == "TimeoutError"
        assert second["input"] == {}

    def test_duration_ms_computed_from_times_when_missing(self):
        recorder, sink = _recorder()
        t0 = datetime(2026, 10, 24, 8, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(milliseconds=1500)
        recorder.record_node_failure(
            "n5", "llm", 0, None, "NODE_TIMEOUT", "t/o", "timeout", started_at=t0, finished_at=t1
        )
        assert sink.records[0]["duration_ms"] == 1500


class TestRecorderConformance:
    """兜底、一致性、值域（INV-09、DC-06）。"""

    def test_tenant_and_category_fallbacks(self):
        sink = _RecordingSink()
        recorder = WorkflowRunLogger(
            run_id="r", trace_id="t", settings=_settings(), sink=sink
        )
        recorder.begin_run()
        record = sink.records[0]
        assert record["tenant_id"] == "default"
        assert record["business_category"] == "default"

    def test_run_id_trace_id_consistency_and_enum_domains(self):
        recorder, sink = _recorder()
        recorder.begin_run()
        recorder.begin_node("n1", "llm", 0)
        recorder.end_node(_result(True))
        recorder.record_node_failure("n2", "llm", 1, None, "X", "m", "failed")
        recorder.end_run("succeeded")
        for record in sink.records:
            assert record["run_id"] == "run-0001"
            assert record["trace_id"] == "trace-0001"
            assert record["level"] in LEVELS
            assert record["business_category"] in BUSINESS_CATEGORIES
            if record["record_type"] == "node_run":
                assert record["status"] in NODE_RUN_STATUSES + (STATUS_RUNNING,)
            else:
                assert record["status"] in WORKFLOW_RUN_STATUSES

    def test_chinese_values_preserved(self):
        recorder, sink = _recorder()
        recorder.begin_node("n1", "llm", 0, {"query": "工作流执行"})
        record = sink.records[0]
        assert record["input"]["query"] == "工作流执行"
        line = json.dumps(record, ensure_ascii=False, default=str)
        assert "工作流执行" in line


class TestRecorderRedactionTruncation:
    """脱敏/截断联动（INV-02、INV-03、design 2.6）。"""

    def test_blacklist_and_value_pattern_redaction(self):
        recorder, sink = _recorder()
        recorder.begin_node(
            "n1",
            "llm",
            0,
            {
                "password": "super-secret",
                "nested": {"api_key": "k-123"},
                "text": "Bearer abc.def.ghi-jkl_mno",
            },
        )
        payload = sink.records[0]["input"]
        assert payload["password"] == "[REDACTED]"
        assert payload["nested"]["api_key"] == "[REDACTED]"
        assert "abc.def.ghi-jkl_mno" not in json.dumps(payload, ensure_ascii=False)

    def test_truncation_flag_and_note(self):
        # 载荷用中文长文本：不触值模式脱敏（40+ 位 ASCII 随机串会被先替换为
        # [REDACTED] 而到不了截断），确保走到截断分支（脱敏先于截断，INV-03）
        recorder, sink = _recorder(log_max_payload_bytes=1024)
        recorder.begin_node("n1", "llm", 0, {"blob": "载荷内容" * 400})
        record = sink.records[0]
        assert record["truncated"] is True
        assert "截断" in record["payload_note"]
        assert isinstance(record["input"], str)
        assert record["input"].endswith("...[TRUNCATED]")

    def test_error_stack_truncated_with_flag(self):
        # 同上：error_stack 用中文长文本，避免值模式脱敏先行占位
        recorder, sink = _recorder(log_max_payload_bytes=1024)
        recorder.record_node_failure(
            "n1", "llm", 0, None, "X", "m", "failed", error_stack="堆栈跟踪帧详情 " * 300
        )
        record = sink.records[0]
        assert record["truncated"] is True
        assert record["error"]["error_stack"].endswith("...[TRUNCATED]")

    def test_no_truncation_no_flag(self):
        recorder, sink = _recorder()
        recorder.begin_node("n1", "llm", 0, {"q": "small"})
        record = sink.records[0]
        assert "truncated" not in record
        assert "payload_note" not in record


class TestRecorderLifecycle:
    """生命周期：close 冲刷/幂等、自建 sink 全链路。"""

    def test_close_flushes_shared_sink_without_closing(self):
        sink = _RecordingSink()
        recorder, _ = _recorder(sink=sink)
        recorder.close()
        assert sink.flushed == 1
        assert sink.closed == 0
        recorder.close()
        assert sink.flushed == 1

    def test_default_construction_writes_to_real_sink(self, tmp_path):
        settings = _settings(log_dir=str(tmp_path / "logs"))
        recorder = WorkflowRunLogger(
            run_id="r1",
            trace_id="t1",
            tenant_id="tenant-a",
            business_category="manual",
            workflow_id="wf-1",
            settings=settings,
        )
        recorder.begin_run()
        recorder.close()
        assert _wait_for(lambda: list((tmp_path / "logs").rglob("*.jsonl")))
        files = list((tmp_path / "logs").rglob("*.jsonl"))
        record = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert record["record_type"] == "workflow_run"
        assert record["run_id"] == "r1"
        assert record["status"] == "running"
