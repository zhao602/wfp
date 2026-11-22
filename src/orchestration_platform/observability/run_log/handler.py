# =============================================================================
# 运行日志双策略轮转 handler（observability/run_log/handler.py，workflow_run_logging CHG-08）
#
# 职责：按天 + 按大小双策略滚动（TimeSizeRotatingFileHandler）。
# 设计意图（design DC-07、2.0.2、INV-07、INV-D-07）：
#   1. 继承标准库 logging.handlers.TimedRotatingFileHandler（when="midnight", interval=1）；
#   2. shouldRollover = _size_exceeded() or super().shouldRollover(record)；
#   3. 大小轮转"先切名后重开"：以 namer 将当前文件归档为规范命名
#      （扫描现有 _NNN 取下一序号，3 位零填充），随后重开同路径活动文件
#      （design 2.0.2：当天活动文件不带序号）；
#   4. 时间轮转不切名（文件名已含日期，跨日新文件由落盘通道按日期键切换），
#      仅重开句柄、推进轮转点并触发清理回调；
#   5. 写入异常经 handleError 吞掉并回调计数（不打印 stderr，DC-11）。
# =============================================================================

import logging
import os
import re
import time
from logging.handlers import TimedRotatingFileHandler
from typing import Callable, Optional


class TimeSizeRotatingFileHandler(TimedRotatingFileHandler):
    """按天 + 按大小双策略滚动的 JSONL 文件 handler。"""

    def __init__(
        self,
        filename: str,
        when: str = "midnight",
        interval: int = 1,
        max_bytes: int = 0,
        encoding: str = "utf-8",
        delay: bool = True,
        rollover_callback: Optional[Callable[[], None]] = None,
        failure_notifier: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(filename, when=when, interval=interval, encoding=encoding, delay=delay)
        self.max_bytes = max_bytes
        self.rollover_callback = rollover_callback
        self.failure_notifier = failure_notifier
        # JsonLinesFormatter 自带换行，置空 handler 终止符防双换行
        self.terminator = ""
        # 归档命名器：规范命名（含序号）
        self.namer = self._archive_namer

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self._size_exceeded():
            return True
        return bool(super().shouldRollover(record))

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        # 大小轮转：先切名（归档为下一序号）后重开，避免丢行
        if self._size_exceeded() and os.path.exists(self.baseFilename):
            archive_path = self.namer(self.baseFilename)
            try:
                os.replace(self.baseFilename, archive_path)
            except OSError:
                # 归档失败降级：继续写原文件
                pass
        # 重开活动文件（时间轮转不切名：文件名已含日期）
        self.stream = self._open()
        # 推进时间轮转点
        current_time = int(time.time())
        new_rollover_at = self.computeRollover(current_time)
        while new_rollover_at <= current_time:
            new_rollover_at += self.interval
        self.rolloverAt = new_rollover_at
        # 轮转后触发一次清理回调
        if self.rollover_callback is not None:
            try:
                self.rollover_callback()
            except Exception:
                # 回调异常不影响写入主流程（INV-D-12 精神）
                pass

    def handleError(self, record: logging.LogRecord) -> None:
        # 写入失败：吞掉（不打印 stderr）、回调计数告警（DC-11）
        if self.failure_notifier is not None:
            try:
                self.failure_notifier()
            except Exception:
                pass

    def _size_exceeded(self) -> bool:
        if not self.max_bytes or self.max_bytes <= 0:
            return False
        # FileHandler 为块缓冲：先冲刷缓冲再取磁盘大小，避免小阈值轮转失灵
        if self.stream is not None:
            try:
                self.flush()
            except Exception:
                pass
        try:
            return os.stat(self.baseFilename).st_size >= self.max_bytes
        except OSError:
            return False

    def _archive_namer(self, default_name: str) -> str:
        """将归档名改写为规范命名：{stem}_{NNN:03d}.jsonl（扫描现有序号取下一序号）。"""
        directory = os.path.dirname(self.baseFilename)
        stem = os.path.splitext(os.path.basename(self.baseFilename))[0]
        next_seq = self._max_existing_seq(directory, stem) + 1
        return os.path.join(directory, f"{stem}_{next_seq:03d}.jsonl")

    @staticmethod
    def _max_existing_seq(directory: str, stem: str) -> int:
        best = 0
        pattern = re.compile(re.escape(stem) + r"_(\d{3,})\.jsonl$")
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not entry.is_file():
                        continue
                    match = pattern.match(entry.name)
                    if match is not None:
                        best = max(best, int(match.group(1)))
        except OSError:
            return 0
        return best
