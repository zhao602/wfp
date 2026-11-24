# =============================================================================
# 子编排器实现（engine/supervisor_orchestration/sub_orchestrator.py）
#
# 职责：实现 ISubOrchestrator，调度领域专家 Agent 集群执行并返回聚合结果（G1/T219）。
# 设计意图（对应 spec 5.1.3、design D1/2.1.3.4）：
#   1. 向 Supervisor 返回聚合结果而非逐 Agent 原始输出全量透传（A1.3.a）；
#   2. 子编排整体失败时按确定性处置链：重试 N 次 → 标记失败 → 结果携带原因；
#   3. 禁止 import langgraph，编排决策在平台自有确定性控制路径上执行。
# =============================================================================

import logging
from typing import Callable, List, Optional

from orchestration_platform.engine.supervisor_orchestration.finding_aggregator import (
    FindingAggregator,
)
from orchestration_platform.engine.supervisor_orchestration.models import (
    AggregatedFinding,
    FindingItem,
    RouteDecision,
)

logger = logging.getLogger(__name__)


class DefaultSubOrchestrator:
    """默认子编排器：调度领域专家 Agent 集群执行并返回聚合发现。

    构造参数：
        aggregator —— 逐级聚合降噪器
        retry_max  —— 子任务失败最大重试次数（默认 2）
        agent_executor —— Agent 执行回调（可选，测试时注入 mock）
            签名: async (task_description: str, route_decision: RouteDecision) -> List[str]
            返回各 Agent 的输出摘要列表
    """

    def __init__(
        self,
        aggregator: Optional[FindingAggregator] = None,
        retry_max: int = 2,
        agent_executor: Optional[Callable] = None,
    ) -> None:
        self._aggregator = aggregator or FindingAggregator()
        self._retry_max = retry_max
        self._agent_executor = agent_executor

    @property
    def top_k(self) -> int:
        return self._aggregator.top_k

    async def execute_domain_task(
        self,
        sub_orchestration_id: str,
        task_description: str,
        route_decision: RouteDecision,
    ) -> AggregatedFinding:
        """执行领域任务，返回聚合发现。

        流程：
            1. 调度领域专家 Agent 集群执行（经 agent_executor 回调）；
            2. 逐 Agent 输出构造 FindingItem（摘要 + 哈希 + 来源）；
            3. 经 FindingAggregator 降噪（去重/限量/附来源）；
            4. 失败时按重试链处置：重试 N 次 → 标记失败 → 携带原因。

        Returns:
            聚合发现（携带来源标识，禁止匿名汇聚，A1.3.a）
        """
        last_error: Optional[str] = None

        for attempt in range(self._retry_max + 1):
            try:
                agent_summaries = await self._dispatch_agents(
                    task_description, route_decision
                )
                items: List[FindingItem] = [
                    FindingItem.from_summary(
                        summary=summary,
                        source=route_decision.sub_orchestrator_id,
                    )
                    for summary in agent_summaries
                ]
                raw_finding = AggregatedFinding(
                    sub_orchestration_id=sub_orchestration_id,
                    sub_orchestrator_id=route_decision.sub_orchestrator_id,
                    items=items,
                    success=True,
                )
                return self._aggregator.aggregate(raw_finding)

            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "子编排 %s 第 %d 次执行失败: %s",
                    sub_orchestration_id,
                    attempt + 1,
                    last_error,
                )

        return AggregatedFinding(
            sub_orchestration_id=sub_orchestration_id,
            sub_orchestrator_id=route_decision.sub_orchestrator_id,
            items=[],
            success=False,
            failure_reason=f"重试 {self._retry_max} 次后仍失败: {last_error}",
        )

    async def _dispatch_agents(
        self, task_description: str, route_decision: RouteDecision
    ) -> List[str]:
        """调度领域专家 Agent 集群执行，返回各 Agent 输出摘要列表。

        若注入了 agent_executor 回调则调用之；否则返回占位摘要（开发/测试用）。
        """
        if self._agent_executor is not None:
            return await self._agent_executor(task_description, route_decision)
        return [f"[{route_decision.sub_orchestrator_id}] 已处理: {task_description}"]
