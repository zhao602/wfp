# =============================================================================
# wfp-sdk 错误体系（wfp_sdk/errors.py）
#
# 职责：与平台错误码同构（docs/sdk-contract.md §2）——vendored 码表子集 +
#       WfpError 异常层级 + 未知码透传 + SDK 本地合成码。
# 纪律：
#   1. vendored 码值与 orchestration_platform.domain.errors 逐字一致
#      （AUTH_TOKEN_MISSING/AUTH_DENIED 为中间件 wire 值，平台无同名常量）；
#   2. 未知响应码 → WfpError(code=原样字符串) 透传，不丢信息；
#   3. 合成码仅 TRANSPORT_ERROR/CLIENT_TIMEOUT 两条，仅 SDK 本地产生，
#      平台无 TRANSPORT_*/CLIENT_* 前缀码，永不冲突。
# =============================================================================

from typing import Any, Dict, Optional

# =============================================================================
# vendored 平台错误码（SDK 可见子集）
# =============================================================================

# --- 认证（AUTH_*）---
AUTH_TOKEN_MISSING = "AUTH_TOKEN_MISSING"                      # 未提供访问令牌（RBAC 中间件 wire 值）
AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"                      # JWT 结构/签名/claims 校验失败
AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"                      # 令牌已过期
AUTH_TOKEN_FORMAT_UNSUPPORTED = "AUTH_TOKEN_FORMAT_UNSUPPORTED"  # 令牌形态与校验模式不匹配
AUTH_DENIED = "AUTH_DENIED"                                    # 鉴权/授权拒绝（RBAC 中间件 wire 值）

# --- 工作流（WF_*）---
WF_VALIDATION_FAILED = "WF_VALIDATION_FAILED"                  # 校验未通过（聚合报告，details.issues 同构）
WF_NOT_FOUND = "WF_NOT_FOUND"                                  # 工作流定义不存在
WF_COMPILATION_TIMEOUT = "WF_COMPILATION_TIMEOUT"              # 大图编译超时
WF_INSTANCE_SUSPENDED = "WF_INSTANCE_SUSPENDED"                # 挂起信号标识码

# --- 实例（INSTANCE_*）---
INSTANCE_NOT_FOUND = "INSTANCE_NOT_FOUND"                      # 实例不存在
INSTANCE_NOT_WAITING_REVIEW = "INSTANCE_NOT_WAITING_REVIEW"    # review 决策时实例非等待审核
INSTANCE_CANCEL_CONFLICT = "INSTANCE_CANCEL_CONFLICT"          # 取消被拒：已终态或无可取消任务（B2）

# --- 状态（STATE_*）---
STATE_INSTANCE_RUNNING = "STATE_INSTANCE_RUNNING"              # 运行中实例禁止回滚/重放
STATE_CHECKPOINT_NOT_FOUND = "STATE_CHECKPOINT_NOT_FOUND"      # 目标检查点不存在

# --- SDK 本地合成码（永不与平台码冲突）---
TRANSPORT_ERROR = "TRANSPORT_ERROR"                            # 网络异常/连接失败/请求超时
CLIENT_TIMEOUT = "CLIENT_TIMEOUT"                              # wait_for_completion 轮询超时


# =============================================================================
# 异常层级
# =============================================================================


class WfpError(Exception):
    """SDK 异常基类：与平台错误体 {code, message, details} 同构。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        http_status: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Dict[str, Any] = details or {}
        self.http_status = http_status

    def to_dict(self) -> Dict[str, Any]:
        """序列化为平台统一错误结构。"""
        return {"code": self.code, "message": self.message, "details": self.details}


class WfpValidationError(WfpError):
    """工作流定义校验未通过（HTTP 422；details.issues 携同构问题清单）。"""


class WfpStateConflictError(WfpError):
    """实例状态冲突（HTTP 409；如 INSTANCE_CANCEL_CONFLICT）。"""


class WfpNotFoundError(WfpError):
    """资源未找到（HTTP 404）。"""


class WfpAuthError(WfpError):
    """鉴权/授权失败（HTTP 401/403）。"""


class WfpTimeoutError(WfpError):
    """客户端本地等待超时（合成码 CLIENT_TIMEOUT，不发 HTTP）。"""


class WfpTransportError(WfpError):
    """网络传输异常（合成码 TRANSPORT_ERROR，不发 HTTP）。"""


# HTTP 状态 → 异常类映射（§2.3）
_STATUS_TO_ERROR: Dict[int, type] = {
    422: WfpValidationError,
    409: WfpStateConflictError,
    404: WfpNotFoundError,
    401: WfpAuthError,
    403: WfpAuthError,
}


def error_from_response(status_code: int, body: Any) -> WfpError:
    """由非 2xx 响应构造同构异常（wire 三形态兼容解析，§2.2）。

    解析顺序：顶层 code → detail.code → detail 字符串 → 兜底合成码。
    未知码原样透传（isomorphic passthrough）。
    """
    code = ""
    message = ""
    details: Dict[str, Any] = {}

    if isinstance(body, dict):
        if isinstance(body.get("code"), str):
            # W3 顶层对象（全局异常处理器 / 中间件直出）
            code = body["code"]
            message = str(body.get("message", ""))
            if isinstance(body.get("details"), dict):
                details = body["details"]
        else:
            detail = body.get("detail")
            if isinstance(detail, dict):
                # W2 detail 内嵌对象（DomainError.to_dict() 经 HTTPException）
                code = str(detail.get("code", ""))
                message = str(detail.get("message", ""))
                if isinstance(detail.get("details"), dict):
                    details = detail["details"]
                elif detail.get("issues") is not None:
                    # POST /workflows 422 变体：detail 即报告体
                    message = message or str(detail.get("message", ""))
                    details = {"issues": detail.get("issues")}
            elif isinstance(detail, str):
                # W1 裸 detail 字符串
                message = detail

    if not code:
        # 码缺失兜底（按状态语义合成，保持可编程分支）
        code = {
            404: INSTANCE_NOT_FOUND,
            401: AUTH_TOKEN_MISSING,
            403: AUTH_DENIED,
        }.get(status_code, f"HTTP_{status_code}")

    error_cls = _STATUS_TO_ERROR.get(status_code, WfpError)
    return error_cls(
        code=code,
        message=message or f"HTTP {status_code}",
        details=details,
        http_status=status_code,
    )
