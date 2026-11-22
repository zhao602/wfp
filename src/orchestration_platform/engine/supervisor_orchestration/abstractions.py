# =============================================================================
# 监督者编排抽象接口（engine/supervisor_orchestration/abstractions.py）
#
# 职责：定义监督者编排器的三个 Protocol 抽象接口（G1/T215）。
# 设计意图（对应 spec 5.1、design D1）：
#   - ISupervisorOrchestrator：顶层监督者编排器（分解/路由/聚合完成）
#   - ISubOrchestrator：子编排器（执行领域任务，返回聚合结果）
#   - IOrchestrationStateStore：编排状态存储（保存/加载/标记终止）
#
# 全部抽象遵循 typing.Protocol + @runtime_checkable 模式（与既有抽象层一致）。
# 抽象层不导入任何重依赖与 langgraph。
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.engine.supervisor_orchestration.models import (
    AggregatedFinding,
    RouteDecision,
    SubOrchestrationRef,
    SupervisorOrchestrationContext,
    SupervisorOrchestrationResult,
)


@runtime_checkable
class ISupervisorOrchestrator(Protocol):
    """监督者编排器抽象接口。

    前置条件：路由决策表已加载且校验通过。
    后置条件：编排结果携带聚合发现与失败子任务；失败子任务含明确原因。
    异常映射：
        SUP_ROUTE_DECISION_FAILED —— 路由决策失败
        SUP_SUB_ORCHESTRATION_FAILED —— 子编排整体失败
        SUP_STATE_RESTORE_FAILED —— 状态恢复失败
    """

    async def decompose(
        self,
        context: SupervisorOrchestrationContext,
        task_description: str,
    ) -> List[SubOrchestrationRef]:
        """将顶层任务确定性分解为子编排任务。"""
        ...

    async def route(
        self,
        context: SupervisorOrchestrationContext,
        sub_task: SubOrchestrationRef,
    ) -> RouteDecision:
        """对子任务进行确定性路由决策。

        同一输入永远得到同一决策（路径可预测可复现）。
        """
        ...

    async def aggregate_and_complete(
        self,
        context: SupervisorOrchestrationContext,
        sub_findings: List[AggregatedFinding],
    ) -> SupervisorOrchestrationResult:
        """汇聚子编排发现并完成编排。"""
        ...


@runtime_checkable
class ISubOrchestrator(Protocol):
    """子编排器抽象接口。

    前置条件：已接收任务描述与路由决策。
    后置条件：返回聚合结果而非逐 Agent 原始输出全量透传（A1.3.a）。
    """

    async def execute_domain_task(
        self,
        sub_orchestration_id: str,
        task_description: str,
        route_decision: RouteDecision,
    ) -> AggregatedFinding:
        """执行领域任务，返回聚合发现。

        Returns:
            聚合发现（携带来源标识，禁止匿名汇聚）。
        """
        ...


@runtime_checkable
class IOrchestrationStateStore(Protocol):
    """编排状态存储抽象接口。

    前置条件：无。
    后置条件：save 有效上下文可恢复续跑；损坏上下文明确终止不产生孤儿执行。
    """

    async def save(self, context: SupervisorOrchestrationContext) -> None:
        """保存编排上下文快照。"""
        ...

    async def load(
        self, orchestration_id: str
    ) -> Optional[SupervisorOrchestrationContext]:
        """加载编排上下文快照。

        Returns:
            上下文；不存在或损坏时返回 None。
        """
        ...

    async def mark_terminated(
        self, orchestration_id: str, reason: str
    ) -> None:
        """标记编排为明确终止状态。"""
        ...

    @property
    def backend_name(self) -> str:
        """返回存储后端名称（如 'memory'/'local_file'/'postgres'）。"""
        ...
