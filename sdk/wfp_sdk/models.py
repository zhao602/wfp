# =============================================================================
# wfp-sdk 数据模型（wfp_sdk/models.py）
#
# 职责：镜像平台 REST 响应形态（docs/sdk-contract.md §1/§4），stdlib dataclass
#       建模（零 pydantic 依赖），from_dict 宽容解析（未知键忽略、缺键落默认）。
# 纪律：字段名与平台路由响应逐字一致（snake_case）；平台响应只增字段时
#       旧版 SDK 解析不破（宽容解析契约）。
# =============================================================================

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# =============================================================================
# 校验域（§4 YAML 契约）
# =============================================================================


@dataclass
class SourceLocation:
    """YAML 源位置（行列 1 起算）。"""

    line: int = 0
    column: int = 0

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "SourceLocation":
        if not isinstance(data, dict):
            return cls()
        return cls(
            line=int(data.get("line", 0) or 0),
            column=int(data.get("column", 0) or 0),
        )


@dataclass
class ValidationIssue:
    """单条校验问题（镜像 domain/models/common.py ValidationIssue）。"""

    code: str = ""
    severity: str = "error"
    message: str = ""
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    location: Optional[SourceLocation] = None
    field: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ValidationIssue":
        return cls(
            code=str(data.get("code", "")),
            severity=str(data.get("severity", "error")),
            message=str(data.get("message", "")),
            node_id=data.get("node_id"),
            edge_id=data.get("edge_id"),
            location=SourceLocation.from_dict(data.get("location")),
            field=data.get("field"),
        )


@dataclass
class ValidationReport:
    """校验报告（POST /workflows/validate 响应，恒 HTTP 200）。"""

    valid: bool = False
    issues: List[ValidationIssue] = field(default_factory=list)
    definition_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ValidationReport":
        raw_issues = data.get("issues") or []
        return cls(
            valid=bool(data.get("valid", False)),
            issues=[ValidationIssue.from_dict(i) for i in raw_issues if isinstance(i, dict)],
            definition_id=data.get("definition_id"),
        )


@dataclass
class DefinitionSubmission:
    """定义提交回执（POST /workflows 成功响应）。"""

    definition_id: str = ""
    definition_version: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DefinitionSubmission":
        version = data.get("definition_version")
        return cls(
            definition_id=str(data.get("definition_id", "")),
            definition_version=int(version) if isinstance(version, int) else None,
        )


# =============================================================================
# 实例域（§1 端点契约）
# =============================================================================


@dataclass
class InstanceInfo:
    """实例状态快照（start/cancel 响应与 get_instance 同构）。"""

    instance_id: str = ""
    definition_id: Optional[str] = None
    status: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InstanceInfo":
        return cls(
            instance_id=str(data.get("instance_id", "")),
            definition_id=data.get("definition_id"),
            status=str(data.get("status", "")),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )


@dataclass
class ExecutionEvent:
    """执行事件（SSE 帧与轮询响应共用形态）。"""

    event_id: str = ""
    instance_id: str = ""
    event_type: str = ""
    node_id: Optional[str] = None
    timestamp: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionEvent":
        payload = data.get("payload")
        return cls(
            event_id=str(data.get("event_id", "")),
            instance_id=str(data.get("instance_id", "")),
            event_type=str(data.get("event_type", "")),
            node_id=data.get("node_id"),
            timestamp=data.get("timestamp"),
            payload=dict(payload) if isinstance(payload, dict) else {},
        )


@dataclass
class InstanceResults:
    """实例执行结果（GET /instances/{id}/results 响应）。"""

    instance_id: str = ""
    status: str = ""
    outputs: Optional[Dict[str, Any]] = None
    finished_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InstanceResults":
        outputs = data.get("outputs")
        return cls(
            instance_id=str(data.get("instance_id", "")),
            status=str(data.get("status", "")),
            outputs=dict(outputs) if isinstance(outputs, dict) else outputs,
            finished_at=data.get("finished_at"),
        )
