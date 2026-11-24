# =============================================================================
# 调度器（engine/scheduler.py）
#
# 职责：确定性调度、条件分支求值、循环控制、并行汇聚、人工审核门、超时控制，
#      并统一由调度器写检查点。
# 设计意图（对应 design 2.5.2、spec 5.2.1、验收 A3/A7/B1）：
#   1. 确定性调度——下一节点基于"边条件求值结果 + 声明顺序"，禁用哈希集合迭代；
#   2. 条件分支——受限表达式求值器，仅允许比较与逻辑运算，禁止函数调用；
#   3. 循环控制——loop_counters 递增，达上限返回 WF_LOOP_NOT_CONVERGED；
#   4. 检查点统一写入——节点完成后由调度器写检查点，执行器不直接写。
# =============================================================================

import ast
import asyncio
import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Tuple, runtime_checkable

from orchestration_platform.domain.errors import (
    NODE_TIMEOUT,
    WF_BUDGET_EXCEEDED,
    WF_INSTANCE_SUSPENDED,
    WF_LOOP_NOT_CONVERGED,
    WF_NODE_EXECUTION_FAILED,
    WF_PARALLEL_JOIN_NOT_SATISFIED,
    WF_VALIDATION_FAILED,
    DomainError,
    NodeExecutionError,
)
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.domain_event import (
    DEFAULT_TENANT_ID,
    DomainEvent,
    summarize_text,
)
from orchestration_platform.domain.models.enums import (
    ControlFlowKind,
    EventType,
    JoinWaitPolicy,
    NodeType,
    ParallelFailureStrategy,
)
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.abstractions.compiled_workflow import (
    CompiledWorkflow,
    ParallelRegionMeta,
)
from orchestration_platform.engine.abstractions.domain_event_publisher import (
    IDomainEventPublisher,
)
from orchestration_platform.engine.abstractions.engine_config import EngineSettings
from orchestration_platform.engine.abstractions.node_usage_recorder import NodeUsageRecorder
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.engine.shared_state_manager import SharedStateManager
from orchestration_platform.persistence.abstractions.repositories import (
    ICheckpointRepository,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_checkpoint_id() -> str:
    return f"cp_{uuid.uuid4().hex[:12]}"


def _node_type_label(node_type: Any) -> str:
    """节点类型统一转字符串标签（枚举取 .value，其余 str()）。

    NodeType 为 str-Enum：直接 str() 在 Python 3.10 会得到 "NodeType.TRIGGER"
    形式而非值，运行日志需要值本身（INV-09 字段语义对齐）。
    """
    value = getattr(node_type, "value", None)
    if isinstance(value, str):
        return value
    return str(node_type)


# =============================================================================
# 挂起信号（arch_review_v2_tasks_wave2 G2，CHG-61/DC-26）
# =============================================================================

class WorkflowSuspendedSignal(Exception):
    """人工审核门挂起信号：review 门节点挂起时由 Scheduler 抛出（非错误）。

    设计意图（DC-26）：
      - 继承 Exception 而非 DomainError——挂起不是错误，不进 DomainError
        异常处理器映射（application_factory 零改动）；
      - execute 签名不变（HC-01）约束下「提前终止但非失败」的唯一干净表达；
      - 携带挂起检查点与 approve 续跑位点，供 run_and_record 置 WAITING_REVIEW、
        review 决策经 Wave1 resume_checkpoint 机制唤醒（PA-7 / E-W2-01）。
    """

    code: str = WF_INSTANCE_SUSPENDED

    def __init__(
        self,
        checkpoint_id: Optional[str] = None,
        suspended_node_id: str = "",
        next_node_id: Optional[str] = None,
        review_instructions: str = "",
        timeout_action: str = "",
    ) -> None:
        super().__init__(
            f"实例挂起于人工审核门 {suspended_node_id}"
            f"（checkpoint_id={checkpoint_id}, next_node_id={next_node_id}）"
        )
        self.checkpoint_id = checkpoint_id
        self.suspended_node_id = suspended_node_id
        self.next_node_id = next_node_id
        self.review_instructions = review_instructions
        self.timeout_action = timeout_action


# =============================================================================
# 受限表达式求值器
# =============================================================================

class RestrictedExpressionEvaluator:
    """受限表达式求值器：仅允许对 SharedState 字段做比较与逻辑运算。

    禁止：函数调用、属性访问（除 output. 字段外）、import、赋值、任意代码执行。
    允许：字面量、变量名（映射到 node_outputs）、比较运算、布尔运算。
    """

    # 允许的 AST 节点类型
    _ALLOWED_NODES = (
        ast.Expression, ast.BoolOp, ast.Compare, ast.UnaryOp,
        ast.Name, ast.Constant, ast.Tuple, ast.List, ast.Attribute,
        ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
        ast.In, ast.NotIn, ast.Load,
    )

    def evaluate(self, expression: str, state: SharedState) -> bool:
        """求值条件表达式，返回布尔结果。

        表达式中的变量名映射到 node_outputs 中的值。
        支持 output.field 形式的点号访问（映射到 node_outputs["field"]）。
        """
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as e:
            raise DomainError(
                WF_VALIDATION_FAILED,
                f"条件表达式语法错误：{expression}（{e}）",
            )

        self._validate_node(tree)
        return bool(self._eval_node(tree.body, state))

    def _validate_node(self, node: ast.AST) -> None:
        """递归校验 AST 节点是否在允许集合内。"""
        if not isinstance(node, self._ALLOWED_NODES):
            raise DomainError(
                WF_VALIDATION_FAILED,
                f"条件表达式包含不允许的语法结构：{type(node).__name__}",
            )
        for child in ast.iter_child_nodes(node):
            self._validate_node(child)

    def _eval_node(self, node: ast.AST, state: SharedState) -> Any:
        """递归求值 AST 节点。"""
        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.Name):
            if node.id in ("True", "true"):
                return True
            if node.id in ("False", "false"):
                return False
            if node.id in ("None", "null"):
                return None
            # 变量名映射到 node_outputs
            return state.node_outputs.get(node.id)

        if isinstance(node, ast.Attribute):
            # output.field → node_outputs["field"]
            base = self._eval_node(node.value, state)
            if isinstance(base, dict):
                return base.get(node.attr)
            return getattr(base, node.attr, None)

        if isinstance(node, ast.Compare):
            left = self._eval_node(node.left, state)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval_node(comparator, state)
                if not self._eval_compare(op, left, right):
                    return False
                left = right
            return True

        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return all(self._eval_node(v, state) for v in node.values)
            if isinstance(node.op, ast.Or):
                return any(self._eval_node(v, state) for v in node.values)

        if isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand, state)
            if isinstance(node.op, ast.Not):
                return not operand
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return +operand

        if isinstance(node, (ast.Tuple, ast.List)):
            return [self._eval_node(e, state) for e in node.elts]

        raise DomainError(
            WF_VALIDATION_FAILED,
            f"条件表达式包含不支持的表达式类型：{type(node).__name__}",
        )

    def _eval_compare(self, op: ast.cmpop, left: Any, right: Any) -> bool:
        if isinstance(op, ast.Eq):
            return left == right
        if isinstance(op, ast.NotEq):
            return left != right
        if isinstance(op, ast.Lt):
            return left < right
        if isinstance(op, ast.LtE):
            return left <= right
        if isinstance(op, ast.Gt):
            return left > right
        if isinstance(op, ast.GtE):
            return left >= right
        if isinstance(op, ast.In):
            return left in right
        if isinstance(op, ast.NotIn):
            return left not in right
        return False


