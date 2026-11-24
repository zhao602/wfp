# =============================================================================
# SupervisorNodeExecutor 节点执行器（engine/supervisor_orchestration/node_executor.py）
#
# 职责：实现 SUPERVISOR 节点类型，驱动监督者编排器执行（G1/T220）。
# 设计意图（对应 spec 5.1.1、design D1/2.1.3.6）：
#   1. 以 node_type=SUPERVISOR 注册进 node_executor_registry；
#   2. 与 SubWorkflowNodeExecutor 完全平行、互不引用（D1）；
#   3. 开关关闭时不注册，SUPERVISOR 节点类型按未知拦截（A1.6.a）；
#   4. 禁止 import langgraph。
# =============================================================================

import time
from typing import Any, Dict, Optional

from orchestration_platform.domain.errors import (
    SUP_SUB_ORCHESTRATION_FAILED,
    DomainError,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.engine.supervisor_orchestration.finding_aggregator import (
    FindingAggregator,
)
from orchestration_platform.engine.supervisor_orchestration.models import (
    SupervisorOrchestrationResult,
)
from orchestration_platform.engine.supervisor_orchestration.orchestration_state_store import (
    InMemoryStateStore,
)
from orchestration_platform.engine.supervisor_orchestration.route_decision_table import (
    RouteDecisionTable,
)
from orchestration_platform.engine.supervisor_orchestration.sub_orchestrator import (
    DefaultSubOrchestrator,
)
from orchestration_platform.engine.supervisor_orchestration.supervisor_orchestrator import (
    DefaultSupervisorOrchestrator,
)


class SupervisorNodeExecutor:
    """SUPERVISOR 节点执行器：驱动监督者编排器完成顶层任务分解与聚合。

    构造注入：
        decision_table    —— 路由决策表（可选，延迟初始化）
        state_store       —— 编排状态存储（可选，默认内存）
        max_sub_orchestrators —— 子编排器数量上限
        retry_max         —— 子任务失败最大重试次数
        agent_executor    —— Agent 执行回调（可选，测试注入 mock）

    与 SubWorkflowNodeExecutor 完全平行、互不引用。
    """

    def __init__(
        self,
        decision_table: Optional[RouteDecisionTable] = None,
        state_store: Optional[Any] = None,
        max_sub_orchestrators: int = 20,
        retry_max: int = 2,
        agent_executor: Optional[Any] = None,
    ) -> None:
        self._decision_table = decision_table
        self._state_store = state_store
        self._max_sub = max_sub_orchestrators
        self._retry_max = retry_max
        self._agent_executor = agent_executor

    @property
    def node_type(self) -> NodeType:
        """返回该执行器处理的节点类型：SUPERVISOR。"""
        return NodeType.SUPERVISOR

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行 SUPERVISOR 节点：构建编排器 → 运行 → 返回结果。

        签名与 SubWorkflowNodeExecutor.execute(context) -> NodeExecutionResult 同构。
        """
        start_time = time.perf_counter()

        try:
            task_description = self._extract_task_description(context)
            orchestrator = self._build_orchestrator(context)
            result: SupervisorOrchestrationResult = await orchestrator.run(
                task_description
            )

            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return NodeExecutionResult(
                success=result.success,
                output_name=context.node_definition.output_name,
                output_value=self._result_to_output(result),
                error_code=None if result.success else SUP_SUB_ORCHESTRATION_FAILED,
                error_message=None if result.success else self._build_error_message(result),
                duration_ms=duration_ms,
                metadata={
                    "orchestration_id": result.orchestration_id,
                    "status": result.status.value,
                    "failed_sub_count": len(result.failed_sub_tasks),
                    "finding_count": sum(len(f.items) for f in result.findings),
                },
            )

        except DomainError as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return NodeExecutionResult(
                success=False,
                error_code=exc.error_code,
                error_message=exc.message,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return NodeExecutionResult(
                success=False,
                error_code=SUP_SUB_ORCHESTRATION_FAILED,
                error_message=f"监督者编排器执行异常: {exc}",
                duration_ms=duration_ms,
            )

    def _extract_task_description(self, context: NodeExecutionContext) -> str:
        """从节点上下文提取任务描述。

        优先从节点配置的 task_description 字段获取；
        其次从共享状态的指定输入字段获取；
        兜底使用节点 ID 作为任务描述。
        """
        config = context.node_definition.configuration
        if hasattr(config, "task_description") and config.task_description:
            return config.task_description
        if hasattr(config, "input_field") and config.input_field:
            value = context.shared_state.read_output(config.input_field)
            if value is not None:
                return str(value)
        return f"节点 {context.node_definition.node_id} 的监督者编排任务"

    def _build_orchestrator(
        self, context: NodeExecutionContext
    ) -> DefaultSupervisorOrchestrator:
        """构建监督者编排器实例。

        若构造时已注入 decision_table/state_store 则复用；
        否则从节点配置延迟初始化。
        """
        table = self._decision_table
        if table is None:
            table = self._init_decision_table(context)

        store = self._state_store
        if store is None:
            store = InMemoryStateStore()

        sub_orchestrator = DefaultSubOrchestrator(
            aggregator=FindingAggregator(),
            retry_max=self._retry_max,
            agent_executor=self._agent_executor,
        )

        return DefaultSupervisorOrchestrator(
            decision_table=table,
            state_store=store,
            sub_orchestrator=sub_orchestrator,
            aggregator=FindingAggregator(),
            max_sub_orchestrators=self._max_sub,
        )

    def _init_decision_table(
        self, context: NodeExecutionContext
    ) -> RouteDecisionTable:
        """从节点配置初始化路由决策表。

        支持两种配置方式：
            1. 内联决策表（config.route_decision_table 为字典）；
            2. 外部 YAML 文件路径（config.route_decision_table_path 为字符串）。
        """
        config = context.node_definition.configuration
        if hasattr(config, "route_decision_table") and config.route_decision_table:
            return RouteDecisionTable.from_dict(config.route_decision_table)
        if hasattr(config, "route_decision_table_path") and config.route_decision_table_path:
            return RouteDecisionTable.from_yaml_file(config.route_decision_table_path)
        return RouteDecisionTable.from_dict(self._default_decision_table())

    def _default_decision_table(self) -> Dict[str, Any]:
        """默认路由决策表（兜底通用路由）。"""
        return {
            "version": "1.0",
            "default_route": "generalist",
            "fallback_on_no_decision": "default",
            "routes": [],
        }

    def _result_to_output(
        self, result: SupervisorOrchestrationResult
    ) -> Dict[str, Any]:
        """将编排结果转为节点输出值。"""
        return {
            "orchestration_id": result.orchestration_id,
            "success": result.success,
            "status": result.status.value,
            "findings": [
                {
                    "sub_orchestration_id": f.sub_orchestration_id,
                    "sub_orchestrator_id": f.sub_orchestrator_id,
                    "items": [
                        {
                            "content_summary": item.content_summary,
                            "content_hash": item.content_hash,
                            "source_sub_orchestrator_id": item.source_sub_orchestrator_id,
                        }
                        for item in f.items
                    ],
                    "success": f.success,
                }
                for f in result.findings
            ],
            "failed_sub_tasks": [
                {
                    "sub_orchestration_id": ref.sub_orchestration_id,
                    "sub_orchestrator_id": ref.sub_orchestrator_id,
                    "failure_reason": ref.failure_reason,
                }
                for ref in result.failed_sub_tasks
            ],
        }

    def _build_error_message(
        self, result: SupervisorOrchestrationResult
    ) -> str:
        """构建失败错误消息。"""
        reasons = [
            f"{ref.sub_orchestrator_id}: {ref.failure_reason}"
            for ref in result.failed_sub_tasks
            if ref.failure_reason
        ]
        return "子编排失败: " + "; ".join(reasons) if reasons else "子编排失败（无详细原因）"
