# =============================================================================
# LangGraph 适配器（engine/adapter/langgraph_adapter.py）
#
# 职责：将 CompiledWorkflow 编译为 LangGraph StateGraph，驱动执行并完成
#      检查点落盘（LangGraph 第二运行时适配器；原引擎契约 Protocol 已随
#      M4 B10 移除，本类即双运行时边界的适配层公开面）。
# 设计意图（对应 design 2.5.2、spec 4.4.3、验收 B7）：
#   1. 此目录是全仓库唯一允许 import langgraph 的位置；
#   2. 适配器不含业务规则——节点语义经执行器注册表单源复用（INV-B4-01），
#      条件/循环决策规程与 scheduler._eval_branch/_eval_loop 一致复刻（DC-B4-02）；
#   3. 适配器负责：中间表示 → 框架图、状态双向映射、执行驱动、检查点落盘。
# 实施定位（arch_review_gap_b4_architecture B4-C1，2026-11-19 真实化）：
#   - compile/start 为真实实现：三类子集拓扑（顺序/条件单层/单层循环，M1~M4）；
#   - 节点函数经 executor_registry.get_executor 调用与内置引擎同一执行器实例，
#     NodeExecutionContext 同口径构造，失败映射 NodeExecutionError 同码回退链；
#   - 等价子集硬边界：并行扇出/汇聚、HITL 挂起、回滚重放、多租户运行时、
#     子工作流嵌套（含 SUPERVISOR 动态子编排）五项显式排除（_validate_subset
#     运行期安全网）；
#   - resume/rollback/replay 为抛错桩（排除③，真实链路走内置引擎检查点与
#     application/rollback_replay_service.py）；submit_review 为最小占位（排除②）；
#   - 本目录同时是"业务代码禁止 import langgraph"隔离格局的物理锚点
#     （守护测试：tests/unit/test_langgraph_isolation.py）。
# =============================================================================

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from orchestration_platform.domain.errors import (
    INSTANCE_NOT_FOUND,
    INSTANCE_NOT_WAITING,
    NODE_TIMEOUT,
    STATE_CHECKPOINT_NOT_FOUND,
    STATE_INSTANCE_RUNNING,
    WF_LOOP_NOT_CONVERGED,
    WF_NODE_EXECUTION_FAILED,
    WF_VALIDATION_FAILED,
    DomainError,
    NodeExecutionError,
    ResourceNotFoundError,
)
from orchestration_platform.domain.models.enums import (
    ControlFlowKind,
    InstanceStatus,
    NodeType,
    TriggerType,
)
from orchestration_platform.domain.models.execution_instance import (
    ExecutionInstance,
    RollbackResult,
    StartInstanceRequest,
    ReplayRequest,
    ReplayResult,
    ReviewDecisionRequest,
)
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.engine.abstractions.compiled_workflow import CompiledWorkflow
from orchestration_platform.engine.abstractions.engine_config import EngineSettings
from orchestration_platform.engine.adapter.langgraph_state_mapping import (
    LangGraphStateMapping,
    _KEY_BRANCH_SELECTIONS,
    _KEY_CURRENT_NODE_ID,
    _KEY_LOOP_COUNTERS,
    _KEY_NEXT_NODE_ID,
    _KEY_NODE_OUTPUTS,
    _KEY_ROUND_INDEX,
)
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.engine.scheduler import RestrictedExpressionEvaluator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def is_langgraph_available() -> bool:
    """探测 langgraph 依赖是否在位（HC-06/GK-06：探测代码同样仅位于本目录）。

    供容器 langgraph_engine_factory 三态判定使用（DC-B4-03/INV-B4-03）：
    True + 本函数 False → 降级内置引擎 + 告警日志，不伪造等价结论。
    """
    try:
        import langgraph  # noqa: F401
    except ImportError:
        return False
    return True