# =============================================================================
# 节点事件回调协议
# =============================================================================

@runtime_checkable
class NodeEventCallback(Protocol):
    """节点事件回调协议：调度器在节点执行各阶段调用，供路由层实时上报事件。

    设计意图（DC-3/DC-7）：
      - 调度器通过此协议将节点级事件（NODE_STARTED/COMPLETED/FAILED）实时上报；
      - 回调为可选注入（INV-5），event_callback=None 时调度器行为不变；
      - 回调异常由调用方自行 try/except 不传播（DC-5）。
    """

    async def on_node_started(
        self, instance_id: str, node_id: str, trace_id: str
    ) -> None:
        """节点开始执行时调用。"""
        ...

    async def on_node_completed(
        self,
        instance_id: str,
        node_id: str,
        trace_id: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> None:
        """节点执行成功完成时调用。

        payload 仅在 node_type == OUTPUT_DISPLAY 时由调度器构造传入
        （含 display_content/display_title），其余节点不传（None 回退空载荷）。
        """
        ...

    async def on_node_failed(
        self, instance_id: str, node_id: str, trace_id: str, error: str
    ) -> None:
        """节点执行失败时调用。"""
        ...


# =============================================================================
# 调度器
# =============================================================================

class Scheduler:
    """确定性调度器：驱动节点执行、条件求值、循环控制、检查点写入。"""

    def __init__(
        self,
        executor_registry: NodeExecutorRegistry,
        checkpoint_repository: Optional[ICheckpointRepository] = None,
        event_callback: Optional[NodeEventCallback] = None,
        run_log_recorder: Optional[Any] = None,
        domain_event_publisher: Optional[IDomainEventPublisher] = None,
        cancellation_checker: Optional[Callable[[], bool]] = None,
        settings: Optional[EngineSettings] = None,
        usage_recorder: Optional[NodeUsageRecorder] = None,
    ) -> None:
        """构造调度器。

        参数：
            executor_registry    —— 节点执行器注册表
            checkpoint_repository —— 检查点仓储；缺省不持久化（仅内存）。
            event_callback       —— 节点事件回调；缺省 None 时不上报节点级事件（INV-5）。
            run_log_recorder     —— 运行日志采集门面（WorkflowRunLogger，末位可选，
                                    INV-4/INV-D-12）；缺省 None 时零采集、零文件写入。
            domain_event_publisher —— 领域事件发布器（arch_review_v2_tasks_wave2
                                    CHG-61/DC-23，发布点 P5/P6）；缺省 None 时
                                    零发布（基线行为不变）。
            cancellation_checker —— 协作式取消检查器（M3 P5 实例级取消，
                                    roadmap design D2 兜底路径）；提供时于
                                    节点边界（主循环游标推进处）轮询，返回
                                    True 即以 CancelledError 终止执行（由
                                    run_and_record CancelledError 分支统一
                                    落 cancelled 终态）；缺省 None 零行为
                                    （HC-01，既有调用点零改动）。
            settings             —— 引擎运行配置（M4 B10：engine-owned
                                    EngineSettings；缺省 None → EngineSettings()
                                    默认值，与 configuration 默认一致；宿主经
                                    application.engine_wiring.build_engine_settings()
                                    注入以生效 env 覆盖）。
            usage_recorder       —— 节点级用量计量器（M4 B10：engine-owned
                                    NodeUsageRecorder 协议实例；缺省 None 跳过
                                    计量——API 进程由宿主注入装配后的 governance
                                    计量器，worker/run_local 无装配 → None ==
                                    现行跳过语义）。
        """
        self._registry = executor_registry
        self._checkpoint_repo = checkpoint_repository
        self._expression_evaluator = RestrictedExpressionEvaluator()
        self._settings = settings or EngineSettings()
        self._usage_recorder = usage_recorder
        self._event_callback = event_callback
        self._run_log_recorder = run_log_recorder
        self._domain_event_publisher = domain_event_publisher
        self._cancellation_checker = cancellation_checker

    async def execute(
        self,
        compiled: CompiledWorkflow,
        initial_state: SharedState,
        instance_id: str,
        trace_id: str,
        resume_checkpoint: Optional[Checkpoint] = None,
        tenant_id: Optional[str] = None,
    ) -> SharedState:
        """执行编译后的工作流，返回最终共享状态。

        流程：从入口节点开始 → 逐节点执行 → 条件求值/循环控制 → 写检查点 → 前进。

        resume_checkpoint（arch_review_v2 3.3，CHG-08 部分）：提供时从该检查点
        续跑——初始状态取 state_snapshot、起始节点取 next_node_id、轮次从
        round_index + 1 续统计（检查点记录的是已完成轮次，续跑自下一轮开始，
        避免与同轮唯一约束冲突，CHG-15）；None 时行为与现状完全一致（从 entry
        开始，既有调用点零破坏，HC-01）。suspend 消费不做（F3 属 W5–7，Z-5/E16）。

        tenant_id（roadmap B7 W2a）：业务执行链租户穿参——提供时
        node.* 领域事件按该租户发布、节点级用量按该租户记账；
        None 时事件回退 DEFAULT_TENANT_ID（既有调用方零改动，
        HC-01）、计量回退 recorder 兜底（W1b 语义不变）。
        """
        if resume_checkpoint is not None:
            initial_state = resume_checkpoint.state_snapshot
            current_node_id = resume_checkpoint.next_node_id
            round_index = resume_checkpoint.round_index + 1
        else:
            current_node_id = compiled.entry_node_id
            round_index = 0

        state_manager = SharedStateManager(initial_state)
        if resume_checkpoint is not None:
            # 恢复场景：轮次与位点同步进共享状态（快照内含值为检查点完成轮次）
            state_manager._state.round_index = round_index

        while current_node_id is not None:
            # M3 协作式取消兜底（design D2）：节点边界轮询外部取消信号，
            # True 即 CancelledError 终止（run_and_record 分支统一落终态）；
            # checker 为 None 零行为（HC-01）。主循环游标推进处为检查点，
            # 长驻并行区域内部不轮询（抢占式 task.cancel 主路径覆盖）。
            if (
                self._cancellation_checker is not None
                and self._cancellation_checker()
            ):
                raise asyncio.CancelledError()
            node = compiled.get_node(current_node_id)
            if node is None:
                break

            # B6 并行扇出：激活区域分派（loop-top dispatch，design §4.2/§4.5）。
            # 分叉源 S 走单游标（完成后位点=分支入口，下轮分派）；分支路径内
            # 节点与前沿未空的 join 转入区域执行器（含恢复重建）。
            region = compiled.region_containing(current_node_id)
            if (
                region is not None
                and self._should_dispatch_parallel_region(
                    region, current_node_id, state_manager
                )
            ):
                region_next = await self._execute_parallel_region(
                    compiled, state_manager, instance_id, trace_id, region,
                    tenant_id=tenant_id,
                )
                if region_next is None:
                    break
                current_node_id = region_next
                # 区域内轮次由各分支同步原子递增（state.round_index），
                # 出区域后主循环本地轮次重新对齐（CHG-15 唯一约束保持）
                round_index = state_manager.state.round_index
                state_manager.advance_to(current_node_id)
                continue

            # 节点生命周期共享例程（B6 design §4.1：单游标/并行分支共用，
            # 行为与原内联实现一致；检查点/后继/轮次/HITL 留在调用方）
            result = await self._run_node_lifecycle(
                compiled, state_manager, instance_id, trace_id, node, round_index,
                tenant_id=tenant_id,
            )

            # HITL 挂起消费分支（arch_review_v2_tasks_wave2 G2，CHG-61/DC-26；
            # OP-03 唯一消费面）：三重条件同时满足才消费——① suspend 元数据为真；
            # ② 控制流节点；③ control_flow_kind 为 human_review_gate；其余任何
            # 节点出现 suspend 元数据维持现状不消费（记告警，既有行为零变化）
            if (
                result.metadata.get("suspend") is True
                and node.node_type == NodeType.CONTROL_FLOW
                and result.metadata.get("control_flow_kind")
                == ControlFlowKind.HUMAN_REVIEW_GATE.value
            ):
                # 顺序硬约束（DC-26 规则 2）：① 先定 approve 续跑后继位点；
                # ② 写挂起检查点（completed=review 门，next=真实后继，复用
                # (instance_id, round_index) 唯一约束，E-W2-01）；③ 抛信号提前
                # 终止（不进入失败路径）；提前退出不计步数护栏轮次
                # （round_index 不递增，DC-10 既有护栏语义不变）
                suspend_next_node_id = self._determine_next_node(
                    compiled, node, state_manager
                )
                suspend_checkpoint_id = await self._write_checkpoint(
                    state_manager, instance_id, current_node_id, round_index,
                    suspend_next_node_id,
                )
                raise WorkflowSuspendedSignal(
                    checkpoint_id=suspend_checkpoint_id,
                    suspended_node_id=node.node_id,
                    next_node_id=suspend_next_node_id,
                    review_instructions=str(
                        result.metadata.get("review_instructions") or ""
                    ),
                    timeout_action=str(result.metadata.get("timeout_action") or ""),
                )

            if result.metadata.get("suspend") is True:
                # 非 review 门携带 suspend 元数据：不消费（OP-03），记告警后
                # 按既有路径继续（spec FR-03 规则 1 / INV-07）
                logging.warning(
                    "节点 %s 携带 suspend 元数据但非人工审核门，不消费（OP-03）",
                    node.node_id,
                )

            # 确定下一节点（先于检查点写入：next_node_id 须为真实后继，
            # 恢复才能从正确位点续跑，arch_review_v2 3.3，CHG-09/DC-09）
            next_node_id = self._determine_next_node(
                compiled, node, state_manager
            )

            # B6：激活区域 join 走单游标收尾（恢复路径，前沿为空）→
            # 先清前沿三表再写检查点（快照不含已收敛残留；崩溃窗口
            # 落在 join 幂等重跑，触发预检直接命中，不重跑分支）
            if node.node_id in compiled.parallel_regions:
                state_manager.clear_parallel_region(node.node_id)

            # 写检查点（completed=当前节点，next=真实后继）
            await self._write_checkpoint(
                state_manager, instance_id, current_node_id, round_index,
                next_node_id,
            )

            current_node_id = next_node_id
            if current_node_id is not None:
                state_manager.advance_to(current_node_id)

            round_index += 1
            state_manager._state.round_index = round_index

            # G6 实例级步数护栏（arch_review_v2 3.6，CHG-01/04/08；卡 A2；DC-10；E6）：
            # 默认关闭（workflow_step_guard_enabled=False，基线零变化，HC-07）；
            # 开启且轮次达到 workflow_max_steps 且仍有后继待执行 → WF_BUDGET_EXCEEDED
            # （实例级全局限额），与 loop 节点 max_iterations 的节点级局部限额
            # （WF_LOOP_NOT_CONVERGED，:652/:672 一带）解耦、先触发先报告；
            # token/成本预算本轮不做（依赖 F7 三表，归 W11–13，OP-01 范围裁决）。
            if (
                current_node_id is not None
                and self._settings.workflow_step_guard_enabled
                and round_index >= self._settings.workflow_max_steps
            ):
                raise DomainError(
                    WF_BUDGET_EXCEEDED,
                    f"实例 {instance_id} 步数护栏触发：已完成 {round_index} 轮达到"
                    f"最大步数限额 workflow_max_steps="
                    f"{self._settings.workflow_max_steps}，"
                    f"下一节点 {current_node_id}（第 {round_index + 1} 轮）被阻断"
                    f"（最后完成节点 {node.node_id}）",
                )

        return state_manager.state

    # ------------------------------------------------------------------
    # 节点生命周期共享例程（B6 design §4.1：单游标主循环与并行分支共用）
    # ------------------------------------------------------------------
    async def _run_node_lifecycle(
        self,
        compiled: CompiledWorkflow,
        state_manager: SharedStateManager,
        instance_id: str,
        trace_id: str,
        node,
        round_index: int,
        tenant_id: Optional[str] = None,
    ) -> NodeExecutionResult:
        """执行单个节点的完整生命周期（回调/运行日志/领域事件/输出写）。

        覆盖：on_node_started 回调 → 运行日志 begin_node → _execute_node
        （含超时/异常路径）→ 失败路径（on_node_failed / node.failed 领域
        事件 / record_node_failure → 抛 NodeExecutionError）或成功路径
        （on_node_completed / node.succeeded 领域事件 / end_node / 命名
        输出写），实现与原主循环内联版本一致（determinism / suspend /
        resource / run-log 测试守门）。检查点/后继确定/轮次推进/HITL
        挂起消费留在调用方（并行分支路径不含控制流节点，无 HITL 可能）。
        """
        # 节点开始事件回调（DC-3，INV-3）
        if self._event_callback is not None:
            try:
                await self._event_callback.on_node_started(
                    instance_id, node.node_id, trace_id
                )
            except Exception:
                logging.warning(
                    "on_node_started 回调失败: node_id=%s", node.node_id,
                    exc_info=True,
                )

        # 运行日志：节点级开始记录（T6.3；与事件回调并行、互不干扰，
        # 采集异常独立吞掉，绝不影响执行，INV-01）
        input_snapshot: Optional[Dict[str, Any]] = None
        if self._run_log_recorder is not None:
            try:
                input_snapshot = dict(state_manager.state.node_outputs)
                self._run_log_recorder.begin_node(
                    node.node_id,
                    _node_type_label(node.node_type),
                    round_index,
                    input_snapshot,
                )
            except Exception:
                logging.warning(
                    "运行日志 begin_node 接线失败: node_id=%s", node.node_id,
                    exc_info=True,
                )

        # 执行节点（起止时间随返回值传递：并行分支下共享实例属性存在
        # 竞态，B6 design §4.1 改为随结果传递；_execute_node 执行语义不变）
        result, node_started_at, node_finished_at = await self._execute_node(
            node, state_manager, instance_id, trace_id, round_index, compiled
        )

        if not result.success:
            # 节点失败事件回调（DC-4，spec 5.3.1.3）
            if self._event_callback is not None:
                try:
                    await self._event_callback.on_node_failed(
                        instance_id,
                        node.node_id,
                        trace_id,
                        error=result.error_message or "",
                    )
                except Exception:
                    logging.warning(
                        "on_node_failed 回调失败: node_id=%s", node.node_id,
                        exc_info=True,
                    )
            # 领域事件：节点失败发布（CHG-61/DC-23 发布点 P6；
            # 发布失败由发布器自吞异常，绝不影响执行，DC-31）
            if self._domain_event_publisher is not None:
                try:
                    await self._domain_event_publisher.publish_domain_event(
                        DomainEvent(
                            event_type="node.failed",
                            instance_id=instance_id,
                            tenant_id=tenant_id or DEFAULT_TENANT_ID,
                            trace_id=trace_id,
                            node_id=node.node_id,
                            payload={
                                "node_id": node.node_id,
                                "error_code": result.error_code
                                or WF_NODE_EXECUTION_FAILED,
                                "error_message": summarize_text(
                                    result.error_message
                                ),
                            },
                        )
                    )
                except Exception:
                    logging.warning(
                        "node.failed 领域事件发布失败: node_id=%s",
                        node.node_id,
                        exc_info=True,
                    )
            # 运行日志：节点失败/超时记录（T6.3；NODE_TIMEOUT 区分 timeout 态）
            if self._run_log_recorder is not None:
                try:
                    node_status = (
                        "timeout" if result.error_code == NODE_TIMEOUT else "failed"
                    )
                    node_duration_ms = result.duration_ms
                    if (
                        not node_duration_ms
                        and node_started_at is not None
                        and node_finished_at is not None
                    ):
                        node_duration_ms = max(
                            0,
                            int(
                                (node_finished_at - node_started_at)
                                .total_seconds()
                                * 1000
                            ),
                        )
                    self._run_log_recorder.record_node_failure(
                        node.node_id,
                        _node_type_label(node.node_type),
                        round_index,
                        input_snapshot,
                        error_code=result.error_code or WF_NODE_EXECUTION_FAILED,
                        error_message=result.error_message or "",
                        status=node_status,
                        started_at=node_started_at,
                        finished_at=node_finished_at,
                        duration_ms=node_duration_ms,
                    )
                except Exception:
                    logging.warning(
                        "运行日志 record_node_failure 接线失败: node_id=%s",
                        node.node_id,
                        exc_info=True,
                    )
            raise NodeExecutionError(
                result.error_code or WF_NODE_EXECUTION_FAILED,
                f"节点 {node.node_id} 执行失败：{result.error_message}",
            )

        # 节点完成事件回调（DC-3，INV-3）
        if self._event_callback is not None:
            try:
                if node.node_type == NodeType.OUTPUT_DISPLAY:
                    display_config = node.configuration
                    display_payload: dict[str, JsonValue] = {
                        "display_content": result.output_value,
                        "display_title": (
                            display_config.display_title or node.name
                        ),
                    }
                    await self._event_callback.on_node_completed(
                        instance_id, node.node_id, trace_id, display_payload
                    )
                else:
                    await self._event_callback.on_node_completed(
                        instance_id, node.node_id, trace_id
                    )
            except Exception:
                logging.warning(
                    "on_node_completed 回调失败: node_id=%s", node.node_id,
                    exc_info=True,
                )

        # 领域事件：节点成功发布（CHG-61/DC-23 发布点 P5；
        # 发布失败由发布器自吞异常，绝不影响执行，DC-31）
        if self._domain_event_publisher is not None:
            try:
                node_duration_ms = result.duration_ms
                if (
                    not node_duration_ms
                    and node_started_at is not None
                    and node_finished_at is not None
                ):
                    node_duration_ms = max(
                        0,
                        int(
                            (node_finished_at - node_started_at)
                            .total_seconds()
                            * 1000
                        ),
                    )
                await self._domain_event_publisher.publish_domain_event(
                    DomainEvent(
                        event_type="node.succeeded",
                        instance_id=instance_id,
                        tenant_id=tenant_id or DEFAULT_TENANT_ID,
                        trace_id=trace_id,
                        node_id=node.node_id,
                        payload={
                            "node_id": node.node_id,
                            "duration_ms": node_duration_ms,
                            "output_name": node.name,
                        },
                    )
                )
            except Exception:
                logging.warning(
                    "node.succeeded 领域事件发布失败: node_id=%s",
                    node.node_id,
                    exc_info=True,
                )

        # 运行日志：节点级成功记录（T6.3）
        if self._run_log_recorder is not None:
            try:
                self._run_log_recorder.end_node(
                    result,
                    started_at=node_started_at,
                    finished_at=node_finished_at,
                    input_snapshot=input_snapshot,
                )
            except Exception:
                logging.warning(
                    "运行日志 end_node 接线失败: node_id=%s", node.node_id,
                    exc_info=True,
                )

        # 节点级用量记账（roadmap B7 W1b）：执行器透传 provider usage
        # （metadata["token_usage"]）时记节点级用量行——与执行级同队列/
        # 同按租户分组刷盘；metering 未装配或无 usage 时静默跳过，
        # 绝不影响执行主流程（INV-01）
        token_usage = (result.metadata or {}).get("token_usage")
        if isinstance(token_usage, dict) and token_usage:
            recorder = self._usage_recorder
            if recorder is not None:
                try:
                    await recorder.record_node_usage(
                        tenant_id=tenant_id,
                        instance_id=instance_id,
                        node_id=node.node_id,
                        agent_node_id=(
                            node.node_id
                            if node.node_type == NodeType.AGENT
                            else None
                        ),
                        model_identifier=str(
                            result.metadata.get("model_identifier", "unknown")
                        ),
                        token_usage=token_usage,
                        workflow_definition_id=compiled.definition_id,
                    )
                except Exception:
                    logging.warning(
                        "节点级用量记账失败: node_id=%s", node.node_id,
                        exc_info=True,
                    )

        # 写入命名输出
        if result.output_name and result.output_value is not None:
            state_manager.write_output(result.output_name, result.output_value)

        return result

    # ------------------------------------------------------------------
    # B6 并行扇出：区域执行（design §4.2 等待计数 / §4.3 失败语义 /
    # §4.4 领域事件 / §4.5 resume 区域重建）
    # ------------------------------------------------------------------
    def _should_dispatch_parallel_region(
        self,
        region: ParallelRegionMeta,
        current_node_id: str,
        state_manager: SharedStateManager,
    ) -> bool:
        """判定当前位点是否转入并行区域执行器（loop-top dispatch）。

        - 分叉源 S：走单游标（完成后位点=分支入口，下一轮再分派）；
        - 分支路径内节点：转入区域执行器（含恢复重建，design §4.5）；
        - 汇聚节点：前沿仍有在途分支（恢复场景）→ 转入；前沿为空 →
          单游标收尾（主循环清理三表，design §6 位点表）。
        """
        if current_node_id == region.fork_source_node_id:
            return False
        if current_node_id == region.join_node_id:
            return (
                state_manager.get_parallel_positions(region.join_node_id)
                is not None
            )
        return True

    def _parallel_wait_satisfied(
        self, region: ParallelRegionMeta, state_manager: SharedStateManager
    ) -> bool:
        """等待轴触发判定（wait_all 全部到达 / wait_any 任一 / wait_n_of_m 达数）。"""
        arrivals = len(state_manager.get_parallel_arrivals(region.join_node_id))
        if region.wait_policy == JoinWaitPolicy.WAIT_ALL.value:
            return arrivals >= len(region.branch_entry_ids)
        if region.wait_policy == JoinWaitPolicy.WAIT_ANY.value:
            return arrivals >= 1
        return arrivals >= (region.wait_count or 1)

    def _parallel_required_arrivals(
        self, region: ParallelRegionMeta
    ) -> Optional[int]:
        """等待轴所需成功到达数；wait_all 返回 None（等待全部）。"""
        if region.wait_policy == JoinWaitPolicy.WAIT_ALL.value:
            return None
        if region.wait_policy == JoinWaitPolicy.WAIT_ANY.value:
            return 1
        return region.wait_count or 1

    async def _execute_parallel_region(
        self,
        compiled: CompiledWorkflow,
        state_manager: SharedStateManager,
        instance_id: str,
        trace_id: str,
        region: ParallelRegionMeta,
        tenant_id: Optional[str] = None,
    ) -> Optional[str]:
        """执行一个激活并行扇出区域：分支并发 → 等待计数 → join 收敛。

        返回 join 首出边目标（区域后继）；join 为终点返回 None。
        失败语义（design §4.3）：fast_fail 首个失败立即裁撤兄弟分支并沿
        NodeExecutionError 路径实例失败；失败轴 wait_all 等全部分支结束
        后首个失败聚合胜出；wait_any / wait_n_of_m 不可达 →
        WF_PARALLEL_JOIN_NOT_SATISFIED。
        """
        join_id = region.join_node_id
        arrivals = state_manager.get_parallel_arrivals(join_id)
        cancelled = state_manager.get_parallel_cancelled(join_id)

        # 触发预检（恢复场景：等待已达成 → 不再起分支，直接执行 join，
        # 保证 join 幂等重跑且不重跑任何分支）
        if self._parallel_wait_satisfied(region, state_manager):
            return await self._execute_parallel_join(
                compiled, state_manager, instance_id, trace_id, region,
                tenant_id=tenant_id,
            )

        # 前沿重建（design §4.5：已到达分支不重跑；在途分支按位点重跑，
        # at-least-once，与既有单游标 resume 语义一致；旧检查点无前沿
        # 字段时按分支入口全量重建，仍向后兼容）
        stored_positions = state_manager.get_parallel_positions(join_id)
        if stored_positions is None:
            to_run = [
                entry
                for entry in region.branch_entry_ids
                if entry not in arrivals and entry not in cancelled
            ]
            state_manager.init_parallel_region(
                join_id, {entry: region.branch_paths[entry][0] for entry in to_run}
            )
        else:
            to_run = [
                entry
                for entry in stored_positions
                if entry not in arrivals and entry not in cancelled
            ]
            state_manager.init_parallel_region(
                join_id, {entry: stored_positions[entry] for entry in to_run}
            )

        required = self._parallel_required_arrivals(region)
        # 不可达预检（防御：恢复前沿不完整 / wait_all 存在已取消分支）
        if required is not None and len(arrivals) + len(to_run) < required:
            raise DomainError(
                WF_PARALLEL_JOIN_NOT_SATISFIED,
                f"并行汇聚 {join_id} 等待不可满足：已成功到达 "
                f"{len(arrivals)} 个分支，可执行 {len(to_run)} 个分支，"
                f"小于等待数 {required}（等待策略 {region.wait_policy}）",
            )
        if (
            required is None
            and cancelled
            and len(arrivals) + len(to_run) < len(region.branch_entry_ids)
        ):
            raise DomainError(
                WF_PARALLEL_JOIN_NOT_SATISFIED,
                f"并行汇聚 {join_id} 等待不可满足：存在已取消分支 "
                f"{sorted(cancelled)}，全量到达（wait_all）无法达成",
            )

        # 分支任务并发拉起（每分支先发 PARALLEL_BRANCH_STARTED 领域事件）
        branch_tasks: Dict[asyncio.Task, str] = {}
        for entry in to_run:
            await self._publish_parallel_event(
                instance_id, trace_id, region, entry,
                EventType.PARALLEL_BRANCH_STARTED.value, "started",
                tenant_id=tenant_id,
            )
            task = asyncio.create_task(
                self._run_parallel_branch(
                    compiled, state_manager, instance_id, trace_id, region, entry,
                    tenant_id=tenant_id,
                )
            )
            branch_tasks[task] = entry

        pending: Set[asyncio.Task] = set(branch_tasks)
        first_failure: Optional[BaseException] = None
        fast_fail = (
            region.failure_strategy == ParallelFailureStrategy.FAST_FAIL.value
        )
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    entry = branch_tasks[task]
                    if task.cancelled():
                        # 裁撤引发的取消：状态事件由 _drain_cancelled 统一发布
                        continue
                    exc = task.exception()
                    if exc is not None:
                        if first_failure is None:
                            first_failure = exc  # 首个失败胜出（design §4.3）
                        await self._publish_parallel_event(
                            instance_id, trace_id, region, entry,
                            EventType.PARALLEL_BRANCH_COMPLETED.value, "failed",
                            tenant_id=tenant_id,
                        )
                        continue
                    await self._publish_parallel_event(
                        instance_id, trace_id, region, entry,
                        EventType.PARALLEL_BRANCH_COMPLETED.value, "succeeded",
                        tenant_id=tenant_id,
                    )

                # 失败轴 fast_fail：首个失败立即裁撤其余分支并实例失败
                if first_failure is not None and fast_fail:
                    for task in pending:
                        task.cancel()
                    await self._drain_cancelled(
                        branch_tasks, pending, state_manager,
                        instance_id, trace_id, region, tenant_id=tenant_id,
                    )
                    raise first_failure

                # 等待轴触发：成功到达数达等待数 → 裁撤剩余分支并执行 join
                if required is not None and self._parallel_wait_satisfied(
                    region, state_manager
                ):
                    for task in pending:
                        task.cancel()
                    await self._drain_cancelled(
                        branch_tasks, pending, state_manager,
                        instance_id, trace_id, region, tenant_id=tenant_id,
                    )
                    return await self._execute_parallel_join(
                        compiled, state_manager, instance_id, trace_id, region,
                        tenant_id=tenant_id,
                    )

                # 等待轴不可达：成功 + 在途 < 等待数（分支失败/结束所致）
                if required is not None:
                    reachable = (
                        len(state_manager.get_parallel_arrivals(join_id))
                        + len(pending)
                    )
                    if reachable < required:
                        for task in pending:
                            task.cancel()
                        await self._drain_cancelled(
                            branch_tasks, pending, state_manager,
                            instance_id, trace_id, region, tenant_id=tenant_id,
                        )
                        raise DomainError(
                            WF_PARALLEL_JOIN_NOT_SATISFIED,
                            f"并行汇聚 {join_id} 等待不可满足：已成功到达 "
                            f"{len(state_manager.get_parallel_arrivals(join_id))}"
                            f" 个分支，在途 {len(pending)} 个分支，小于等待数 "
                            f"{required}（等待策略 {region.wait_policy}）",
                        )
        except asyncio.CancelledError:
            # 外部取消（整实例终止）：裁撤在途分支后原样上抛，避免孤儿任务
            for task in pending:
                task.cancel()
            raise

        # 全部分支结束：失败轴 wait_all 聚合（首个失败胜出）或 wait_all 收敛
        if first_failure is not None:
            raise first_failure
        return await self._execute_parallel_join(
            compiled, state_manager, instance_id, trace_id, region,
            tenant_id=tenant_id,
        )

    async def _run_parallel_branch(
        self,
        compiled: CompiledWorkflow,
        state_manager: SharedStateManager,
        instance_id: str,
        trace_id: str,
        region: ParallelRegionMeta,
        branch_entry_id: str,
        tenant_id: Optional[str] = None,
    ) -> None:
        """沿分支路径逐节点执行：生命周期例程 + 前沿更新 + 分支检查点。

        每节点：同步分配轮次（读改写之间无 await → 原子，并发分支轮次
        互异，CHG-15 (instance_id, round_index) 唯一约束保持）→ 生命周期
        例程 → 前沿更新（到达 join 记 arrival 并移除位点；否则推进位点）
        → 写分支检查点（branch_id=分支入口，roadmap:155 缓解项；先更新
        前沿后写检查点，快照前沿与 cp.next 一致，design §6 位点表）。
        取消（等待触发 / fast_fail 裁撤）：记 parallel_cancelled 后原样
        上抛（取消发生在 await 点，不再产生输出/检查点，design §4.2）。
        """
        join_id = region.join_node_id
        path = region.branch_paths[branch_entry_id]
        positions = state_manager.get_parallel_positions(join_id)
        if positions is not None and branch_entry_id in positions:
            current_node_id: Optional[str] = positions[branch_entry_id]
        else:
            current_node_id = path[0]  # 防御：位点缺失回退分支入口
        try:
            while current_node_id is not None:
                node = compiled.get_node(current_node_id)
                if node is None:
                    break  # 防御：编译期已保证路径节点存在
                # 轮次同步分配（无 await 间隔 → 原子；并发分支轮次互异）
                node_round = state_manager.state.round_index
                state_manager._state.round_index = node_round + 1
                await self._run_node_lifecycle(
                    compiled, state_manager, instance_id, trace_id, node,
                    node_round,
                    tenant_id=tenant_id,
                )
                next_index = path.index(current_node_id) + 1
                next_node_id: Optional[str] = (
                    path[next_index] if next_index < len(path) else join_id
                )
                if next_node_id == join_id:
                    # 分支到达 join：记到达 + 移除位点 + 写检查点后结束
                    # （join 本体由区域执行器在触发后统一执行）
                    state_manager.record_branch_arrival(join_id, branch_entry_id)
                    state_manager.remove_branch_position(join_id, branch_entry_id)
                    await self._write_checkpoint(
                        state_manager, instance_id, current_node_id, node_round,
                        next_node_id, branch_id=branch_entry_id,
                    )
                    return
                state_manager.advance_branch_position(
                    join_id, branch_entry_id, next_node_id
                )
                await self._write_checkpoint(
                    state_manager, instance_id, current_node_id, node_round,
                    next_node_id, branch_id=branch_entry_id,
                )
                current_node_id = next_node_id
        except asyncio.CancelledError:
            state_manager.record_branch_cancelled(join_id, branch_entry_id)
            raise

    async def _drain_cancelled(
        self,
        branch_tasks: Dict[asyncio.Task, str],
        pending: Set[asyncio.Task],
        state_manager: SharedStateManager,
        instance_id: str,
        trace_id: str,
        region: ParallelRegionMeta,
        tenant_id: Optional[str] = None,
    ) -> None:
        """等待被裁撤分支真正结束，并按最终状态补发 PARALLEL_BRANCH_COMPLETED。

        被取消分支在 await 点抛 CancelledError → 记 parallel_cancelled 后
        上抛 → 此处按 cancelled 发布；恰在裁撤窗口内到达/失败的分支按
        succeeded / failed 发布（取消发生在 await 点，窗口内仍可收尾）。
        """
        if not pending:
            return
        done, _ = await asyncio.wait(pending)
        arrivals = state_manager.get_parallel_arrivals(region.join_node_id)
        for task in done:
            entry = branch_tasks[task]
            if task.cancelled():
                status = "succeeded" if entry in arrivals else "cancelled"
            elif task.exception() is not None:
                status = "failed"
            else:
                status = "succeeded"
            await self._publish_parallel_event(
                instance_id, trace_id, region, entry,
                EventType.PARALLEL_BRANCH_COMPLETED.value, status,
                tenant_id=tenant_id,
            )

    async def _execute_parallel_join(
        self,
        compiled: CompiledWorkflow,
        state_manager: SharedStateManager,
        instance_id: str,
        trace_id: str,
        region: ParallelRegionMeta,
        tenant_id: Optional[str] = None,
    ) -> Optional[str]:
        """执行汇聚节点本体并收敛区域：先清三表再写 join 检查点。

        清表先于检查点（快照不含已收敛区域残留）；崩溃窗口落在 join
        幂等重跑（恢复后触发预检 _parallel_wait_satisfied 直接命中，
        不重跑分支）。join 检查点 branch_id=None（design §6 位点表）。
        """
        join_id = region.join_node_id
        join_node = compiled.get_node(join_id)
        if join_node is None:
            return None  # 防御：编译期已保证 join 存在
        join_round = state_manager.state.round_index
        state_manager._state.round_index = join_round + 1
        await self._run_node_lifecycle(
            compiled, state_manager, instance_id, trace_id, join_node, join_round,
            tenant_id=tenant_id,
        )
        next_node_id = self._determine_next_node(compiled, join_node, state_manager)
        state_manager.clear_parallel_region(join_id)
        await self._write_checkpoint(
            state_manager, instance_id, join_id, join_round, next_node_id,
            branch_id=None,
        )
        return next_node_id

    async def _publish_parallel_event(
        self,
        instance_id: str,
        trace_id: str,
        region: ParallelRegionMeta,
        branch_entry_id: str,
        event_type_value: str,
        status: str,
        tenant_id: Optional[str] = None,
    ) -> None:
        """发布并行分支领域事件（design §4.4；发布失败自吞，DC-31）。

        事件类型取 EventType.PARALLEL_BRANCH_STARTED / COMPLETED
        （enums.py:149-150 本次接线），沿调度器 node.* 领域事件前缀
        约定发布（DomainEvent 前缀白名单 instance./node./governance.）；
        载荷含 join_node_id / branch_entry_id / 状态。
        """
        if self._domain_event_publisher is None:
            return
        try:
            await self._domain_event_publisher.publish_domain_event(
                DomainEvent(
                    event_type=f"node.{event_type_value}",
                    instance_id=instance_id,
                    tenant_id=tenant_id or DEFAULT_TENANT_ID,
                    trace_id=trace_id,
                    node_id=region.join_node_id,
                    payload={
                        "join_node_id": region.join_node_id,
                        "branch_entry_id": branch_entry_id,
                        "status": status,
                    },
                )
            )
        except Exception:
            logging.warning(
                "并行分支领域事件发布失败: join=%s branch=%s",
                region.join_node_id, branch_entry_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # 节点执行（含超时控制）
    # ------------------------------------------------------------------
    async def _execute_node(
        self,
        node,
        state_manager: SharedStateManager,
        instance_id: str,
        trace_id: str,
        round_index: int,
        compiled: CompiledWorkflow,
    ) -> Tuple[NodeExecutionResult, datetime, datetime]:
        """执行单个节点（含超时控制），返回（结果, 开始时间, 结束时间）。

        compiled（upstream_input_ref T4.3 / CHG-B06）：由其 nodes 派生
        节点定义索引注入 NodeExecutionContext（形态 B 引用解析用，
        DC-03 方案 A 单点注入；不影响既有字段与调度逻辑，INV-E09）。
        起止时间随返回值传递（B6 design §4.1 并行竞态修复：原共享
        实例属性 _last_node_* 在并发分支下互相覆盖；节点执行语义不变）。
        """
        executor = self._registry.get_executor(node.node_type)

        timeout = node.timeout_seconds or self._settings.node_default_timeout_seconds
        context = NodeExecutionContext(
            node_definition=node,
            shared_state=state_manager.state,
            instance_id=instance_id,
            trace_id=trace_id,
            timeout_seconds=timeout,
            round_index=round_index,
            node_definitions={n.node_id: n for n in compiled.nodes},
        )

        # 运行日志：入口记录开始时间（T6.2，正常/超时/异常路径共用；
        # 随返回值传递给调用方，B6 design §4.1 改为随结果传递）
        node_started_at = _utc_now()

        try:
            # 尝试使用 asyncio.wait_for 实现超时控制；
            # 若无运行中事件循环（如同步测试场景），直接调用执行器。
            try:
                asyncio.get_running_loop()
                result = await asyncio.wait_for(
                    executor.execute(context),
                    timeout=timeout,
                )
            except RuntimeError:
                result = await executor.execute(context)
            # 运行日志：正常出口记录结束时间（T6.2）
            return result, node_started_at, _utc_now()
        except asyncio.TimeoutError:
            # 运行日志：超时分支补算耗时（T6.2），随结果交给调用方记录
            node_finished_at = _utc_now()
            duration_ms = max(
                0, int((node_finished_at - node_started_at).total_seconds() * 1000)
            )
            return (
                NodeExecutionResult(
                    success=False,
                    error_code=NODE_TIMEOUT,
                    error_message=f"节点 {node.node_id} 执行超时（{timeout} 秒）",
                    duration_ms=duration_ms,
                ),
                node_started_at,
                node_finished_at,
            )
        except Exception as exc:
            # 运行日志：重抛前就地捕获异常类型与堆栈交门面（T6.2，先采集后原样重抛；
            # error_stack 后续经门面脱敏/截断，采集异常独立吞掉不影响执行，INV-01）
            if self._run_log_recorder is not None:
                try:
                    self._run_log_recorder.record_node_failure(
                        node.node_id,
                        _node_type_label(node.node_type),
                        round_index,
                        None,
                        error_code=WF_NODE_EXECUTION_FAILED,
                        error_message=str(exc),
                        status="failed",
                        started_at=node_started_at,
                        finished_at=_utc_now(),
                        error_type=type(exc).__name__,
                        error_stack=traceback.format_exc(),
                    )
                except Exception:
                    logging.warning(
                        "运行日志 record_node_failure 接线失败: node_id=%s",
                        node.node_id,
                        exc_info=True,
                    )
            # 非超时异常：上报 NODE_FAILED 后重抛（DC-4，spec 5.3.3.1）
            if self._event_callback is not None:
                try:
                    await self._event_callback.on_node_failed(
                        instance_id, node.node_id, trace_id, error=str(exc)
                    )
                except Exception:
                    logging.warning(
                        "on_node_failed 回调失败: node_id=%s", node.node_id,
                        exc_info=True,
                    )
            raise

    # ------------------------------------------------------------------
    # 检查点写入
    # ------------------------------------------------------------------
    async def _write_checkpoint(
        self,
        state_manager: SharedStateManager,
        instance_id: str,
        completed_node_id: str,
        round_index: int,
        next_node_id: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> Optional[str]:
        """节点完成后统一写入检查点，返回检查点标识。

        next_node_id：真实后继节点（主循环先确定后继再写检查点，保证恢复从
        正确位点续跑，arch_review_v2 3.3，CHG-09/DC-09）；缺省 None（终态）。
        branch_id（B6 roadmap_b6_parallel_fanout，roadmap:155 缓解项「checkpoint
        先加 branch_id」）：并行分支节点检查点 = 分支入口 Bi；S / join 与
        单游标检查点 = None（缺省，既有调用点零变化，HC-01）。
        返回值（arch_review_v2_tasks_wave2 3.2，CHG-61/DC-26）：挂起消费分支
        经此取 checkpoint_id 供 WorkflowSuspendedSignal 携带；既有调用点忽略
        返回值，行为零变化；仓储未注入（不持久化）时返回 None。
        """
        if self._checkpoint_repo is None:
            return None

        checkpoint = Checkpoint(
            checkpoint_id=_new_checkpoint_id(),
            instance_id=instance_id,
            completed_node_id=completed_node_id,
            next_node_id=next_node_id,
            state_snapshot=state_manager.snapshot(),
            round_index=round_index,
            branch_id=branch_id,
        )
        return await self._checkpoint_repo.append(checkpoint)

    # ------------------------------------------------------------------
    # 下一节点确定（确定性调度）
    # ------------------------------------------------------------------
    def _determine_next_node(
        self,
        compiled: CompiledWorkflow,
        node,
        state_manager: SharedStateManager,
    ) -> Optional[str]:
        """确定下一节点（确定性调度）。

        规则：
        - 普通节点：取第一条出边的目标（按声明顺序）；
        - 条件分支：按条件求值结果选择，无匹配则用默认分支，均无则报错；
        - 循环节点：检查终止条件，满足则退出循环取非回边出边，否则取 loop_body_entry；
        - 其他控制流：取第一条出边。
        """
        if node.node_type == NodeType.CONTROL_FLOW:
            config = node.configuration
            kind = getattr(config, "control_flow_kind", None)

            if kind == ControlFlowKind.CONDITIONAL_BRANCH:
                return self._eval_branch(compiled, node, state_manager)

            if kind == ControlFlowKind.LOOP:
                return self._eval_loop(compiled, node, state_manager)

        # 普通节点：取第一条出边的目标（按声明顺序，保证确定性）
        edges = compiled.successors(node.node_id)
        if not edges:
            return None
        return edges[0].target_node_id

    # ------------------------------------------------------------------
    # 条件分支求值
    # ------------------------------------------------------------------
    def _eval_branch(
        self,
        compiled: CompiledWorkflow,
        node,
        state_manager: SharedStateManager,
    ) -> Optional[str]:
        """条件分支求值：按声明顺序求值条件，首个为真者对应的 target 即为后继。

        无匹配且无默认分支 → 终止并报错（spec 5.2.1.3b）。
        """
        config = node.configuration
        conditions = getattr(config, "conditions", [])
        default_target = getattr(config, "default_branch_target", None)

        for condition in conditions:
            try:
                if self._expression_evaluator.evaluate(
                    condition.expression, state_manager.state
                ):
                    state_manager.set_branch_selection(node.node_id, condition.target_node_id)
                    return condition.target_node_id
            except DomainError:
                continue

        if default_target:
            state_manager.set_branch_selection(node.node_id, default_target)
            return default_target

        raise DomainError(
            WF_VALIDATION_FAILED,
            f"条件分支节点 {node.node_id} 无匹配分支且未配置默认分支",
        )

    # ------------------------------------------------------------------
    # 循环控制
    # ------------------------------------------------------------------
    def _eval_loop(
        self,
        compiled: CompiledWorkflow,
        node,
        state_manager: SharedStateManager,
    ) -> Optional[str]:
        """循环控制：检查终止条件与最大迭代次数。

        满足终止条件 → 退出循环，取非回边出边（如 finalize_node）；
        未满足且未达上限 → 取 loop_body_entry 继续循环；
        达上限仍未满足 → WF_LOOP_NOT_CONVERGED。
        """
        config = node.configuration
        termination_condition = getattr(config, "loop_termination_condition", "")
        loop_body_entry = getattr(config, "loop_body_entry", "")
        max_iterations = getattr(config, "max_iterations", None) or self._settings.loop_max_iterations

        counter = state_manager.get_loop_counter(node.node_id)

        # 检查终止条件
        if termination_condition:
            try:
                if self._expression_evaluator.evaluate(
                    termination_condition, state_manager.state
                ):
                    # 终止条件满足，退出循环——取非回边出边
                    edges = compiled.successors(node.node_id)
                    for edge in edges:
                        if edge.target_node_id != loop_body_entry:
                            return edge.target_node_id
                    return None
            except DomainError:
                pass

        # 检查最大迭代次数
        if counter >= max_iterations:
            raise DomainError(
                WF_LOOP_NOT_CONVERGED,
                f"循环节点 {node.node_id} 达到最大迭代次数 {max_iterations} 但未满足终止条件",
            )

        # 继续循环
        state_manager.increment_loop_counter(node.node_id)
        return loop_body_entry
