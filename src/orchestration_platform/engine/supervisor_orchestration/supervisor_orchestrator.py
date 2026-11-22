# =============================================================================
# 监督者编排器实现（engine/supervisor_orchestration/supervisor_orchestrator.py）
#
# 职责：实现 ISupervisorOrchestrator，顶层监督者编排器（G1/T219）。
# 设计意图（对应 spec 5.1.1/5.1.2/5.1.4/5.1.5、design D1/2.1.3.5）：
#   1. decompose —— 确定性分解顶层任务为子编排任务；
#   2. route —— 经 T217 决策表进行确定性路由；
#   3. aggregate_and_complete —— 汇聚子编排发现并完成编排；
#   4. 检查点联动——每个决策点 save 快照，支持中断恢复（A1.5）；
#   5. 子编排器数量上限 supervisor_orchestration_max_sub_orchestrators（默认 20）。
#
# 禁止 import langgraph；编排决策全部在平台自有确定性控制路径上执行。
# =============================================================================

import logging
import uuid
from typing import List, Optional

from orchestration_platform.domain.errors import (
    SUP_ROUTE_DECISION_FAILED,
    SUP_SUB_ORCHESTRATION_FAILED,
    DomainError,
)
from orchestration_platform.engine.supervisor_orchestration.abstractions import (
    IOrchestrationStateStore,
    ISubOrchestrator,
)
from orchestration_platform.engine.supervisor_orchestration.finding_aggregator import (
    FindingAggregator,
)
from orchestration_platform.engine.supervisor_orchestration.models import (
    AggregatedFinding,
    OrchestrationStatus,
    RouteDecision,
    SubOrchestrationRef,
    SupervisorOrchestrationContext,
    SupervisorOrchestrationResult,
)
from orchestration_platform.engine.supervisor_orchestration.route_decision_table import (
    RouteDecisionTable,
)

logger = logging.getLogger(__name__)


