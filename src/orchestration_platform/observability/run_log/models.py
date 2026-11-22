# =============================================================================
# 运行日志记录数据类（observability/run_log/models.py，workflow_run_logging CHG-03）
#
# 职责：定义独立于领域模型的运行日志记录载体
#       （RunLogContext / NodeRunLogRecord / WorkflowRunLogRecord）及 to_dict() 序列化。
# 设计意图（design 2.0.1 统一字段规范）：
#   1. 记录类为纯数据载体，不进入 domain/models/（INV-04、INV-D-09）；
#   2. to_dict() 键序：公共字段（design 2.0.1（1））在前、族字段在后；
#   3. 非必填且为 None 的键不写入（不发 null 键）。
# =============================================================================

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Optional

from orchestration_platform.observability.run_log import fields


@dataclass(frozen=True)
class RunLogContext:
    """运行日志上下文（一次工作流执行内共享）。"""

    run_id: str
    trace_id: str
    tenant_id: str
    business_category: str
    workflow_id: Optional[str] = None
    definition_version: Optional[int] = None


@dataclass
class NodeRunLogRecord:
    """节点级运行日志记录（record_type="node_run"）。"""

    _DICT_FIELDS: ClassVar[tuple] = fields.COMMON_FIELDS + fields.NODE_RUN_FIELDS

    # 公共字段（design 2.0.1（1））
    run_id: str
    trace_id: str
    tenant_id: str
    business_category: str
    level: str
    timestamp: str
    status: str
    started_at: str
    # 节点级字段（design 2.0.1（2））
    node_id: str
    node_type: str
    round_index: int
    input: Any
    # 固定默认与可选字段（None 时 to_dict() 不写键）
    schema_version: str = fields.SCHEMA_VERSION
    record_type: str = fields.RECORD_TYPE_NODE_RUN
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    output: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None
    truncated: Optional[bool] = None
    payload_note: Optional[str] = None
    subworkflow_depth: int = 0

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for name in self._DICT_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            result[name] = value
        return result


@dataclass
class WorkflowRunLogRecord:
    """工作流级运行日志记录（record_type="workflow_run"）。"""

    _DICT_FIELDS: ClassVar[tuple] = fields.COMMON_FIELDS + fields.WORKFLOW_RUN_FIELDS

    # 公共字段（design 2.0.1（1））
    run_id: str
    trace_id: str
    tenant_id: str
    business_category: str
    level: str
    timestamp: str
    status: str
    started_at: str
    # 工作流级字段（design 2.0.1（3））
    workflow_id: str
    # 固定默认与可选字段（None 时 to_dict() 不写键）
    schema_version: str = fields.SCHEMA_VERSION
    record_type: str = fields.RECORD_TYPE_WORKFLOW_RUN
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    definition_version: Optional[int] = None
    trigger_type: Optional[str] = None
    node_summary: Optional[Dict[str, int]] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for name in self._DICT_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            result[name] = value
        return result
