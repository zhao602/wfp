# =============================================================================
# wfp-sdk 包入口（wfp_sdk/__init__.py）
#
# 职责：公开面收敛导出（客户端 + 异常家族 + 数据模型 + vendored 码表）。
# 纪律：零 import orchestration_platform（roadmap B2 验收「不泄漏内部模块」）；
#       公开面变更须同步 docs/sdk-contract.md（§5 版本策略）。
# =============================================================================

from wfp_sdk._version import __version__
from wfp_sdk.client import WorkflowClient
from wfp_sdk.errors import (
    AUTH_DENIED,
    AUTH_TOKEN_EXPIRED,
    AUTH_TOKEN_FORMAT_UNSUPPORTED,
    AUTH_TOKEN_INVALID,
    AUTH_TOKEN_MISSING,
    CLIENT_TIMEOUT,
    INSTANCE_CANCEL_CONFLICT,
    INSTANCE_NOT_FOUND,
    INSTANCE_NOT_WAITING_REVIEW,
    STATE_CHECKPOINT_NOT_FOUND,
    STATE_INSTANCE_RUNNING,
    TRANSPORT_ERROR,
    WF_COMPILATION_TIMEOUT,
    WF_INSTANCE_SUSPENDED,
    WF_NOT_FOUND,
    WF_VALIDATION_FAILED,
    WfpAuthError,
    WfpError,
    WfpNotFoundError,
    WfpStateConflictError,
    WfpTimeoutError,
    WfpTransportError,
    WfpValidationError,
)
from wfp_sdk.models import (
    DefinitionSubmission,
    ExecutionEvent,
    InstanceInfo,
    InstanceResults,
    SourceLocation,
    ValidationIssue,
    ValidationReport,
)

__all__ = [
    "__version__",
    # 客户端
    "WorkflowClient",
    # 异常家族
    "WfpError",
    "WfpValidationError",
    "WfpStateConflictError",
    "WfpNotFoundError",
    "WfpAuthError",
    "WfpTimeoutError",
    "WfpTransportError",
    # 数据模型
    "ValidationReport",
    "ValidationIssue",
    "SourceLocation",
    "DefinitionSubmission",
    "InstanceInfo",
    "ExecutionEvent",
    "InstanceResults",
    # vendored 码表（SDK 可见子集 + 合成码）
    "AUTH_TOKEN_MISSING",
    "AUTH_TOKEN_INVALID",
    "AUTH_TOKEN_EXPIRED",
    "AUTH_TOKEN_FORMAT_UNSUPPORTED",
    "AUTH_DENIED",
    "WF_VALIDATION_FAILED",
    "WF_NOT_FOUND",
    "WF_COMPILATION_TIMEOUT",
    "WF_INSTANCE_SUSPENDED",
    "INSTANCE_NOT_FOUND",
    "INSTANCE_NOT_WAITING_REVIEW",
    "INSTANCE_CANCEL_CONFLICT",
    "STATE_INSTANCE_RUNNING",
    "STATE_CHECKPOINT_NOT_FOUND",
    "TRANSPORT_ERROR",
    "CLIENT_TIMEOUT",
]
