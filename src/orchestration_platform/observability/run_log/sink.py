# =============================================================================
# 运行日志落盘通道（observability/run_log/sink.py，workflow_run_logging CHG-10）
#
# 职责：JSONL 落盘 + 异步队列 + 双策略轮转 + 过期清理编排（RunLogSink）。
# 设计意图（design DC-11、2.1.3.3、INV-01、INV-D-01、R-C）：
#   1. 惰性初始化：首次 emit 才创建目录/handler/线程，未初始化前不产生任何 logs/ 目录；
#   2. 异步通道：logger（业务侧）挂 _CountingQueueHandler → QueueListener 守护线程
#      → _FanOutHandler 按 (record_type, tenant, category, date) 路由到
#      TimeSizeRotatingFileHandler；log_async_enabled=False 时扇出直挂（同步）；
#   3. 队列满丢弃 + dropped_count；写入/序列化失败吞异常 + failed_count + 一次性告警；
#   4. 级别过滤：record["level"] 低于 log_level 的记录不落盘（spec 5.5）；
#   5. 清理触发：初始化时一次 + 每次轮转回调（保护活动文件）。
# =============================================================================

import json
import logging
import os
import queue
import time
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener
from typing import Any, Dict, List, Optional, Tuple

from orchestration_platform.configuration.settings import Settings
from orchestration_platform.observability.run_log.formatter import JsonLinesFormatter
from orchestration_platform.observability.run_log.handler import TimeSizeRotatingFileHandler
from orchestration_platform.observability.run_log.pathing import LogPathResolver
from orchestration_platform.observability.run_log.retention import LogRetentionCleaner
from orchestration_platform.observability.structured_logger import StructuredLogger

logger = logging.getLogger(__name__)

_LEVEL_ORDER = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


class _CountingQueueHandler(QueueHandler):
    """入队计数 handler：队列满等入队失败丢弃并计数（dropped_count）。"""

    def __init__(self, queue_obj: "queue.Queue", sink: "RunLogSink") -> None:
        super().__init__(queue_obj)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.enqueue(self.prepare(record))
        except Exception:
            self._sink._note_dropped()


class _FanOutHandler(logging.Handler):
    """扇出路由 handler：解析 JSON 行，按 (record_type, tenant, category, date) 路由到对应文件 handler。"""

    def __init__(self, sink: "RunLogSink") -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink
        self._handlers: Dict[Tuple[str, str, str, str], logging.Handler] = {}

    def emit(self, record: logging.LogRecord) -> None:
        line = record.getMessage()
        try:
            payload = json.loads(line)
        except ValueError:
            self._sink._note_write_failure()
            return
        route_key = self._sink._route_key(payload)
        handler = self._handlers.get(route_key)
        if handler is None:
            try:
                handler = self._sink._build_target_handler(route_key)
            except Exception:
                self._sink._note_write_failure()
                return
            self._handlers[route_key] = handler
        handler.handle(record)

    def active_paths(self) -> List[str]:
        paths: List[str] = []
        for handler in self._handlers.values():
            base = getattr(handler, "baseFilename", None)
            if base:
                paths.append(str(base))
        return paths

    def flush_all(self) -> None:
        for handler in self._handlers.values():
            try:
                handler.flush()
            except Exception:
                pass

    def close(self) -> None:
        for handler in self._handlers.values():
            try:
                handler.close()
            except Exception:
                pass
        self._handlers.clear()
        super().close()


