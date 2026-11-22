# =============================================================================
# 运行日志落盘通道 / 双策略轮转 / 过期清理用例
# （tests/unit/test_run_log_sink_rotation_retention.py）
#
# 覆盖验收（tasks T4.5 / design DC-07、DC-11、E-10、E-11 / INV-01、INV-D-01、R-C）：
#   1. RunLogSink：惰性初始化、扇出路由、级别过滤、异步/同步、非阻塞、幂等关闭；
#   2. 降级：不可写目录计数不抛、序列化失败计数、队列满丢弃计数（DC-11）；
#   3. 轮转：跨日切换文件、大小轮转归档不丢行、轮转回调、写失败经通知器计数、
#      关闭轮转不归档；
#   4. 清理：过期删除、活动文件保护、0 天不清理、缺目录返 0；
#   5. 编排：初始化触发一次清理、1MB 真实路径轮转触发清理且零丢失。
# =============================================================================

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

from orchestration_platform.configuration.settings import Settings
from orchestration_platform.observability.run_log.formatter import JsonLinesFormatter
from orchestration_platform.observability.run_log.handler import TimeSizeRotatingFileHandler
from orchestration_platform.observability.run_log.retention import LogRetentionCleaner
from orchestration_platform.observability.run_log.sink import RunLogSink, _FanOutHandler

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


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: Dict[str, Any] = {"log_dir": str(tmp_path / "logs")}
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def make_sink(tmp_path: Path):
    """构造使用独立 logger 名（测试隔离）与 tmp 目录的 RunLogSink，teardown 统一关闭。"""
    created: List[RunLogSink] = []

    def _make(**overrides: Any) -> RunLogSink:
        sink = RunLogSink(
            settings=_settings(tmp_path, **overrides),
            logger_name=f"test.run_log.{uuid.uuid4().hex}",
        )
        created.append(sink)
        return sink

    yield _make
    for sink in created:
        try:
            sink.close()
        except Exception:
            pass


def _record(seq: int = 0, pad: int = 0, **overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "schema_version": "1.0",
        "record_type": "node_run",
        "run_id": f"run-{seq:04d}",
        "trace_id": "trace-0001",
        "tenant_id": "tenant-a",
        "business_category": "default",
        "level": "INFO",
        "timestamp": "2026-10-24T12:00:00.000000+00:00",
        "status": "succeeded",
        "started_at": "2026-10-24T12:00:00.000000+00:00",
        "finished_at": "2026-10-24T12:00:01.000000+00:00",
        "duration_ms": 1000,
        "node_id": f"node-{seq}",
        "node_type": "llm",
        "round_index": 0,
        "input": {"query": "x" * pad} if pad else {"query": "hello"},
        "output": {"answer": "ok"},
    }
    payload.update(overrides)
    return payload


def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _log_record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="test.run_log",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )


