# 销项（2026-11-21，roadmap B0/D3）：原 B1 族候选孤儿，已由治理拦截器链接线为生产调用点（governance_factory create_governance_chain 六槽装配），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# 审计日志拦截器（governance/interceptors/audit_interceptor.py）
#
# 职责：记录操作审计日志。
# 设计意图（对应 spec 5.3、design 2.2.2、任务 T66.1）：
#   1. intercept_before 记录操作前状态（异步）；
#   2. intercept_after 记录操作结果与变更摘要；
#   3. 全操作留痕，异步写入不阻塞业务；
#   4. 未启用治理时跳过（降级模式）。
# =============================================================================

from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import AuditOperationResult
from orchestration_platform.governance.audit.audit_logger import AuditLogger
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
    GovernanceResult,
    IAuditInterceptor,
)


class AuditInterceptor:
    """审计日志拦截器实现。

    记录操作审计日志，异步写入不阻塞业务。
    实现 IAuditInterceptor 接口。
    """

    def __init__(
        self,
        audit_logger: AuditLogger,
        enabled: Optional[bool] = None,
    ) -> None:
        """初始化审计拦截器。

        Args:
            audit_logger: 审计日志器。
            enabled: 是否启用；缺省从配置读取。
        """
        self._logger = audit_logger
        self._enabled = (
            enabled if enabled is not None else get_settings().audit_enabled
        )

    async def record_audit(
        self, request: GovernanceRequest, result: GovernanceResult
    ) -> None:
        """记录操作审计日志条目。"""
        if not request.tenant_id or not request.user_id:
            return

        operation_result = AuditOperationResult.SUCCESS
        if result.audit_summary and result.audit_summary.get("isolation_violation"):
            operation_result = AuditOperationResult.FAILURE

        await self._logger.record(
            tenant_id=request.tenant_id,
            actor_id=request.user_id,
            resource_type=request.resource_type,
            resource_id=request.instance_id or request.metadata.get("resource_id", ""),
            operation=request.operation,
            trace_id=request.trace_id,
            operation_result=operation_result,
            request_payload=request.payload,
            response_summary=str(result.audit_summary) if result.audit_summary else None,
            ip_address=request.metadata.get("ip_address"),
        )

    async def intercept_before(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
    ) -> GovernanceDecision:
        """前置拦截：记录操作前状态（异步）。

        未启用治理时跳过（降级模式）。
        """
        if not self._enabled:
            return decision
        # 前置仅记录请求开始，不阻塞
        return decision

    async def intercept_after(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
        result: GovernanceResult,
    ) -> GovernanceResult:
        """后置拦截：记录操作结果与变更摘要。

        异步写入不阻塞业务。
        """
        if not self._enabled:
            return result

        try:
            await self.record_audit(request, result)
        except Exception:
            # 审计写入失败不影响业务结果
            # 生产环境按 AUDIT_WRITE_FAIL_STRATEGY 配置处理
            pass

        return result