class RunLogSink:
    """运行日志落盘通道（非阻塞、容错、惰性初始化）。"""

    _DEFAULT_LOGGER_NAME = "orchestration_platform.run_log"

    def __init__(self, settings: Optional[Settings] = None, logger_name: Optional[str] = None) -> None:
        if settings is None:
            from orchestration_platform.configuration.settings import get_settings

            settings = get_settings()
        self._settings = settings
        self._logger_name = logger_name or self._DEFAULT_LOGGER_NAME
        self._level_floor = _LEVEL_ORDER.get(str(settings.log_level).upper(), logging.INFO)
        self._resolver = LogPathResolver(settings.log_dir)
        self._cleaner = LogRetentionCleaner(settings.log_dir, settings.log_retention_days)
        self._async = bool(settings.log_async_enabled)
        self._rotation_enabled = bool(settings.log_rotation_enabled)
        self._initialized = False
        self._closed = False
        self._dropped_count = 0
        self._failed_count = 0
        self._warned_dropped = False
        self._warned_write_failure = False
        self._logger: Optional[logging.Logger] = None
        self._structured_logger: Optional[StructuredLogger] = None
        self._attached_handler: Optional[logging.Handler] = None
        self._queue: Optional["queue.Queue"] = None
        self._listener: Optional[QueueListener] = None
        self._dispatcher: Optional[_FanOutHandler] = None

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def failed_count(self) -> int:
        return self._failed_count

    def emit(self, record: Dict[str, Any]) -> None:
        """非阻塞提交一条已脱敏/截断的记录；任何内部异常均被吞掉（INV-01）。"""
        if self._closed:
            return
        try:
            level_name = str(record.get("level", "INFO")).upper()
            if _LEVEL_ORDER.get(level_name, logging.INFO) < self._level_floor:
                return
            if not self._initialized:
                self._initialize()
            # 经 StructuredLogger.log_record 逐行输出（design Snk --> SL，DC-01 生产调用点）
            self._structured_logger.log_record(record)
        except Exception:
            self._note_write_failure()

    def flush(self) -> None:
        """冲刷后台队列与文件缓冲（尽力而为，不抛出）。"""
        if self._closed or not self._initialized:
            return
        try:
            self._drain_queue()
            if self._dispatcher is not None:
                self._dispatcher.flush_all()
        except Exception:
            self._note_write_failure()

    def _drain_queue(self) -> None:
        """等待队列内记录全部完成 handle（QueueListener 消费完会 task_done；5s 截止，不抛出）。"""
        if self._queue is None:
            return
        deadline = time.monotonic() + 5.0
        while self._queue.unfinished_tasks > 0:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.005)

    def close(self) -> None:
        """停止后台线程并关闭文件句柄（幂等）。"""
        if self._closed:
            return
        self._closed = True
        if not self._initialized:
            return
        try:
            if self._listener is not None:
                # 先排空：stop() 的哨兵为 put_nowait，满队列会抛 queue.Full
                self._drain_queue()
                self._listener.stop()
            if self._dispatcher is not None:
                self._dispatcher.close()
            if self._logger is not None and self._attached_handler is not None:
                self._logger.removeHandler(self._attached_handler)
        except Exception:
            logger.warning("运行日志落盘通道关闭异常", exc_info=True)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _initialize(self) -> None:
        self._logger = logging.getLogger(self._logger_name)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._structured_logger = StructuredLogger(self._logger_name)
        self._dispatcher = _FanOutHandler(self)
        if self._async:
            self._queue = queue.Queue(maxsize=self._settings.log_queue_max_size)
            counting_handler = _CountingQueueHandler(self._queue, self)
            counting_handler.setLevel(logging.DEBUG)
            self._attached_handler = counting_handler
            self._logger.addHandler(counting_handler)
            self._listener = QueueListener(self._queue, self._dispatcher, respect_handler_level=False)
            self._listener.start()
        else:
            self._attached_handler = self._dispatcher
            self._logger.addHandler(self._dispatcher)
        self._initialized = True
        # 进程启动触发一次清理（保护活动文件）
        self._run_retention_cleanup()

    def _route_key(self, payload: Dict[str, Any]) -> Tuple[str, str, str, str]:
        record_type = str(payload.get("record_type", "unknown"))
        tenant = str(payload.get("tenant_id", "") or "")
        category = str(payload.get("business_category", "") or "")
        timestamp = str(payload.get("timestamp", "") or "")
        date_part = timestamp[:10].replace("-", "") if len(timestamp) >= 10 else ""
        if not date_part:
            date_part = datetime.now().strftime("%Y%m%d")
        return (record_type, tenant, category, date_part)

    def _build_target_handler(self, route_key: Tuple[str, str, str, str]) -> logging.Handler:
        record_type, tenant, category, date_part = route_key
        when = datetime.strptime(date_part, "%Y%m%d")
        path = self._resolver.resolve(record_type, tenant, category, when, seq=0)
        os.makedirs(path.parent, exist_ok=True)
        if self._rotation_enabled:
            handler = TimeSizeRotatingFileHandler(
                filename=str(path),
                when=self._settings.log_rotation_when,
                interval=1,
                max_bytes=self._settings.log_rotation_max_bytes,
                rollover_callback=self._run_retention_cleanup,
                failure_notifier=self._note_write_failure,
            )
        else:
            handler = logging.FileHandler(str(path), encoding="utf-8", delay=True)
            handler.terminator = ""
        handler.setFormatter(JsonLinesFormatter())
        handler.setLevel(logging.DEBUG)
        return handler

    def _run_retention_cleanup(self) -> None:
        try:
            self._cleaner.cleanup(now=None, active_paths=self._collect_active_paths())
        except Exception:
            logger.warning("运行日志过期清理执行失败", exc_info=True)

    def _collect_active_paths(self) -> List[str]:
        if self._dispatcher is None:
            return []
        return self._dispatcher.active_paths()

    def _note_dropped(self) -> None:
        self._dropped_count += 1
        if not self._warned_dropped:
            self._warned_dropped = True
            logger.warning("运行日志队列积压，已开始丢弃（后续不再重复告警）")

    def _note_write_failure(self) -> None:
        self._failed_count += 1
        if not self._warned_write_failure:
            self._warned_write_failure = True
            logger.warning("运行日志写入失败，已降级为计数（后续不再重复告警）")