class LangGraphAdapter:
    """LangGraph 双运行时适配器。

    此类是全仓库唯一直接依赖 langgraph 的模块。
    业务代码不感知底层框架，经本类公开方法调用第二运行时。
    """

    def __init__(
        self,
        graph_compiler: Optional[GraphCompiler] = None,
        executor_registry: Optional[Any] = None,
        run_log_recorder: Optional[Any] = None,
        settings: Optional[EngineSettings] = None,
    ) -> None:
        """构造适配器实例。

        参数：
            graph_compiler   —— 图编译器；缺省新建。
            executor_registry —— 节点执行器注册表（节点语义单源，INV-B4-01）；
                                缺省时引擎内新建空 NodeExecutorRegistry()
                                （M4 B10：api 延迟导入已删，生产由宿主显式注入）；
                                矩阵 harness 显式注入与内置引擎同一实例。
            run_log_recorder —— 运行日志采集门面（可选；缺省 None 零采集）。
            settings         —— 引擎运行配置（M4 B10：缺省 None →
                                EngineSettings() 默认值；宿主注入生效 env 覆盖）。
        """
        self._settings = settings or EngineSettings()
        self._compiler = graph_compiler or GraphCompiler(settings=self._settings)
        if executor_registry is None:
            # M4 B10：api 惰性导入已删——缺省引擎内新建空注册表，
            # 生产构造点（application_factory）显式注入容器注册表。
            executor_registry = NodeExecutorRegistry()
        self._registry = executor_registry
        # 求值器类级复用（DC-B4-02）：与 scheduler 同一 RestrictedExpressionEvaluator
        self._expression_evaluator = RestrictedExpressionEvaluator()
        self._run_log_recorder = run_log_recorder
        # 编译缓存：definition_id -> CompiledWorkflow
        self._compiled_cache: Dict[str, CompiledWorkflow] = {}
        # 实例存储（验证期内存实现，生产环境由仓储持久化）
        self._instances: Dict[str, ExecutionInstance] = {}

    # ------------------------------------------------------------------
    # 适配器公开引擎能力（compile/start；resume 系为子集外抛错桩）
    # ------------------------------------------------------------------
    async def compile(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        """编译工作流定义为 CompiledWorkflow（委托 GraphCompiler）。"""
        compiled = await self._compiler.compile(definition)
        self._compiled_cache[definition.definition_id] = compiled
        return compiled

    async def start(self, request: StartInstanceRequest) -> ExecutionInstance:
        """启动工作流实例。

        流程：编译定义 → 构建初始状态 → 构建 LangGraph 图 → 执行 → 返回实例。
        """
        # 获取编译后的工作流
        compiled = self._compiled_cache.get(request.definition_id)
        if compiled is None:
            raise ResourceNotFoundError(
                INSTANCE_NOT_FOUND,
                f"工作流定义 {request.definition_id} 未编译，请先调用 compile",
            )

        # 创建实例
        instance_id = _new_id("inst")
        trace_id = _new_id("trace")
        instance = ExecutionInstance(
            instance_id=instance_id,
            definition_id=request.definition_id,
            definition_version=compiled.definition_version,
            status=InstanceStatus.RUNNING,
            inputs=request.inputs,
            idempotency_key=request.idempotency_key,
            trigger_type=request.trigger_type,
            trace_id=trace_id,
        )
        self._instances[instance_id] = instance

        # 构建初始共享状态
        initial_state = SharedState(
            instance_id=instance_id,
            node_outputs=dict(request.inputs),
            current_node_id=compiled.entry_node_id,
        )

        # 执行工作流（LangGraph 驱动）
        try:
            final_state = await self._execute_graph(compiled, initial_state, instance)
            instance.outputs = dict(final_state.node_outputs)
            instance.status = InstanceStatus.SUCCEEDED
            instance.finished_at = _utc_now()
        except Exception as e:
            instance.status = InstanceStatus.FAILED
            instance.finished_at = _utc_now()
            instance.error = {"message": str(e)}
            if isinstance(e, DomainError):
                instance.error["code"] = e.code

        return instance

    async def resume(
        self, instance_id: str, checkpoint_id: str
    ) -> ExecutionInstance:
        """从指定检查点恢复实例执行。

        检查点恢复不在 LangGraph 等价子集内（排除③，硬证据③边界），
        真实恢复走内置引擎链路；本方法保持抛错桩。
        """
        instance = self._instances.get(instance_id)
        if instance is None:
            raise ResourceNotFoundError(
                INSTANCE_NOT_FOUND, f"实例 {instance_id} 不存在"
            )
        raise DomainError(
            STATE_CHECKPOINT_NOT_FOUND,
            f"检查点恢复不在 LangGraph 等价子集内（排除③），走内置引擎链路",
        )

    async def rollback(
        self, instance_id: str, target_checkpoint_id: str
    ) -> RollbackResult:
        """回滚实例到指定检查点。

        回滚不在 LangGraph 等价子集内（排除③）；真实回滚走
        application/rollback_replay_service.py。
        """
        instance = self._instances.get(instance_id)
        if instance is None:
            raise ResourceNotFoundError(
                INSTANCE_NOT_FOUND, f"实例 {instance_id} 不存在"
            )
        if instance.status == InstanceStatus.RUNNING:
            raise DomainError(
                STATE_INSTANCE_RUNNING,
                f"实例 {instance_id} 正在运行，禁止回滚",
            )
        raise DomainError(
            STATE_CHECKPOINT_NOT_FOUND,
            f"回滚不在 LangGraph 等价子集内（排除③），走 rollback_replay_service",
        )

    async def replay(self, request: ReplayRequest) -> ReplayResult:
        """从指定检查点重放实例执行。

        重放不在 LangGraph 等价子集内（排除③）。
        """
        instance = self._instances.get(request.instance_id)
        if instance is None:
            raise ResourceNotFoundError(
                INSTANCE_NOT_FOUND, f"实例 {request.instance_id} 不存在"
            )
        raise DomainError(
            STATE_CHECKPOINT_NOT_FOUND,
            f"重放不在 LangGraph 等价子集内（排除③），走 rollback_replay_service",
        )

    async def submit_review(
        self, instance_id: str, decision: ReviewDecisionRequest
    ) -> ExecutionInstance:
        """提交人工审核决策。

        HITL 挂起不在 LangGraph 等价子集内（排除②）。
        """
        instance = self._instances.get(instance_id)
        if instance is None:
            raise ResourceNotFoundError(
                INSTANCE_NOT_FOUND, f"实例 {instance_id} 不存在"
            )
        if instance.status != InstanceStatus.WAITING_REVIEW:
            raise DomainError(
                INSTANCE_NOT_WAITING,
                f"实例 {instance_id} 不处于等待审核状态（当前状态：{instance.status.value}）",
            )
        instance.status = InstanceStatus.RUNNING
        return instance

    # ------------------------------------------------------------------
    # 内部方法：LangGraph 图构建与执行
    # ------------------------------------------------------------------
    async def _execute_graph(
        self,
        compiled: CompiledWorkflow,
        initial_state: SharedState,
        instance: ExecutionInstance,
    ) -> SharedState:
        """构建 LangGraph StateGraph 并驱动执行（三类子集拓扑，M1~M4）。

        此方法是唯一直接调用 LangGraph API 的位置。
        """
        # 等价子集硬边界校验（五项排除的运行期安全网，E-矩阵 N/A 口径一致）
        self._validate_subset(compiled)

        # 延迟导入 langgraph——仅在此方法内引用，确保隔离（HC-03）
        from langgraph.graph import END, START, StateGraph
        from typing import TypedDict

        # 定义框架状态类型（动态构建，避免模块级 langgraph 依赖）；
        # next_node_id 为瞬态路由键（design §4.4），不出 adapter
        class FrameworkState(TypedDict, total=False):
            instance_id: str
            node_outputs: Dict[str, Any]
            loop_counters: Dict[str, int]
            branch_selections: Dict[str, str]
            current_node_id: Optional[str]
            round_index: int
            next_node_id: Optional[str]

        def _route_next(state: Dict[str, Any]):
            """条件路由：读瞬态键 next_node_id（决策在节点函数内完成，DC-B4-02）。"""
            return state.get(_KEY_NEXT_NODE_ID) or END

        # 构建 StateGraph
        graph = StateGraph(FrameworkState)

        # 为每个节点注册真实执行函数（节点语义单源：同一执行器注册表实例）
        instance_id = instance.instance_id
        trace_id = instance.trace_id or _new_id("trace")
        for node in compiled.nodes:
            graph.add_node(
                node.node_id,
                self._make_node_function(node, compiled, instance_id, trace_id),
            )

        # M2/M3：控制流条件分支/循环节点 → add_conditional_edges 路由
        # （决策在节点函数内完成，声明序/默认回退/计数上限/回边与 scheduler 一致）
        routed_sources = set()
        for node in compiled.nodes:
            if node.node_type != NodeType.CONTROL_FLOW:
                continue
            kind = getattr(node.configuration, "control_flow_kind", None)
            if kind in (ControlFlowKind.CONDITIONAL_BRANCH, ControlFlowKind.LOOP):
                graph.add_conditional_edges(node.node_id, _route_next)
                routed_sources.add(node.node_id)

        # M1/M4：普通节点取首声明出边（多出边取首边，与 scheduler 口径一致；
        # 控制流已路由的出边不重复平铺——旧桩全边平铺即并行扇出，语义错误）
        seen_plain_sources = set()
        for edge in compiled.edges:
            if edge.source_node_id in routed_sources:
                continue
            if edge.source_node_id in seen_plain_sources:
                continue
            graph.add_edge(edge.source_node_id, edge.target_node_id)
            seen_plain_sources.add(edge.source_node_id)

        # 设置入口边
        graph.add_edge(START, compiled.entry_node_id)

        # 终点节点连接到 END
        for terminal_id in compiled.terminal_node_ids:
            graph.add_edge(terminal_id, END)

        # 编译图
        compiled_graph = graph.compile()

        # 执行图
        initial_dict = LangGraphStateMapping.to_framework_state(initial_state)
        result = await compiled_graph.ainvoke(initial_dict)

        # 将结果转换回 SharedState（瞬态键 next_node_id 不出 adapter）
        return LangGraphStateMapping.from_framework_state(result)

    # ------------------------------------------------------------------
    # 节点执行函数（真实化，DC-B4-02 节点语义单源）
    # ------------------------------------------------------------------
    def _make_node_function(
        self,
        node,
        compiled: CompiledWorkflow,
        instance_id: str,
        trace_id: str,
    ):
        """为 LangGraph 创建节点执行函数。

        真实化（arch_review_gap_b4_architecture design §4.3）：
        1. executor = registry.get_executor(node.node_type)——节点语义单源；
        2. NodeExecutionContext 同 Scheduler._execute_node 口径构造；
        3. 超时控制同口径（asyncio.wait_for，超时映射 NODE_TIMEOUT）；
        4. 失败 → NodeExecutionError（result.error_code or WF_NODE_EXECUTION_FAILED 同回退链）；
        5. 命名输出写入 node_outputs（成功且 output_value 非 None 时）；
        6. 决策：_decide_next 复刻 scheduler 声明序/默认回退/计数上限/回边规程；
        7. 返回全量状态字典增量（plain TypedDict 替换语义，防止历史输出丢失）。
        """

        async def node_fn(state: Dict[str, Any]) -> Dict[str, Any]:
            shared = LangGraphStateMapping.from_framework_state(state)
            executor = self._registry.get_executor(node.node_type)

            timeout = node.timeout_seconds or self._settings.node_default_timeout_seconds
            round_index = int(state.get(_KEY_ROUND_INDEX, 0) or 0)
            context = NodeExecutionContext(
                node_definition=node,
                shared_state=shared,
                instance_id=instance_id,
                trace_id=trace_id,
                timeout_seconds=timeout,
                round_index=round_index,
                node_definitions={n.node_id: n for n in compiled.nodes},
            )

            result = await self._execute_with_timeout(executor, context, timeout)

            if not result.success:
                raise NodeExecutionError(
                    result.error_code or WF_NODE_EXECUTION_FAILED,
                    f"节点 {node.node_id} 执行失败：{result.error_message}",
                )

            # 命名输出写入（先于决策：分支/循环条件可引用本节点输出，与 scheduler 一致）
            if result.output_name and result.output_value is not None:
                shared.write_output(result.output_name, result.output_value)

            next_node_id, bookkeeping = self._decide_next(compiled, node, shared)

            update: Dict[str, Any] = {
                _KEY_NODE_OUTPUTS: dict(shared.node_outputs),
                _KEY_LOOP_COUNTERS: dict(shared.loop_counters),
                _KEY_BRANCH_SELECTIONS: dict(shared.branch_selections),
                _KEY_CURRENT_NODE_ID: node.node_id,
                _KEY_ROUND_INDEX: round_index + 1,
                _KEY_NEXT_NODE_ID: next_node_id,
            }
            update.update(bookkeeping)
            return update

        return node_fn

    async def _execute_with_timeout(
        self, executor, context: NodeExecutionContext, timeout: int
    ) -> NodeExecutionResult:
        """执行器调用 + 超时控制（与 Scheduler._execute_node 同口径）。"""
        try:
            asyncio.get_running_loop()
            return await asyncio.wait_for(executor.execute(context), timeout=timeout)
        except RuntimeError:
            # 无运行中事件循环（同步测试场景）：直接调用执行器
            return await executor.execute(context)
        except asyncio.TimeoutError:
            return NodeExecutionResult(
                success=False,
                error_code=NODE_TIMEOUT,
                error_message=(
                    f"节点 {context.node_definition.node_id} 执行超时（{timeout} 秒）"
                ),
            )

    # ------------------------------------------------------------------
    # 下一节点决策（DC-B4-02：scheduler._determine_next_node 规程复刻）
    # ------------------------------------------------------------------
    def _decide_next(
        self, compiled: CompiledWorkflow, node, shared: SharedState
    ):
        """确定下一节点（确定性调度，规程与 scheduler._determine_next_node 一致）。

        返回 (next_node_id, bookkeeping)：bookkeeping 为分支选择/循环计数的
        状态簿记增量（与 SharedStateManager 写入口径一致）。
        """
        if node.node_type == NodeType.CONTROL_FLOW:
            config = node.configuration
            kind = getattr(config, "control_flow_kind", None)

            if kind == ControlFlowKind.CONDITIONAL_BRANCH:
                return self._eval_branch_next(compiled, node, shared)

            if kind == ControlFlowKind.LOOP:
                return self._eval_loop_next(compiled, node, shared)

        # 普通节点：取第一条出边的目标（按声明顺序，保证确定性）
        edges = compiled.successors(node.node_id)
        if not edges:
            return None, {}
        return edges[0].target_node_id, {}

    def _eval_branch_next(
        self, compiled: CompiledWorkflow, node, shared: SharedState
    ):
        """条件分支求值（scheduler._eval_branch 规程复刻）。

        按声明顺序求值条件，首个为真者对应的 target 即为后继；
        无匹配且无默认分支 → WF_VALIDATION_FAILED（同码）。
        """
        config = node.configuration
        conditions = getattr(config, "conditions", [])
        default_target = getattr(config, "default_branch_target", None)

        for condition in conditions:
            try:
                if self._expression_evaluator.evaluate(condition.expression, shared):
                    return condition.target_node_id, {
                        _KEY_BRANCH_SELECTIONS: {
                            **shared.branch_selections,
                            node.node_id: condition.target_node_id,
                        },
                    }
            except DomainError:
                continue

        if default_target:
            return default_target, {
                _KEY_BRANCH_SELECTIONS: {
                    **shared.branch_selections,
                    node.node_id: default_target,
                },
            }

        raise DomainError(
            WF_VALIDATION_FAILED,
            f"条件分支节点 {node.node_id} 无匹配分支且未配置默认分支",
        )

    def _eval_loop_next(
        self, compiled: CompiledWorkflow, node, shared: SharedState
    ):
        """循环控制（scheduler._eval_loop 规程复刻）。

        满足终止条件 → 退出循环，取非回边出边；未满足且未达上限 →
        loop_body_entry 继续循环（计数递增）；达上限仍未满足 →
        WF_LOOP_NOT_CONVERGED（同码）。
        """
        config = node.configuration
        termination_condition = getattr(config, "loop_termination_condition", "")
        loop_body_entry = getattr(config, "loop_body_entry", "")
        max_iterations = (
            getattr(config, "max_iterations", None) or self._settings.loop_max_iterations
        )

        counter = shared.loop_counters.get(node.node_id, 0)

        # 检查终止条件
        if termination_condition:
            try:
                if self._expression_evaluator.evaluate(termination_condition, shared):
                    # 终止条件满足，退出循环——取非回边出边
                    for edge in compiled.successors(node.node_id):
                        if edge.target_node_id != loop_body_entry:
                            return edge.target_node_id, {}
                    return None, {}
            except DomainError:
                pass

        # 检查最大迭代次数
        if counter >= max_iterations:
            raise DomainError(
                WF_LOOP_NOT_CONVERGED,
                f"循环节点 {node.node_id} 达到最大迭代次数 {max_iterations} 但未满足终止条件",
            )

        # 继续循环（计数递增簿记）
        return loop_body_entry, {
            _KEY_LOOP_COUNTERS: {
                **shared.loop_counters,
                node.node_id: counter + 1,
            },
        }

    # ------------------------------------------------------------------
    # 等价子集硬边界校验（五项排除的运行期安全网）
    # ------------------------------------------------------------------
    def _validate_subset(self, compiled: CompiledWorkflow) -> None:
        """校验编译产物落在等价子集内（三类拓扑 + 五项排除）。

        越界即 WF_VALIDATION_FAILED——不静默误执行（排除项的正确语义是
        并行扇出/挂起/子编排，平铺边即语义错误）。边界与 E-矩阵 N/A 口径一致。
        """
        for node in compiled.nodes:
            if node.node_type in (NodeType.SUBWORKFLOW, NodeType.SUPERVISOR):
                raise DomainError(
                    WF_VALIDATION_FAILED,
                    f"节点 {node.node_id} 类型 {node.node_type.value} 不在 LangGraph "
                    f"等价子集内（子工作流嵌套/监督者动态子编排，排除⑤）",
                )
            if node.node_type == NodeType.CONTROL_FLOW:
                kind = getattr(node.configuration, "control_flow_kind", None)
                if kind == ControlFlowKind.PARALLEL_JOIN:
                    raise DomainError(
                        WF_VALIDATION_FAILED,
                        f"控制流节点 {node.node_id} 并行汇聚不在 LangGraph 等价子集内"
                        f"（并行扇出/汇聚，排除①）",
                    )
                if kind == ControlFlowKind.HUMAN_REVIEW_GATE:
                    raise DomainError(
                        WF_VALIDATION_FAILED,
                        f"控制流节点 {node.node_id} 人工审核门不在 LangGraph 等价子集内"
                        f"（HITL 挂起，排除②）",
                    )
