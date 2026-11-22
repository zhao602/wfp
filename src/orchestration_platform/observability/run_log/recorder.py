# =============================================================================
# 运行日志采集门面（observability/run_log/recorder.py，workflow_run_logging CHG-11）
#
# 职责：一次工作流运行的节点级/工作流级记录编排（WorkflowRunLogger）。
# 设计意图（design 接口组 R、DC-09、时序 :510/:536/:538、E-9 / INV-01、INV-02、INV-03、INV-D-01、INV-D-13）：
#   1. 编排顺序固定：组装 → 脱敏 → 截断 → RunLogSink.emit（脱敏先于截断，INV-03）；
#   2. 级别映射（DC-09 / design :510）：开始与成功 INFO；节点失败/超时（record_node_failure）
#      与工作流失败 ERROR；未知终态 WARN；
#   3. 每个公开方法整体吞异常、返回 None、绝不抛出（红线，INV-01）；
#   4. tenant_id 缺失以 log_tenant_default 兜底；business_category 缺失兜底 default；
#   5. E-9：运行中记录（begin_run/begin_node）不伪造 finished_at/duration_ms。
# =============================================================================

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from orchestration_platform.configuration.settings import Settings
from orchestration_platform.observability.run_log.fields import (
    BUSINESS_CATEGORY_DEFAULT,
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_WARN,
    RECORD_TYPE_NODE_RUN,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    STATUS_TIMEOUT,
)
from orchestration_platform.observability.run_log.models import (
    NodeRunLogRecord,
    RunLogContext,
    WorkflowRunLogRecord,
)
from orchestration_platform.observability.run_log.redactor import RunLogRedactor
from orchestration_platform.observability.run_log.sink import RunLogSink
from orchestration_platform.observability.run_log.truncation import Truncator

logger = logging.getLogger(__name__)

