# =============================================================================
# RBAC 鉴权中间件（api/middleware/rbac_authorization_middleware.py）
#
# 职责：在 FastAPI 请求处理链中注入 RBAC 鉴权与租户隔离校验。
# 设计意图（对应 spec 5.1、design 2.5.6、任务 T56.4）：
#   1. 在既有 authentication.py 之后执行；
#   2. 从 Authorization 头提取 Bearer 令牌，构造 GovernanceRequest；
#   3. 调用 InterceptorChain.execute_before 执行鉴权与隔离校验；
#   4. 未鉴权返回 401，鉴权通过后将租户与用户上下文注入 request.state；
#   5. 未启用治理时中间件自动放行（降级模式）；
#   6. 既有路由签名不变。
# =============================================================================

import uuid
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import OperationType, ResourceType
from orchestration_platform.governance.interceptors.base import (
    GovernanceRequest,
    InterceptorChain,
)


# 不需要鉴权的路径前缀
# /api/v1/auth/token：JWT 签发端点（arch_review_v2_tasks G2/CHG-13）——
# 部署侧信任边界内调用（design §4.2 前置条件），不承担自助注册；
# 治理链开启时须可达（否则生产组合 AUTH_TOKEN_ISSUANCE_ENABLED=true 无法签发）。
_PUBLIC_PATHS = frozenset({
    "/healthz",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/v1/auth/token",
})


# HTTP 方法到操作类型的映射
_METHOD_TO_OPERATION = {
    "GET": OperationType.READ,
    "POST": OperationType.CREATE,
    "PUT": OperationType.UPDATE,
    "PATCH": OperationType.UPDATE,
    "DELETE": OperationType.DELETE,
}


# 路径前缀到资源类型的映射
def _infer_resource_type(path: str) -> ResourceType:
    """从请求路径推断资源类型。"""
    if "/workflows" in path:
        return ResourceType.WORKFLOW_DEFINITION
    if "/instances" in path:
        return ResourceType.EXECUTION_INSTANCE
    if "/mcp" in path or "/tools" in path:
        return ResourceType.MCP_TOOL
    if "/datasources" in path:
        return ResourceType.DATA_SOURCE_CONNECTOR
    if "/tenants" in path:
        return ResourceType.TENANT_CONFIG
    if "/audit" in path:
        return ResourceType.AUDIT_LOG
    return ResourceType.WORKFLOW_DEFINITION


class RbacAuthorizationMiddleware(BaseHTTPMiddleware):
    """RBAC 鉴权中间件。

    在 FastAPI 请求处理链中执行 RBAC 鉴权与租户隔离校验。
    未启用治理时自动放行（降级模式）。
    """

    def __init__(
        self,
        app: ASGIApp,
        interceptor_chain: Optional[InterceptorChain] = None,
    ) -> None:
        """初始化鉴权中间件。

        Args:
            app: ASGI 应用。
            interceptor_chain: 拦截器链实例；None 时降级放行。
        """
        super().__init__(app)
        self._chain = interceptor_chain
        self._settings = get_settings()

    async def dispatch(
        self, request: Request, call_next
    ) -> Response:
        """处理请求：鉴权 → 放行或拒绝。"""
        # 公开路径直接放行
        if any(request.url.path.startswith(p) for p in _PUBLIC_PATHS):
            return await call_next(request)

        # 降级模式：拦截器链未配置或全部未启用时放行
        if self._chain is None or self._chain.is_degraded_mode:
            return await call_next(request)

        # 提取 Bearer 令牌
        token = self._extract_token(request)
        if token is None:
            return JSONResponse(
                status_code=401,
                content={
                    "code": "AUTH_TOKEN_MISSING",
                    "message": "未提供访问令牌",
                    "details": {},
                },
            )

        # 构造治理请求
        operation = _METHOD_TO_OPERATION.get(
            request.method, OperationType.READ
        )
        resource_type = _infer_resource_type(request.url.path)

        governance_request = GovernanceRequest(
            token=token,
            resource_type=resource_type,
            operation=operation,
            trace_id=request.headers.get("X-Trace-ID", uuid.uuid4().hex),
            payload={"path": request.url.path, "method": request.method},
        )

        # 执行前置拦截器链
        decision = await self._chain.execute_before(governance_request)

        if not decision.allowed:
            status_code = 403 if "权限不足" in (decision.deny_reason or "") else 401
            return JSONResponse(
                status_code=status_code,
                content={
                    "code": "AUTH_DENIED",
                    "message": decision.deny_reason or "鉴权失败",
                    "details": {
                        "resource_type": resource_type.value,
                        "operation": operation.value,
                    },
                },
            )

        # 鉴权通过：注入租户与用户上下文至 request.state
        request.state.tenant_id = governance_request.tenant_id
        request.state.user_id = governance_request.user_id
        request.state.governance_decision = decision

        # 执行后续处理
        response = await call_next(request)

        # 执行后置拦截器链（租户隔离校验等）
        try:
            from orchestration_platform.governance.interceptors.base import (
                GovernanceResult,
            )
            result = GovernanceResult()
            await self._chain.execute_after(
                governance_request, decision, result
            )
        except Exception:
            pass

        return response

    @staticmethod
    def _extract_token(request: Request) -> Optional[str]:
        """从 Authorization 头提取 Bearer 令牌。

        Returns:
            令牌字符串（有效时），None（缺失或格式错误时）。
        """
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None
        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1].strip()
