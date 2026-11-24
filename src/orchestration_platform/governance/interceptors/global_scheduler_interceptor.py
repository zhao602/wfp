# TODO(tech-debt): B档候选孤儿（族群 B1），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 全局调度拦截器（governance/interceptors/global_scheduler_interceptor.py）
#
# 职责：实例启动前入全局队列，按资源池可用性决定放行或排队。
# 设计意图（对应 spec 5.4、design 2.2.2、任务 T71.1）：
#   1. intercept_before 实例启动前入全局队列；
#   2. 调用 GlobalScheduler.submit；
#   3. 资源充足时放行至引擎启动实例；
#   4. 资源不足时返回 202 实例已排队；
#   5. 未启用全局调度器时自动放行（降级模式）。
# =============================================================================

from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import InstancePriority
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
    GovernanceResult,
)
from orchestration_platform.governance.scheduler.global_scheduler import (
    GlobalScheduler,
)


class GlobalSchedulerInterceptor:
    """全局调度拦截器实现。

    实例启动前入全局队列，按资源池可用性决定放行或排队。
    实现 IGlobalSchedulerInterceptor 接口。
    """

    def __init__(
        self,
        scheduler: GlobalScheduler,
        enabled: Optional[bool] = None,
    ) -> None:
        """初始化全局调度拦截器。

        Args:
            scheduler: 全局调度器实例。
            enabled: 是否启用；缺省从配置读取。
        """
        self._scheduler = scheduler
        self._enabled = (
            enabled if enabled is not None else get_settings().global_scheduler_enabled
        )

    async def submit_to_scheduler(
        self, request: GovernanceRequest
    ) -> GovernanceDecision:
        """将实例提交至全局调度器，返回调度决策。"""
        if not request.instance_id or not request.tenant_id:
            return GovernanceDecision(allowed=True)

        priority_str = request.metadata.get("priority", "normal")
        try:
            priority = InstancePriority(priority_str)
        except ValueError:
            priority = InstancePriority.NORMAL

        result = await self._scheduler.submit(
            instance_id=request.instance_id,
            tenant_id=request.tenant_id,
            priority=priority,
        )

        if result.scheduled:
            return GovernanceDecision(allowed=True, metadata={"scheduled": True})
        elif result.queued:
            return GovernanceDecision(
                allowed=False,
                deny_reason="实例已排队等待资源",
                metadata={"queued": True, "reason": result.reason},
            )
        else:
            return GovernanceDecision(
                allowed=False,
                deny_reason=result.reason or "调度失败",
                metadata={"scheduled": False, "queued": False},
            )

    async def intercept_before(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
    ) -> GovernanceDecision:
        """前置拦截：实例启动前入全局队列。

        未启用全局调度器时自动放行（降级模式）。
        """
        if not self._enabled:
            return decision

        try:
            return await self.submit_to_scheduler(request)
        except Exception as exc:
            return GovernanceDecision(
                allowed=False,
                deny_reason=f"全局调度拦截器异常: {type(exc).__name__}: {exc}",
            )

    async def intercept_after(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
        result: GovernanceResult,
    ) -> GovernanceResult:
        """后置拦截：资源释放时通知调度器。"""
        if not self._enabled:
            return result

        try:
            if request.instance_id:
                await self._scheduler.on_resource_released(request.instance_id)
        except Exception:
            pass

        return result