class TestRunLogSinkBasics:
    """RunLogSink 基础行为：惰性初始化、扇出、级别过滤、异步/同步、非阻塞、幂等关闭。"""

    def test_lazy_init_creates_no_directory_before_first_emit(self, tmp_path, make_sink):
        sink = make_sink()
        assert not (tmp_path / "logs").exists()
        sink.emit(_record(0))
        assert _wait_for(lambda: (tmp_path / "logs").exists())

    def test_emit_writes_single_jsonl_line(self, tmp_path, make_sink):
        sink = make_sink()
        record = _record(0)
        sink.emit(record)
        sink.flush()
        files = list((tmp_path / "logs").rglob("*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert content.endswith("\n")
        assert content.count("\n") == 1
        assert json.loads(content) == record

    def test_fan_out_by_record_type_tenant_and_category(self, tmp_path, make_sink):
        sink = make_sink()
        sink.emit(_record(0))
        sink.emit(
            _record(
                1,
                record_type="workflow_run",
                tenant_id="tenant-b",
                business_category="manual",
                workflow_id="wf-001",
            )
        )
        sink.flush()
        base = tmp_path / "logs"
        assert (base / "tenant-a" / "default" / "node_run_default_20261024.jsonl").exists()
        assert (base / "tenant-b" / "manual" / "workflow_run_manual_20261024.jsonl").exists()

    def test_level_filter_skips_records_below_floor(self, tmp_path, make_sink):
        sink = make_sink(log_level="WARN")
        sink.emit(_record(0, level="INFO"))
        assert not (tmp_path / "logs").exists()
        sink.emit(_record(1, level="ERROR"))
        assert _wait_for(lambda: list((tmp_path / "logs").rglob("*.jsonl")))
        sink.flush()
        lines = [
            line
            for path in (tmp_path / "logs").rglob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        assert len(lines) == 1
        assert json.loads(lines[0])["level"] == "ERROR"

    def test_sync_mode_writes_records(self, tmp_path, make_sink):
        sink = make_sink(log_async_enabled=False)
        sink.emit(_record(0))
        sink.emit(_record(1))
        sink.flush()
        files = list((tmp_path / "logs").rglob("*.jsonl"))
        assert len(files) == 1
        assert _count_lines(files[0]) == 2

    def test_async_emit_is_non_blocking(self, make_sink):
        sink = make_sink()
        start = time.monotonic()
        for i in range(100):
            sink.emit(_record(i))
        elapsed = time.monotonic() - start
        assert elapsed < 1.0
        sink.flush()

    def test_close_is_idempotent_and_silent_afterwards(self, tmp_path, make_sink):
        sink = make_sink()
        sink.emit(_record(0))
        assert _wait_for(lambda: list((tmp_path / "logs").rglob("*.jsonl")))
        sink.flush()
        sink.close()
        sink.close()
        sink.emit(_record(1))
        files = list((tmp_path / "logs").rglob("*.jsonl"))
        assert sum(_count_lines(path) for path in files) == 1


class TestRunLogSinkDegradation:
    """降级路径（DC-11）：不可写目录、序列化失败、队列满——均计数不抛。"""

    def test_unwritable_log_dir_counts_failure_async(self, tmp_path, make_sink):
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("not a directory", encoding="utf-8")
        sink = make_sink(log_dir=str(blocker))
        sink.emit(_record(0))
        assert _wait_for(lambda: sink.failed_count >= 1)

    def test_unwritable_log_dir_counts_failure_sync(self, tmp_path, make_sink):
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("not a directory", encoding="utf-8")
        sink = make_sink(log_async_enabled=False, log_dir=str(blocker))
        sink.emit(_record(0))
        assert sink.failed_count >= 1

    def test_serialization_failure_counted(self, make_sink):
        sink = make_sink()
        record = _record(0)
        circular: Dict[str, Any] = {}
        circular["self"] = circular
        record["input"] = circular
        sink.emit(record)
        assert sink.failed_count >= 1

    def test_queue_full_drops_and_counts(self, make_sink, monkeypatch):
        released = threading.Event()
        original_emit = _FanOutHandler.emit

        def blocking_emit(self, record):
            if not released.is_set():
                released.wait(timeout=10)
            original_emit(self, record)

        monkeypatch.setattr(_FanOutHandler, "emit", blocking_emit)
        sink = make_sink(log_queue_max_size=2)
        for i in range(12):
            sink.emit(_record(i))
        assert sink.dropped_count >= 1
        released.set()
        sink.flush()


class TestRotation:
    """双策略轮转（DC-07）：跨日切换、大小归档不丢行、回调、通知器、关闭轮转。"""

    def test_date_change_switches_file(self, tmp_path, make_sink):
        sink = make_sink()
        sink.emit(_record(0, timestamp="2026-10-24T12:00:00.000000+00:00"))
        sink.emit(_record(1, timestamp="2026-10-25T08:00:00.000000+00:00"))
        sink.flush()
        base = tmp_path / "logs" / "tenant-a" / "default"
        first = base / "node_run_default_20261024.jsonl"
        second = base / "node_run_default_20261025.jsonl"
        assert first.exists() and second.exists()
        assert _count_lines(first) == 1
        assert _count_lines(second) == 1

    def test_size_rollover_archives_without_loss(self, tmp_path):
        directory = tmp_path / "rotate"
        directory.mkdir()
        base = directory / "node_run_default_20261024.jsonl"
        rollovers: List[int] = []
        failures: List[int] = []
        handler = TimeSizeRotatingFileHandler(
            filename=str(base),
            when="midnight",
            max_bytes=400,
            delay=True,
            rollover_callback=lambda: rollovers.append(1),
            failure_notifier=lambda: failures.append(1),
        )
        handler.setFormatter(JsonLinesFormatter())
        # 排除午夜时间轮转干扰：仅考察大小轮转
        handler.rolloverAt = int(time.time()) + 86400
        for i in range(30):
            handler.handle(_log_record(json.dumps({"seq": i, "pad": "x" * 60})))
        handler.flush()
        archives = sorted(path for path in directory.glob("*_[0-9][0-9][0-9].jsonl"))
        assert archives, "未发生大小轮转"
        seqs = [int(path.name.rsplit("_", 1)[1].split(".")[0]) for path in archives]
        assert seqs == list(range(1, len(seqs) + 1))
        assert base.exists()
        total = _count_lines(base) + sum(_count_lines(path) for path in archives)
        assert total == 30
        assert len(rollovers) == len(archives)
        assert failures == []
        handler.close()

    def test_handle_error_notifies_without_raising(self, tmp_path):
        directory = tmp_path / "notify"
        directory.mkdir()
        calls: List[int] = []
        handler = TimeSizeRotatingFileHandler(
            filename=str(directory / "node_run_default_20261024.jsonl"),
            when="midnight",
            max_bytes=0,
            delay=True,
            failure_notifier=lambda: calls.append(1),
        )
        record = _log_record("{}")
        # 直接调用 handleError：验证吞掉 + 通知器契约
        handler.handleError(record)
        assert len(calls) == 1

        class _BrokenStream:
            def write(self, *args):
                raise OSError("disk full")

            def flush(self):
                raise OSError("disk full")

            def close(self):
                pass

        # 写入失败路径：emit 内部经 handleError 吞掉并通知，不抛出
        handler.stream = _BrokenStream()
        handler.handle(record)
        assert len(calls) == 2

    def test_rotation_disabled_sink_writes_without_archives(self, tmp_path, make_sink):
        sink = make_sink(log_rotation_enabled=False)
        for i in range(50):
            sink.emit(_record(i))
        sink.flush()
        base = tmp_path / "logs" / "tenant-a" / "default"
        assert list(base.glob("*_[0-9][0-9][0-9].jsonl")) == []
        assert _count_lines(base / "node_run_default_20261024.jsonl") == 50


class TestRetentionCleaner:
    """过期清理（DC-07）：删除超期、保护活动文件、0 天不清理、缺目录返 0。"""

    def test_cleanup_deletes_expired_protects_active(self, tmp_path):
        directory = tmp_path / "logs" / "tenant-a" / "default"
        directory.mkdir(parents=True)
        expired = directory / "stale_20260101.jsonl"
        expired.write_text("old", encoding="utf-8")
        active = directory / "node_run_default_20261024.jsonl"
        active.write_text("current", encoding="utf-8")
        old_ts = time.time() - 40 * 86400
        os.utime(expired, (old_ts, old_ts))
        os.utime(active, (old_ts, old_ts))
        cleaner = LogRetentionCleaner(tmp_path / "logs", retention_days=14)
        assert cleaner.cleanup(active_paths=[active]) == 1
        assert not expired.exists()
        assert active.exists()

    def test_zero_retention_days_deletes_nothing(self, tmp_path):
        directory = tmp_path / "logs" / "tenant-a"
        directory.mkdir(parents=True)
        stale = directory / "stale.jsonl"
        stale.write_text("old", encoding="utf-8")
        old_ts = time.time() - 40 * 86400
        os.utime(stale, (old_ts, old_ts))
        cleaner = LogRetentionCleaner(tmp_path / "logs", retention_days=0)
        assert cleaner.cleanup() == 0
        assert stale.exists()

    def test_missing_directory_returns_zero(self, tmp_path):
        cleaner = LogRetentionCleaner(tmp_path / "nope", retention_days=14)
        assert cleaner.cleanup() == 0

    def test_subdirectories_are_not_deleted(self, tmp_path):
        nested = tmp_path / "logs" / "tenant-a" / "default" / "subdir"
        nested.mkdir(parents=True)
        old_ts = time.time() - 40 * 86400
        os.utime(nested, (old_ts, old_ts))
        cleaner = LogRetentionCleaner(tmp_path / "logs", retention_days=14)
        assert cleaner.cleanup() == 0
        assert nested.exists()


class TestSinkCleanupOrchestration:
    """清理编排：初始化触发一次、1MB 真实路径轮转触发清理且零丢失。"""

    def test_init_cleanup_removes_expired_files(self, tmp_path, make_sink):
        directory = tmp_path / "logs" / "tenant-a" / "default"
        directory.mkdir(parents=True)
        stale = directory / "stale.jsonl"
        stale.write_text("old", encoding="utf-8")
        old_ts = time.time() - 40 * 86400
        os.utime(stale, (old_ts, old_ts))
        sink = make_sink()
        sink.emit(_record(0))
        assert _wait_for(lambda: list((tmp_path / "logs").rglob("node_run_*.jsonl")))
        assert not stale.exists()

    def test_rollover_triggers_cleanup_and_keeps_all_lines(self, tmp_path, make_sink):
        directory = tmp_path / "logs" / "tenant-a" / "default"
        sink = make_sink(log_rotation_max_bytes=1048576)
        sink.emit(_record(0, pad=600))
        assert _wait_for(lambda: list((tmp_path / "logs").rglob("node_run_*.jsonl")))
        # 初始化清理已执行完毕后植入过期文件，仅能被轮转触发的清理删除
        directory.mkdir(parents=True, exist_ok=True)
        stale = directory / "stale.jsonl"
        stale.write_text("old", encoding="utf-8")
        old_ts = time.time() - 40 * 86400
        os.utime(stale, (old_ts, old_ts))
        for i in range(1, 2600):
            sink.emit(_record(i, pad=600))
        sink.flush()
        archives = sorted(path for path in directory.glob("*_[0-9][0-9][0-9].jsonl"))
        assert archives, "1MB 阈值未触发大小轮转"
        assert not stale.exists()
        total = sum(_count_lines(path) for path in directory.glob("*.jsonl"))
        assert total == 2600
