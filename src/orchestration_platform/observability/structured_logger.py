# 已由 workflow_run_logging 接线为生产调用点（log_record，design DC-01），不再作为 cleanup_dead_code 清理候选（原 B15 待清理定位解除）
# =============================================================================
# 结构化日志（observability/structured_logger.py）
#
# 职责：结构化日志，贯穿统一 trace_id，含实例/节点/轮次/耗时/结果。
# 设计意图（对应 spec 4.4.1、4.4.2）：
#   日志包含实例标识、节点标识、执行轮次、耗时、结果状态。
# =============================================================================

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional


class StructuredLogger:
    """结构化日志器。"""

    def __init__(self, name: str = "orchestration_platform") -> None:
        self._logger = logging.getLogger(name)

    def log(
        self,
        level: str,
        message: str,
        trace_id: Optional[str] = None,
        instance_id: Optional[str] = None,
        node_id: Optional[str] = None,
        round_index: Optional[int] = None,
        duration_ms: Optional[int] = None,
        **extra: Any,
    ) -> None:
        """输出结构化日志。"""
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": message,
        }
        if trace_id:
            record["trace_id"] = trace_id
        if instance_id:
            record["instance_id"] = instance_id
        if node_id:
            record["node_id"] = node_id
        if round_index is not None:
            record["round_index"] = round_index
        if duration_ms is not None:
            record["duration_ms"] = duration_ms
        record.update(extra)

        log_line = json.dumps(record, ensure_ascii=False, default=str)
        getattr(self._logger, level.lower(), self._logger.info)(log_line)

    def log_record(self, record: Mapping[str, Any]) -> None:
        """逐行输出结构化记录（workflow_run_logging 接线；不做字段裁剪，DC-01）。"""
        log_line = json.dumps(record, ensure_ascii=False, default=str)
        self._logger.info(log_line)
