# =============================================================================
# 重放执行器（application/replay_executor.py）
#
# 职责：re_execute 真跑编排与 node_outputs 差异对比（arch_review_v2_tasks_wave2
#      G4/F4，CHG-50/DC-30 规则 3；任务 5.2/5.4）。
# 设计意图（对应 spec FR-05 规则 2/4 + design §3.4/DC-30）：
#   1. RollbackReplayService 既有方法签名不变（HC-01）——本模块只做编排扩展，
#      不改动服务的 rollback/replay/_strict_replay/_re_execute；
#   2. 新实例 ID = {origin_instance_id}-replay-{N}，事件流独立（原记录零覆盖，
#      spec FR-05 规则 2「不覆盖原记录，保留可审计性」）；
#   3. 差异对比双侧取最新检查点状态快照（state_snapshot.node_outputs）——
#      正常执行链路不向事件仓储写 NODE_SUCCEEDED（注册表内存事件不携带
#      输出值），检查点快照是唯一权威输出存储（INV-08 式适配：
#      design DC-30「从事件流推导」的现状落地形态，batch-records §P5 留痕）；
#   4. 确定性节点白名单显式常量（R6 伪证反例守护，任务 5.2 ⑤）：白名单外
#      差异如实标注 divergent_reason="nondeterministic"，不计入
#      divergent_node_ids（spec FR-05 规则 4「不做伪证」）。
# =============================================================================

from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from orchestration_platform.domain.errors import (
    STATE_CHECKPOINT_NOT_FOUND,
    WF_VALIDATION_FAILED,
    DomainError,
)
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.enums import NodeType, ReplayMode
from orchestration_platform.domain.models.execution_instance import (
    ExecutionInstance,
    ReplayRequest,
)
from orchestration_platform.engine.abstractions.compiled_workflow import (
    CompiledWorkflow,
)
from orchestration_platform.persistence.abstractions.repositories import (
    ICheckpointRepository,
    IWorkflowRepository,
)

# 确定性节点白名单（DC-30 规则 3 ⑤）：此类节点输出差异 → divergent_node_ids
# 回填（AC-03）；白名单外（AGENT/LLM/TOOL/SUBWORKFLOW/SUPERVISOR/
# VECTOR_KNOWLEDGE_BASE/PLUGIN/DYNAMIC_INJECTOR 等概率性/外部调用类型）
# 差异不计 divergent，如实标注 divergent_reason="nondeterministic"（不做伪证）
DETERMINISTIC_NODE_TYPES: frozenset = frozenset(
    {
        NodeType.TRIGGER,
        NodeType.CONTROL_FLOW,
        NodeType.DATA_SOURCE,
        NodeType.DATA_TRANSFORM,
        NodeType.CODE,
        NodeType.OUTPUT_DISPLAY,
    }
)

# 差异原因取值域（compared_outputs 条目 divergent_reason 字段）
DIVERGENT_REASON_OUTPUT_DIVERGED = "output_diverged"        # 确定性节点双侧输出不一致
DIVERGENT_REASON_MISSING_IN_REPLAY = "missing_in_replay"    # 原记录有输出、重放侧缺失
DIVERGENT_REASON_NEW_IN_REPLAY = "new_in_replay"            # 重放侧产出原记录没有的输出
DIVERGENT_REASON_NONDETERMINISTIC = "nondeterministic"      # 非确定性节点差异（如实标注，不计 divergent）

# 输出缺失哨兵：区分「双侧均无该输出」与「输出值为 None/空」
_MISSING = object()


class ComparedOutput(BaseModel):
    """重放对比摘要条目（路由响应 compared_outputs 元素，任务 5.1/5.2）。"""

    node_id: str = Field(description="节点标识")
    node_type: str = Field(description="节点类型（NodeType 值）")
    consistent: bool = Field(description="该节点输出是否一致")
    divergent_reason: Optional[str] = Field(
        default=None,
        description=(
            "不一致原因：output_diverged/missing_in_replay/new_in_replay"
            "（确定性节点）或 nondeterministic（非确定性节点，如实标注）"
        ),
    )


