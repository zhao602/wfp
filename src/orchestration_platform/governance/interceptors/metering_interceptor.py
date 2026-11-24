# 销项（2026-11-21，roadmap B0/D3）：原 B1 族候选孤儿，已由治理拦截器链接线为生产调用点（governance_factory create_governance_chain 六槽装配），见 docs/tech-debt-ledger.md 第 4 章
# =============================================================================
# 计量拦截器（governance/interceptors/metering_interceptor.py）
#
# 职责：Agent 节点执行后异步采集 Token 用量。
# 设计意图（对应 spec 5.2、design 2.2.2、任务 T61.1）：
#   1. intercept_after 异步采集 token 用量；
#   2. 调用 TokenMeter.collect_usage 与 UsageBufferQueue.enqueue；
#   3. 异步采集不阻塞 Agent 节点执行与工作流推进；
#   4. 不修改 NodeExecutor 接口，通过拦截器注入。
# =============================================================================

from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
    GovernanceResult,
)
from orchestration_platform.governance.metering.token_meter import TokenMeter
from orchestration_platform.governance.metering.usage_buffer_queue import (
    UsageBufferQueue,
)


class MeteringInterceptor:
    """计量拦截器实现。

    Agent 节点执行后异步采集 Token 用量。
    实现 IMeteringInterceptor 接口。
    """

    def __init__(
        self,
        token_meter: TokenMeter,
        buffer_queue: UsageBufferQueue,
        enabled: Optional[bool] = None,
    ) -> None:
        """初始化计量拦截器。

        Args:
            token_meter: Token 用量采集器。
            buffer_queue: 用量缓冲队列。
            enabled: 是否启用；缺省从配置读取。
        """
        self._meter = token_meter
        self._queue = buffer_queue
        self._enabled = (
            enabled if enabled is not None else get_settings().metering_enabled
        )

    async def collect_usage(
        self, request: GovernanceRequest, result: GovernanceResult
    ) -> GovernanceResult:
        """采集执行过程中的 Token 用量。"""
        if not request.tenant_id or not request.instance_id:
            return result

        usage_data = result.token_usage
        if usage_data is None:
            return result

        record = self._meter.collect_usage(
            tenant_id=request.tenant_id,
            instance_id=request.instance_id,
            node_id=request.metadata.get("node_id", "unknown"),
            inference_result=usage_data,
            agent_node_id=request.metadata.get("agent_node_id"),
            workflow_definition_id=request.metadata.get("workflow_definition_id"),
        )

        await self._queue.enqueue(record)

        result.audit_summary = result.audit_summary or {}
        result.audit_summary["usage_record_id"] = record.record_id
        result.audit_summary["total_tokens"] = record.total_tokens
        return result

    async def intercept_before(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
    ) -> GovernanceDecision:
        """前置拦截：计量无需前置处理。"""
        return decision

    async def intercept_after(
        self,
        request: GovernanceRequest,
        decision: GovernanceDecision,
        result: GovernanceResult,
    ) -> GovernanceResult:
        """后置拦截：异步采集 Token 用量。

        未启用治理时跳过（降级模式）。
        异步采集不阻塞主流程。
        """
        if not self._enabled:
            return result

        try:
            return await self.collect_usage(request, result)
        except Exception:
            # 计量异常不影响业务结果
            return result
