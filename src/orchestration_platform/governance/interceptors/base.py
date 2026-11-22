# =============================================================================
# 治理拦截器框架（governance/interceptors/base.py）
#
# 职责：定义治理拦截器抽象接口、请求/决策模型与拦截器链编排。
# 设计意图（对应 spec 5.1~5.4、design 2.2）：
#   1. 所有治理能力通过 IGovernanceInterceptor 拦截器链注入，不修改既有接口签名；
#   2. 拦截器执行顺序：鉴权 → 租户隔离 → 审计(前) → 配额 → 预算 → 全局调度 → 引擎执行 → 计量采集 → 审计(后)；
#   3. 未启用治理模块时所有拦截器自动放行（降级模式）；
#   4. fail-closed 守卫：拦截器异常时拒绝请求而非放行。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import (
    OperationType,
    ResourceType,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# 请求/决策/结果模型
# =============================================================================

class GovernanceRequest(BaseModel):
    """治理请求：贯穿拦截器链的请求上下文。

    包含令牌、资源类型、操作类型、租户标识、用户标识等。
    """

    token: Optional[str] = Field(default=None, description="访问令牌")
    resource_type: ResourceType = Field(description="目标资源类型")
    operation: OperationType = Field(description="操作类型")
    tenant_id: Optional[str] = Field(default=None, description="租户标识（鉴权后填充）")
    user_id: Optional[str] = Field(default=None, description="用户标识（鉴权后填充）")
    instance_id: Optional[str] = Field(default=None, description="实例标识")
    trace_id: str = Field(description="追踪标识")
    payload: Dict[str, Any] = Field(
        default_factory=dict, description="请求载荷"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="元数据（拦截器间传递）"
    )


class GovernanceDecision(BaseModel):
    """治理决策：拦截器对请求的判定结果。

    允许时放行，拒绝时附带拒绝原因。
    """

    allowed: bool = Field(default=True, description="是否允许")
    deny_reason: Optional[str] = Field(default=None, description="拒绝原因")
    tenant_context: Optional[str] = Field(
        default=None, description="租户上下文（注入后填充）"
    )
    resource_scope_filter: Optional[Dict[str, Any]] = Field(
        default=None, description="资源范围过滤条件"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="决策元数据"
    )


class GovernanceResult(BaseModel):
    """治理结果：引擎执行后的结果与审计摘要。

    供后置拦截器（计量、审计后）使用。
    """

    execution_result: Optional[Any] = Field(default=None, description="引擎执行结果")
    audit_summary: Optional[Dict[str, Any]] = Field(
        default=None, description="审计摘要"
    )
    token_usage: Optional[Dict[str, int]] = Field(
        default=None, description="Token 用量摘要"
    )


# =============================================================================
# 拦截器抽象接口
# =============================================================================

@runtime_checkable
class IGovernanceInterceptor(Protocol):
    """治理拦截器抽象接口。

    所有治理拦截器实现此接口，通过拦截器链编排执行。
    intercept_before 在引擎执行前调用，intercept_after 在执行后调用。
    """

    async def intercept_before(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
    ) -> GovernanceDecision:
        """前置拦截：在引擎执行前校验与注入。

        Args:
            request: 治理请求上下文。
            decision: 当前决策状态（可被前序拦截器修改）。

        Returns:
            更新后的决策。allowed=False 时拦截器链立即终止。
        """
        ...

    async def intercept_after(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
        result: GovernanceResult,
    ) -> GovernanceResult:
        """后置拦截：在引擎执行后采集与记录。

        Args:
            request: 治理请求上下文。
            decision: 最终决策状态。
            result: 执行结果。

        Returns:
            更新后的结果。
        """
        ...


# =============================================================================
# 七类拦截器接口
# =============================================================================

@runtime_checkable
class IAuthInterceptor(IGovernanceInterceptor, Protocol):
    """鉴权拦截器：校验令牌与权限。"""

    async def authenticate(self, request: GovernanceRequest) -> GovernanceDecision:
        """校验访问令牌，填充用户标识与租户标识。"""
        ...

    async def authorize(self, request: GovernanceRequest) -> GovernanceDecision:
        """校验用户对目标资源的操作权限。"""
        ...


@runtime_checkable
class ITenantIsolationInterceptor(IGovernanceInterceptor, Protocol):
    """租户隔离拦截器：注入租户上下文与校验数据隔离。"""

    async def inject_tenant_context(
        self, request: GovernanceRequest
    ) -> GovernanceDecision:
        """注入租户上下文至请求。"""
        ...

    async def verify_isolation(
        self, request: GovernanceRequest
    ) -> GovernanceDecision:
        """校验请求不跨租户访问数据。"""
        ...


@runtime_checkable
class IQuotaInterceptor(IGovernanceInterceptor, Protocol):
    """配额拦截器：校验租户资源配额。"""

    async def check_quota(self, request: GovernanceRequest) -> GovernanceDecision:
        """校验租户资源使用是否超出配额上限。"""
        ...