class ReplayExecutionResult(BaseModel):
    """re_execute 编排结果（路由层扩展载体，ReplayResult 域模型零改动，任务 5.3）。"""

    replay_instance_id: str = Field(
        description="重放新实例标识（{origin_instance_id}-replay-{N}）"
    )
    mode: ReplayMode = Field(description="重放模式")
    consistent: bool = Field(description="确定性节点是否全部一致")
    divergent_node_ids: List[str] = Field(
        default_factory=list, description="确定性差异节点清单"
    )
    compared_outputs: List[ComparedOutput] = Field(
        default_factory=list, description="逐节点对比摘要"
    )
    new_event_count: int = Field(
        default=0, description="重放新实例独立事件流事件数"
    )


class ReplayExecutor:
    """re_execute 真跑编排器（CHG-50；launch 回调由路由层注入）。"""

    def __init__(
        self,
        checkpoint_repository: ICheckpointRepository,
        workflow_repository: Optional[IWorkflowRepository] = None,
    ) -> None:
        self._checkpoint_repository = checkpoint_repository
        self._workflow_repository = workflow_repository

    async def resolve_compiled(
        self,
        definition_id: str,
        runtime_compiled: Optional[CompiledWorkflow],
    ) -> CompiledWorkflow:
        """解析重放用编译产物（DC-30 规则 3 步骤①）。

        优先运行时编译产物缓存（与 origin 同一 YAML 编译，同源保真——
        E-W2-11：InstanceRuntime 无 yaml_text 字段，设计「rt.yaml_text
        重新 parse/compile」的现状等价形态）；缓存缺失（重启等场景）回退
        定义仓储重取 + 重新编译；均不可得 → WF_VALIDATION_FAILED。
        """
        if runtime_compiled is not None:
            return runtime_compiled
        if self._workflow_repository is not None:
            try:
                from orchestration_platform.engine.graph_compiler import GraphCompiler

                definition = await self._workflow_repository.get(definition_id)
                return await GraphCompiler().compile(definition)
            except Exception:
                # 定义未入库（API 直启 yaml 场景）或仓储/编译失败
                # → 落入下方统一拒绝（重放不因定义源故障而伪造可执行上下文）
                pass
        raise DomainError(
            WF_VALIDATION_FAILED,
            f"重放缺少可执行定义：{definition_id}"
            "（运行时无编译产物缓存且定义仓储无此定义）",
        )

    async def strict_summary(
        self,
        origin_instance_id: str,
        compiled: CompiledWorkflow,
    ) -> List[ComparedOutput]:
        """strict 模式 compared_outputs 摘要（DC-30 规则 2，任务 5.1）。

        严格重放复用历史 node_outputs（服务 _strict_replay 既有语义，
        零改动）——响应补逐节点 consistent=True 清单；输出清单自 origin
        最新检查点快照推导（node_outputs 唯一权威输出存储，见模块头注 3）。
        """
        outputs = await self._latest_outputs(origin_instance_id)
        summary: List[ComparedOutput] = []
        for node in compiled.nodes:
            if node.effective_output_name in outputs:
                summary.append(
                    ComparedOutput(
                        node_id=node.node_id,
                        node_type=node.node_type.value,
                        consistent=True,
                    )
                )
        return summary

    async def re_execute(
        self,
        origin_instance: ExecutionInstance,
        request: ReplayRequest,
        compiled: CompiledWorkflow,
        next_replay_instance_id: str,
        launch: Callable[[CompiledWorkflow, Checkpoint], Awaitable[int]],
    ) -> ReplayExecutionResult:
        """re_execute 真跑编排（DC-30 规则 3；AC-03 后半）。

        五步分工：① 编译产物解析（resolve_compiled，路由层先行调用）；
        ② REPLAY 事件留痕（路由层先行调用服务 replay()，原实例流）；
        ③④ 新实例注册 + Scheduler(resume_checkpoint) 真跑 + 事件流独立
        （launch 回调，路由层注入，返回新实例事件流事件数）；
        ⑤ 双侧最新检查点快照对比（本方法收尾）。
        """
        checkpoint = await self._checkpoint_repository.get(request.checkpoint_id)
        if checkpoint is None:
            raise DomainError(
                STATE_CHECKPOINT_NOT_FOUND,
                f"检查点 {request.checkpoint_id} 不存在",
            )

        # 步骤③④：launch 回调（路由注入）注册新实例并真跑至终态
        new_event_count: int = await launch(compiled, checkpoint)

        # 步骤⑤：双侧最新检查点快照对比（白名单正反例 + 非确定性如实标注）
        origin_outputs = await self._latest_outputs(origin_instance.instance_id)
        replay_outputs = await self._latest_outputs(next_replay_instance_id)
        compared, divergent = self._compare_outputs(
            compiled, origin_outputs, replay_outputs
        )

        return ReplayExecutionResult(
            replay_instance_id=next_replay_instance_id,
            mode=ReplayMode.RE_EXECUTE,
            consistent=len(divergent) == 0,
            divergent_node_ids=divergent,
            compared_outputs=compared,
            new_event_count=new_event_count,
        )

    async def _latest_outputs(self, instance_id: str) -> Dict[str, object]:
        """取实例最新检查点的命名输出表（无检查点 → 空表，如实对比）。"""
        checkpoint: Optional[Checkpoint] = (
            await self._checkpoint_repository.latest_for_instance(instance_id)
        )
        if checkpoint is None:
            return {}
        return dict(checkpoint.state_snapshot.node_outputs)

    @classmethod
    def _compare_outputs(
        cls,
        compiled: CompiledWorkflow,
        origin_outputs: Dict[str, object],
        replay_outputs: Dict[str, object],
    ) -> Tuple[List[ComparedOutput], List[str]]:
        """逐节点对比双侧命名输出（DC-30 规则 3 ⑤；R6 伪证反例守护）。

        键 = 节点 effective_output_name（output_name 或 node_id，
        node_definition.py 既有语义）；双侧均无该输出 → 不产生条目
        （控制流节点无输出等场景）。
        """
        compared: List[ComparedOutput] = []
        divergent: List[str] = []
        for node in compiled.nodes:
            origin_value = origin_outputs.get(node.effective_output_name, _MISSING)
            replay_value = replay_outputs.get(node.effective_output_name, _MISSING)
            if origin_value is _MISSING and replay_value is _MISSING:
                continue
            if origin_value == replay_value:
                compared.append(
                    ComparedOutput(
                        node_id=node.node_id,
                        node_type=node.node_type.value,
                        consistent=True,
                    )
                )
                continue
            if node.node_type not in DETERMINISTIC_NODE_TYPES:
                # 非确定性节点（概率性/外部调用）：如实标注不计 divergent
                compared.append(
                    ComparedOutput(
                        node_id=node.node_id,
                        node_type=node.node_type.value,
                        consistent=False,
                        divergent_reason=DIVERGENT_REASON_NONDETERMINISTIC,
                    )
                )
                continue
            # 确定性节点差异：三态归因 + divergent 回填（AC-03）
            if origin_value is _MISSING:
                reason = DIVERGENT_REASON_NEW_IN_REPLAY
            elif replay_value is _MISSING:
                reason = DIVERGENT_REASON_MISSING_IN_REPLAY
            else:
                reason = DIVERGENT_REASON_OUTPUT_DIVERGED
            divergent.append(node.node_id)
            compared.append(
                ComparedOutput(
                    node_id=node.node_id,
                    node_type=node.node_type.value,
                    consistent=False,
                    divergent_reason=reason,
                )
            )
        return compared, divergent
