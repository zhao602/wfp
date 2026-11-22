# TODO(tech-debt): B档候选孤儿（族群 C1），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 引擎抽象接口（engine/abstractions/workflow_engine.py）
#
# 职责：定义框架无关的编排引擎契约 IWorkflowEngine。
# 设计意图（对应 design 2.2.2.1、spec 4.4.3、验收 B7）：
#   1. 将 LangGraph 的图编译与执行能力封装在实现内部，
#      使业务代码与工作流定义不感知底层框架；
#   2. 业务代码唯一可依赖的引擎契约——除 engine/adapter/ 外不出现 langgraph 导入。
# =============================================================================

from typing import Protocol, runtime_checkable

from orchestration_platform.domain.models.execution_instance import (
    RollbackResult,
    StartInstanceRequest,
    ReplayRequest,
    ReplayResult,
    ReviewDecisionRequest,
)
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.engine.abstractions.compiled_workflow import CompiledWorkflow


@runtime_checkable
class IWorkflowEngine(Protocol):
    """编排引擎抽象接口：上层业务唯一可依赖的引擎契约。

    设计意图：将 LangGraph 的图编译与执行能力封装在实现内部，
    使业务代码与工作流定义不感知底层框架（对应 DFX 4.4.3 / 验收 B7）。
    """

    async def compile(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        """编译工作流定义为可执行图中间表示。

        前置条件：definition 已通过校验。
        异常：编译失败 → WF_* 系列错误。
        """
        ...

    async def start(self, request: StartInstanceRequest) -> "ExecutionInstance":
        """启动工作流实例。

        前置条件：实例输入满足定义的输入参数约束。
        后置条件：实例进入待执行/运行中，事件流产生实例创建事件。
        """
        ...

    async def resume(self, instance_id: str, checkpoint_id: str) -> "ExecutionInstance":
        """从指定检查点恢复实例执行。

        异常：检查点缺失 → STATE_CHECKPOINT_NOT_FOUND。
        """
        ...

    async def rollback(
        self, instance_id: str, target_checkpoint_id: str
    ) -> RollbackResult:
        """回滚实例到指定检查点。

        异常：状态冲突 → STATE_INSTANCE_RUNNING（运行中实例禁止回滚）。
        """
        ...

    async def replay(self, request: ReplayRequest) -> ReplayResult:
        """从指定检查点重放实例执行。

        异常：状态冲突 → STATE_INSTANCE_RUNNING。
        """
        ...

    async def submit_review(
        self, instance_id: str, decision: ReviewDecisionRequest
    ) -> "ExecutionInstance":
        """提交人工审核决策。

        异常：实例不处于等待审核状态 → INSTANCE_NOT_WAITING。
        """
        ...