_FALLBACK_ERROR_CODE = "WF_NODE_EXECUTION_FAILED"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso(value: Any) -> str:
    """datetime → ISO8601；字符串透传；空值回退当前时间。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return _now_iso()


class WorkflowRunLogger:
    """运行日志采集门面：一次运行一份上下文，任何公开方法绝不抛出（INV-01 红线）。"""

    def __init__(
        self,
        run_id: str,
        trace_id: str,
        tenant_id: Optional[str] = None,
        business_category: Optional[str] = None,
        workflow_id: Optional[str] = None,
        definition_version: Optional[int] = None,
        settings: Optional[Settings] = None,
        sink: Optional[RunLogSink] = None,
    ) -> None:
        if settings is None:
            from orchestration_platform.configuration.settings import get_settings

            settings = get_settings()
        self._settings = settings
        self._context = RunLogContext(
            run_id=run_id,
            trace_id=trace_id,
            tenant_id=tenant_id or settings.log_tenant_default,
            business_category=business_category or BUSINESS_CATEGORY_DEFAULT,
            workflow_id=workflow_id,
            definition_version=definition_version,
        )
        self._owns_sink = sink is None
        self._sink: Any = sink if sink is not None else RunLogSink(settings)
        self._redactor = RunLogRedactor(settings)
        self._truncator = Truncator()
        self._max_payload_bytes = settings.log_max_payload_bytes
        self._run_started_at: Optional[str] = None
        self._current_node: Optional[Dict[str, Any]] = None
        # 节点级计数（与门面已采集的节点级记录同源，供 end_run 组装 node_summary；
        # 2.0.1（3）"须与节点级日志计数自洽"）
        self._node_counts: Dict[str, int] = {
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "timeout": 0,
        }
        self._closed = False

    # ------------------------------------------------------------------
    # 工作流级
    # ------------------------------------------------------------------
    def begin_run(self, started_at: Optional[datetime] = None) -> None:
        """工作流级开始记录（running；不伪造结束字段，E-9）。"""
        try:
            started = _to_iso(started_at)
            self._run_started_at = started
            record = WorkflowRunLogRecord(
                run_id=self._context.run_id,
                trace_id=self._context.trace_id,
                tenant_id=self._context.tenant_id,
                business_category=self._context.business_category,
                level=LEVEL_INFO,
                timestamp=_now_iso(),
                status=STATUS_RUNNING,
                started_at=started,
                workflow_id=self._context.workflow_id or "",
                definition_version=self._context.definition_version,
            )
            self._emit_record(record.to_dict())
        except Exception:
            logger.warning("运行日志 begin_run 失败（已吞掉，不影响执行）", exc_info=True)

    def end_run(
        self,
        status: str,
        finished_at: Optional[datetime] = None,
        duration_ms: Optional[int] = None,
        node_summary: Optional[Dict[str, int]] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> None:
        """工作流级终态记录：succeeded→INFO；failed→ERROR；其他→WARN（DC-09）。"""
        try:
            if status == STATUS_FAILED:
                level = LEVEL_ERROR
            elif status == STATUS_SUCCEEDED:
                level = LEVEL_INFO
            else:
                level = LEVEL_WARN
            record = WorkflowRunLogRecord(
                run_id=self._context.run_id,
                trace_id=self._context.trace_id,
                tenant_id=self._context.tenant_id,
                business_category=self._context.business_category,
                level=level,
                timestamp=_now_iso(),
                status=status,
                started_at=self._run_started_at or _now_iso(),
                finished_at=_to_iso(finished_at),
                duration_ms=duration_ms,
                workflow_id=self._context.workflow_id or "",
                definition_version=self._context.definition_version,
                node_summary=node_summary,
            )
            payload = record.to_dict()
            if error:
                # design 时序 :538 失败路径传参：error 以独立键落盘（2.0.1（3）未列该字段，DR 记偏差）
                payload["error"] = error
            self._emit_record(payload)
        except Exception:
            logger.warning("运行日志 end_run 失败（已吞掉，不影响执行）", exc_info=True)

    # ------------------------------------------------------------------
    # 节点级
    # ------------------------------------------------------------------
    def begin_node(
        self,
        node_id: str,
        node_type: str,
        round_index: int,
        input_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        """节点级开始记录（running；登记当前节点身份供 end_node 复用，E-9）。"""
        try:
            self._current_node = {
                "node_id": str(node_id),
                "node_type": str(node_type),
                "round_index": int(round_index),
            }
            self._node_counts["total"] += 1
            record = NodeRunLogRecord(
                run_id=self._context.run_id,
                trace_id=self._context.trace_id,
                tenant_id=self._context.tenant_id,
                business_category=self._context.business_category,
                level=LEVEL_INFO,
                timestamp=_now_iso(),
                status=STATUS_RUNNING,
                started_at=_now_iso(),
                node_id=str(node_id),
                node_type=str(node_type),
                round_index=int(round_index),
                input=input_snapshot if input_snapshot is not None else {},
            )
            self._emit_record(record.to_dict())
        except Exception:
            logger.warning("运行日志 begin_node 失败（已吞掉，不影响执行）", exc_info=True)

    def end_node(
        self,
        result: Any,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        input_snapshot: Optional[Dict[str, Any]] = None,
        subworkflow_depth: int = 0,
    ) -> None:
        """节点级成功记录（design :624）；复用 begin_node 登记的节点身份。"""
        try:
            success = bool(getattr(result, "success", False))
            self._node_counts["succeeded" if success else "failed"] += 1
            node = self._current_node or {}
            error_dict: Optional[Dict[str, Any]] = None
            if not success:
                error_dict = {
                    "error_type": "NodeExecutionError",
                    "error_code": getattr(result, "error_code", None) or _FALLBACK_ERROR_CODE,
                    "error_message": getattr(result, "error_message", None) or "",
                }
            record = NodeRunLogRecord(
                run_id=self._context.run_id,
                trace_id=self._context.trace_id,
                tenant_id=self._context.tenant_id,
                business_category=self._context.business_category,
                level=LEVEL_INFO if success else LEVEL_ERROR,
                timestamp=_now_iso(),
                status=STATUS_SUCCEEDED if success else STATUS_FAILED,
                started_at=_to_iso(started_at),
                finished_at=_to_iso(finished_at),
                duration_ms=getattr(result, "duration_ms", None),
                node_id=str(node.get("node_id", "unknown")),
                node_type=str(node.get("node_type", "unknown")),
                round_index=int(node.get("round_index", 0)),
                input=input_snapshot if input_snapshot is not None else {},
                output=getattr(result, "output_value", None),
                error=error_dict,
                subworkflow_depth=subworkflow_depth,
            )
            self._emit_record(record.to_dict())
        except Exception:
            logger.warning("运行日志 end_node 失败（已吞掉，不影响执行）", exc_info=True)

    def record_node_failure(
        self,
        node_id: str,
        node_type: str,
        round_index: int,
        input_snapshot: Optional[Dict[str, Any]] = None,
        error_code: str = "",
        error_message: str = "",
        status: str = STATUS_FAILED,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        duration_ms: Optional[int] = None,
        error_type: Optional[str] = None,
        error_stack: Optional[str] = None,
    ) -> None:
        """节点失败/超时记录：failed/timeout 均 level=ERROR（design :510）。"""
        try:
            resolved_error_type = error_type or (
                "TimeoutError" if status == STATUS_TIMEOUT else "NodeExecutionError"
            )
            self._node_counts["timeout" if status == STATUS_TIMEOUT else "failed"] += 1
            if (
                duration_ms is None
                and isinstance(started_at, datetime)
                and isinstance(finished_at, datetime)
            ):
                duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
            error_dict: Dict[str, Any] = {
                "error_type": resolved_error_type,
                "error_code": error_code,
                "error_message": error_message or "",
            }
            if error_stack:
                error_dict["error_stack"] = error_stack
            record = NodeRunLogRecord(
                run_id=self._context.run_id,
                trace_id=self._context.trace_id,
                tenant_id=self._context.tenant_id,
                business_category=self._context.business_category,
                level=LEVEL_ERROR,
                timestamp=_now_iso(),
                status=status,
                started_at=_to_iso(started_at),
                finished_at=_to_iso(finished_at),
                duration_ms=duration_ms,
                node_id=str(node_id),
                node_type=str(node_type),
                round_index=int(round_index),
                input=input_snapshot if input_snapshot is not None else {},
                error=error_dict,
            )
            self._emit_record(record.to_dict())
        except Exception:
            logger.warning("运行日志 record_node_failure 失败（已吞掉，不影响执行）", exc_info=True)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def close(self) -> None:
        """冲刷落盘通道；自建 sink 时一并停止后台线程（幂等）。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._sink.flush()
            if self._owns_sink:
                self._sink.close()
        except Exception:
            logger.warning("运行日志 close 失败（已吞掉，不影响执行）", exc_info=True)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    @property
    def node_summary(self) -> Dict[str, int]:
        """节点级计数汇总（total/succeeded/failed/timeout，与节点级记录同源）。"""
        return dict(self._node_counts)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _emit_record(self, payload: Dict[str, Any]) -> None:
        """组装后统一走：脱敏 → 截断 → 落盘（顺序固定，INV-03）。"""
        redacted = self._redactor.redact(payload)
        if not isinstance(redacted, dict):
            # 脱敏整体失败：仅保留门面自生成的安全检索字段（不泄漏原载荷，INV-02）
            redacted = {
                "schema_version": payload.get("schema_version", "1.0"),
                "record_type": payload.get("record_type", RECORD_TYPE_NODE_RUN),
                "run_id": self._context.run_id,
                "trace_id": self._context.trace_id,
                "tenant_id": self._context.tenant_id,
                "business_category": self._context.business_category,
                "level": payload.get("level", LEVEL_INFO),
                "timestamp": _now_iso(),
                "status": payload.get("status", "unknown"),
                "started_at": payload.get("started_at", _now_iso()),
                "payload_note": "脱敏整体失败，载荷已占位",
            }
        self._truncate_payload(redacted)
        self._sink.emit(redacted)

    def _truncate_payload(self, payload: Dict[str, Any]) -> None:
        """单字段截断（input/output/error_message/error_stack）+ truncated/payload_note（仅节点级）。"""
        is_node = payload.get("record_type") == RECORD_TYPE_NODE_RUN
        notes: List[str] = []
        truncated = False
        if is_node:
            for key in ("input", "output"):
                value = payload.get(key)
                if value is None:
                    continue
                new_value, was_truncated = self._truncator.truncate_field(
                    value, self._max_payload_bytes
                )
                if was_truncated:
                    payload[key] = new_value
                    truncated = True
                    notes.append(f"{key} 超 log_max_payload_bytes 截断")
        error = payload.get("error")
        if isinstance(error, dict):
            # 复制后再改，防与调用方共享（不修改入参）
            error = dict(error)
            payload["error"] = error
            for key in ("error_message", "error_stack"):
                text = error.get(key)
                if not isinstance(text, str) or not text:
                    continue
                new_text, was_truncated = self._truncator.truncate_field(
                    text, self._max_payload_bytes
                )
                if was_truncated:
                    error[key] = new_text
                    truncated = True
                    notes.append(f"error.{key} 超 log_max_payload_bytes 截断")
        if self._redactor.payload_note:
            notes.append(self._redactor.payload_note)
        if is_node:
            if truncated:
                payload["truncated"] = True
            if notes:
                payload["payload_note"] = "；".join(notes)
