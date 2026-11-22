# =============================================================================
# 用量缓冲队列（governance/metering/usage_buffer_queue.py）
#
# 职责：异步入队、批量写入用量记录，支持持久化可恢复。
# 设计意图（对应 spec 5.2、design 2.5、任务 T57.2）：
#   1. 异步入队、批量写入（每 100 条或每 5 秒）；
#   2. 持久化可恢复：缓冲队列持久化至本地文件，进程重启时恢复；
#   3. 缓冲队列最大容量由 METERING_BUFFER_QUEUE_MAX_SIZE 控制；
#   4. Windows 沙箱下使用 SelectorEventLoop 验证可用。
# =============================================================================

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.metering.models import TokenUsageRecord


class UsageBufferQueue:
    """用量缓冲队列。

    异步入队，批量写入用量仓储。
    支持持久化至本地文件，进程异常退出后重启时恢复未写入用量。
    """

    def __init__(
        self,
        flush_callback: Optional[Callable[[List[TokenUsageRecord]], Any]] = None,
        max_size: Optional[int] = None,
        flush_batch_size: int = 100,
        flush_interval_seconds: float = 5.0,
        persist_path: Optional[str] = None,
    ) -> None:
        """初始化缓冲队列。

        Args:
            flush_callback: 批量写入回调函数（接收用量记录列表）。
            max_size: 队列最大容量；缺省从配置读取。
            flush_batch_size: 批量写入条数阈值。
            flush_interval_seconds: 批量写入时间间隔（秒）。
            persist_path: 持久化路径；缺省从配置读取。
        """
        settings = get_settings()
        self._max_size = max_size or settings.metering_buffer_queue_max_size
        self._flush_batch_size = flush_batch_size
        self._flush_interval = flush_interval_seconds
        self._persist_path = Path(
            persist_path or settings.metering_buffer_persist_path
        )
        self._flush_callback = flush_callback

        self._buffer: List[TokenUsageRecord] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._total_dropped = 0

        # 从持久化文件恢复
        self._restore()

    def _restore(self) -> None:
        """从持久化文件恢复未写入的用量记录。"""
        persist_file = self._persist_path / "buffer.json"
        if persist_file.exists():
            try:
                data = json.loads(persist_file.read_text(encoding="utf-8"))
                for item in data:
                    self._buffer.append(TokenUsageRecord.model_validate(item))
            except Exception:
                pass

    def _persist(self) -> None:
        """将当前缓冲区持久化至文件。"""
        self._persist_path.mkdir(parents=True, exist_ok=True)
        persist_file = self._persist_path / "buffer.json"
        try:
            data = [r.model_dump(mode="json") for r in self._buffer]
            persist_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    async def enqueue(self, record: TokenUsageRecord) -> bool:
        """异步入队用量记录。

        Returns:
            True（入队成功），False（队列已满，记录被丢弃）。
        """
        async with self._lock:
            if len(self._buffer) >= self._max_size:
                self._total_dropped += 1
                return False
            self._buffer.append(record)
            # 达到批量写入阈值时触发刷新
            if len(self._buffer) >= self._flush_batch_size:
                await self._flush()
            else:
                self._persist()
            return True

    async def flush(self) -> int:
        """手动触发批量写入，返回写入条数。"""
        async with self._lock:
            return await self._flush()

    async def _flush(self) -> int:
        """执行批量写入（调用方需已持有锁）。"""
        if not self._buffer:
            return 0

        records_to_write = list(self._buffer)
        self._buffer.clear()

        if self._flush_callback is not None:
            try:
                result = self._flush_callback(records_to_write)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                # 写入失败：将记录放回缓冲区
                self._buffer.extend(records_to_write)
                self._persist()
                return 0

        # 清理持久化文件
        persist_file = self._persist_path / "buffer.json"
        if persist_file.exists():
            persist_file.unlink()
        return len(records_to_write)

    async def start_periodic_flush(self) -> None:
        """启动定时刷新任务。"""
        if self._flush_task is not None:
            return
        self._flush_task = asyncio.create_task(self._periodic_flush_loop())

    async def stop_periodic_flush(self) -> None:
        """停止定时刷新任务并执行最终刷新。"""
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        await self.flush()

    async def _periodic_flush_loop(self) -> None:
        """定时刷新循环。"""
        while True:
            await asyncio.sleep(self._flush_interval)
            async with self._lock:
                if self._buffer:
                    await self._flush()

    @property
    def size(self) -> int:
        """当前缓冲区大小。"""
        return len(self._buffer)

    @property
    def total_dropped(self) -> int:
        """累计丢弃条数（队列满时）。"""
        return self._total_dropped
