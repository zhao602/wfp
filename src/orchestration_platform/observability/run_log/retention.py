# =============================================================================
# 运行日志过期清理（observability/run_log/retention.py，workflow_run_logging CHG-09）
#
# 职责：扫描 {log_dir} 删除超过保留期的历史日志文件（LogRetentionCleaner）。
# 设计意图（design DC-07、INV-D-12）：
#   1. 按 mtime 判定超期；递归覆盖 {tenant}/{category} 层级；
#   2. 跳过 active_paths（当前活动文件）；
#   3. retention_days=0 表示不清理；异常（占用/PermissionError）跳过 + 告警、不外抛。
# =============================================================================

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Union

logger = logging.getLogger(__name__)


class LogRetentionCleaner:
    """运行日志过期清理器。"""

    def __init__(self, log_dir: Union[str, Path], retention_days: int) -> None:
        self._log_dir = Path(log_dir)
        self._retention_days = retention_days

    def cleanup(
        self,
        now: Optional[datetime] = None,
        active_paths: Optional[Iterable[Union[str, Path]]] = None,
    ) -> int:
        """删除超期文件，返回删除数；任何异常不外抛。"""
        if self._retention_days <= 0:
            return 0
        current = datetime.now() if now is None else now
        cutoff = current - timedelta(days=self._retention_days)
        protected = set()
        for path in (active_paths or []):
            try:
                protected.add(str(Path(path).resolve()))
            except OSError:
                continue
        deleted = 0
        try:
            candidates = list(self._log_dir.rglob("*"))
        except OSError:
            logger.warning("运行日志清理扫描失败：%s", self._log_dir, exc_info=True)
            return 0
        for path in candidates:
            try:
                if not path.is_file():
                    continue
                if str(path.resolve()) in protected:
                    continue
                mtime = datetime.fromtimestamp(path.stat().st_mtime)
                if mtime < cutoff:
                    path.unlink()
                    deleted += 1
            except OSError:
                logger.warning("运行日志清理跳过文件：%s", path, exc_info=True)
        return deleted