class DefaultSupervisorOrchestrator:
    """默认监督者编排器：分解 → 路由 → 分派 → 聚合完成。

    构造参数：
        decision_table    —— 路由决策表（T217）
        state_store       —— 编排状态存储（T218）
        sub_orchestrator  —— 子编排器实例
        aggregator        —— 逐级聚合降噪器
        max_sub_orchestrators —— 单编排子编排器数量上限（默认 20）
    """

    def __init__(
        self,
        decision_table: RouteDecisionTable,
        state_store: IOrchestrationStateStore,
        sub_orchestrator: ISubOrchestrator,
        aggregator: Optional[FindingAggregator] = None,
        max_sub_orchestrators: int = 20,
    ) -> None:
        self._decision_table = decision_table
        self._state_store = state_store
        self._sub_orchestrator = sub_orchestrator
        self._aggregator = aggregator or FindingAggregator()
        self._max_sub = max_sub_orchestrators

    async def decompose(
        self,
        context: SupervisorOrchestrationContext,
        task_description: str,
    ) -> List[SubOrchestrationRef]:
        """将顶层任务确定性分解为子编排任务。

        分解策略：经路由决策表对任务描述进行路由，产生单一子编排引用。
        同一输入永远得到同一分解结果（确定性，A1.2.a）。
        """
        decision = self._decision_table.decide(task_description)
        ref = SubOrchestrationRef(
            sub_orchestration_id=f"sub_{uuid.uuid4().hex[:12]}",
            sub_orchestrator_id=decision.sub_orchestrator_id,
            task_description=task_description,
        )
        context.sub_orchestration_refs.append(ref)
        context.route_decision_index += 1
        context.touch()
        await self._state_store.save(context)
        return [ref]

    async def route(
        self,
        context: SupervisorOrchestrationContext,
        sub_task: SubOrchestrationRef,
    ) -> RouteDecision:
        """对子任务进行确定性路由决策。

        同一输入永远得到同一决策（路径可预测可复现，A1.2.a）。
        """
        decision = self._decision_table.decide(sub_task.task_description)
        context.route_decision_index += 1
        context.touch()
        await self._state_store.save(context)
        return decision

    async def aggregate_and_complete(
        self,
        context: SupervisorOrchestrationContext,
        sub_findings: List[AggregatedFinding],
    ) -> SupervisorOrchestrationResult:
        """汇聚子编排发现并完成编排。

        流程：
            1. 经 FindingAggregator 批量降噪；
            2. 更新编排上下文状态；
            3. 标记失败子任务；
            4. 落检查点；
            5. 返回编排结果。
        """
        aggregated = self._aggregator.aggregate_batch(sub_findings)
        context.aggregated_findings.extend(aggregated)

        failed_subs: List[SubOrchestrationRef] = []
        for finding in aggregated:
            if not finding.success:
                for ref in context.sub_orchestration_refs:
                    if ref.sub_orchestration_id == finding.sub_orchestration_id:
                        ref.status = OrchestrationStatus.FAILED
                        ref.failure_reason = finding.failure_reason
                        failed_subs.append(ref)
                        break

        all_success = len(failed_subs) == 0
        context.status = (
            OrchestrationStatus.COMPLETED if all_success else OrchestrationStatus.FAILED
        )
        context.touch()
        await self._state_store.save(context)

        return SupervisorOrchestrationResult(
            orchestration_id=context.orchestration_id,
            success=all_success,
            findings=aggregated,
            failed_sub_tasks=failed_subs,
            status=context.status,
        )

    async def run(
        self, task_description: str
    ) -> SupervisorOrchestrationResult:
        """完整编排流程：分解 → 路由 → 分派子编排 → 聚合完成。

        便捷方法，封装 decompose → route → sub_orchestrator.execute → aggregate_and_complete。
        """
        orchestration_id = f"sup_{uuid.uuid4().hex[:12]}"
        context = SupervisorOrchestrationContext(orchestration_id=orchestration_id)
        await self._state_store.save(context)

        # 1. 分解
        sub_refs = await self.decompose(context, task_description)
        if len(sub_refs) > self._max_sub:
            raise DomainError(
                SUP_SUB_ORCHESTRATION_FAILED,
                f"子编排器数量 {len(sub_refs)} 超过上限 {self._max_sub}",
            )

        # 2. 逐子任务路由 + 分派
        sub_findings: List[AggregatedFinding] = []
        for ref in sub_refs:
            context.status = OrchestrationStatus.AWAITING_SUB
            context.touch()
            await self._state_store.save(context)

            decision = await self.route(context, ref)
            finding = await self._sub_orchestrator.execute_domain_task(
                sub_orchestration_id=ref.sub_orchestration_id,
                task_description=ref.task_description,
                route_decision=decision,
            )
            sub_findings.append(finding)

            ref.status = (
                OrchestrationStatus.COMPLETED if finding.success else OrchestrationStatus.FAILED
            )
            if not finding.success:
                ref.failure_reason = finding.failure_reason
            context.touch()
            await self._state_store.save(context)

        # 3. 聚合完成
        return await self.aggregate_and_complete(context, sub_findings)

    async def resume(
        self, orchestration_id: str, task_description: str
    ) -> SupervisorOrchestrationResult:
        """从检查点恢复编排。

        快照有效→续跑（已完成子编排不重复执行，幂等）；
        快照缺失/损坏→明确终止（SUP_STATE_RESTORE_FAILED）。
        """
        context = await self._state_store.load(orchestration_id)
        if context is None:
            raise DomainError(
                SUP_ROUTE_DECISION_FAILED,
                f"编排状态快照不存在，无法恢复: {orchestration_id}",
            )
        if context.status in (
            OrchestrationStatus.COMPLETED,
            OrchestrationStatus.FAILED,
            OrchestrationStatus.TERMINATED,
        ):
            return SupervisorOrchestrationResult(
                orchestration_id=context.orchestration_id,
                success=context.status == OrchestrationStatus.COMPLETED,
                findings=context.aggregated_findings,
                failed_sub_tasks=[
                    ref
                    for ref in context.sub_orchestration_refs
                    if ref.status == OrchestrationStatus.FAILED
                ],
                status=context.status,
            )

        remaining_refs = [
            ref
            for ref in context.sub_orchestration_refs
            if ref.status == OrchestrationStatus.RUNNING
        ]
        sub_findings: List[AggregatedFinding] = list(context.aggregated_findings)
        for ref in remaining_refs:
            context.status = OrchestrationStatus.AWAITING_SUB
            context.touch()
            await self._state_store.save(context)

            decision = await self.route(context, ref)
            finding = await self._sub_orchestrator.execute_domain_task(
                sub_orchestration_id=ref.sub_orchestration_id,
                task_description=ref.task_description,
                route_decision=decision,
            )
            sub_findings.append(finding)
            ref.status = (
                OrchestrationStatus.COMPLETED if finding.success else OrchestrationStatus.FAILED
            )
            if not finding.success:
                ref.failure_reason = finding.failure_reason
            context.touch()
            await self._state_store.save(context)

        return await self.aggregate_and_complete(context, sub_findings)