@runtime_checkable
class IBudgetInterceptor(IGovernanceInterceptor, Protocol):
    """预算拦截器：校验预算阈值与熔断。"""

    async def check_budget(self, request: GovernanceRequest) -> GovernanceDecision:
        """校验预算是否超限，返回是否熔断。"""
        ...


@runtime_checkable
class IMeteringInterceptor(IGovernanceInterceptor, Protocol):
    """计量拦截器：采集 Token 用量。"""

    async def collect_usage(
        self, request: GovernanceRequest, result: GovernanceResult
    ) -> GovernanceResult:
        """采集执行过程中的 Token 用量。"""
        ...


@runtime_checkable
class IAuditInterceptor(IGovernanceInterceptor, Protocol):
    """审计拦截器：记录操作审计日志。"""

    async def record_audit(
        self, request: GovernanceRequest, result: GovernanceResult
    ) -> None:
        """记录操作审计日志条目。"""
        ...


@runtime_checkable
class IGlobalSchedulerInterceptor(IGovernanceInterceptor, Protocol):
    """全局调度拦截器：提交实例至全局调度队列。"""

    async def submit_to_scheduler(
        self, request: GovernanceRequest
    ) -> GovernanceDecision:
        """将实例提交至全局调度器，返回调度决策。"""
        ...


# =============================================================================
# 拦截器链编排
# =============================================================================

class InterceptorChain:
    """拦截器链编排器。

    按 design 2.1.3(5) 定义的顺序执行拦截器。
    执行顺序：
        前置：鉴权 → 租户隔离 → 审计(前) → 配额 → 预算 → 全局调度
        后置：计量采集 → 审计(后)
    """

    def __init__(
        self,
        auth: Optional[IAuthInterceptor] = None,
        tenant_isolation: Optional[ITenantIsolationInterceptor] = None,
        audit: Optional[IAuditInterceptor] = None,
        quota: Optional[IQuotaInterceptor] = None,
        budget: Optional[IBudgetInterceptor] = None,
        global_scheduler: Optional[IGlobalSchedulerInterceptor] = None,
        metering: Optional[IMeteringInterceptor] = None,
        abac: Optional[IGovernanceInterceptor] = None,
        fail_closed: bool = True,
    ) -> None:
        """初始化拦截器链。

        Args:
            fail_closed: True 时拦截器异常拒绝请求；False 时放行。
            abac: ABAC 拦截器（偏差修正新增，默认 None 向后兼容）。
            其余参数为各拦截器实例，None 表示该拦截器未启用（降级放行）。
        """
        self._auth = auth
        self._abac = abac
        self._tenant_isolation = tenant_isolation
        self._audit = audit
        self._quota = quota
        self._budget = budget
        self._global_scheduler = global_scheduler
        self._metering = metering
        self._fail_closed = fail_closed

    @property
    def is_degraded_mode(self) -> bool:
        """是否处于降级模式（全部拦截器未启用）。"""
        return all(
            interceptor is None
            for interceptor in [
                self._auth,
                self._abac,
                self._tenant_isolation,
                self._audit,
                self._quota,
                self._budget,
                self._global_scheduler,
                self._metering,
            ]
        )

    async def execute_before(
        self,
        request: GovernanceRequest,
    ) -> GovernanceDecision:
        """执行前置拦截器链。

        按 鉴权 → ABAC → 租户隔离 → 审计(前) → 配额 → 预算 → 全局调度 顺序执行。
        任一拦截器拒绝或异常（fail_closed）时立即终止。
        """
        decision = GovernanceDecision(allowed=True)

        # 按顺序执行前置拦截器（ABAC 在鉴权之后、租户隔离之前）
        before_interceptors: List[Optional[IGovernanceInterceptor]] = [
            self._auth,
            self._abac,
            self._tenant_isolation,
            self._audit,
            self._quota,
            self._budget,
            self._global_scheduler,
        ]

        for interceptor in before_interceptors:
            if interceptor is None:
                continue
            try:
                decision = await interceptor.intercept_before(request, decision)
            except Exception as exc:
                if self._fail_closed:
                    return GovernanceDecision(
                        allowed=False,
                        deny_reason=f"拦截器异常: {type(exc).__name__}",
                    )
                continue

            if not decision.allowed:
                return decision

        return decision

    async def execute_after(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
        result: GovernanceResult,
    ) -> GovernanceResult:
        """执行后置拦截器链。

        按 计量采集 → 审计(后) 顺序执行。
        """
        # 计量采集
        if self._metering is not None:
            try:
                result = await self._metering.intercept_after(
                    request, decision, result
                )
            except Exception:
                pass

        # 审计(后)
        if self._audit is not None:
            try:
                result = await self._audit.intercept_after(
                    request, decision, result
                )
            except Exception:
                pass

        return result
