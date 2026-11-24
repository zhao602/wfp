# =============================================================================
# 子工作流节点执行器（engine/nodes/subworkflow_node_executor.py）
#
# 职责：实现 SUBWORKFLOW 节点类型，通过递归调用引擎实现子工作流嵌套。
# 设计意图（对应 spec 5.1.1.5~5.1.1.8、design 2.4.2、任务 T26）：
#   1. 从仓储载入子工作流定义，递归编译并执行；
#   2. 输入输出映射——按配置从父状态提取字段构造子工作流输入，完成后写回；
#   3. 嵌套深度限制——超过 max_nesting_depth 返回 WF_SUBWORKFLOW_DEPTH_EXCEEDED；
#   4. 上下文传递策略——INHERIT 共享只读视图，ISOLATE 深拷贝隔离；
#   5. 事件嵌套——SUBWORKFLOW_STARTED / SUBWORKFLOW_COMPLETED + subworkflow_id + nesting_depth。
# =============================================================================

import time
import uuid
from typing import Any, Dict

from orchestration_platform.domain.errors import (
    WF_SUBWORKFLOW_DEPTH_EXCEEDED,
    WF_SUBWORKFLOW_INPUT_MAPPING_FAILED,
    WF_SUBWORKFLOW_NOT_FOUND,
)
from orchestration_platform.domain.models.enums import EventType, NodeType
from orchestration_platform.domain.models.node_configurations import SubWorkflowNodeConfiguration
from orchestration_platform.engine.input_assembly import (
    AssemblyFailure,
    AssemblyOutcome,
    assemble_keyed_mapping,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)


class SubWorkflowNodeExecutor:
    """子工作流节点执行器：递归调用另一工作流定义。

    构造注入：
        workflow_engine —— 引擎实例（用于编译与启动子工作流）
        workflow_repository —— 工作流定义仓储（载入子定义）
        event_repository —— 事件仓储（记录嵌套事件）
    """

    def __init__(
        self,
        workflow_engine: Any = None,
        workflow_repository: Any = None,
        event_repository: Any = None,
    ) -> None:
        self._engine = workflow_engine
        self._workflow_repo = workflow_repository
        self._event_repo = event_repository
        self._compile_cache: Dict[str, Any] = {}

    @property
    def node_type(self) -> NodeType:
        return NodeType.SUBWORKFLOW

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行子工作流节点。"""
        start_time = time.perf_counter()
        config: SubWorkflowNodeConfiguration = context.node_definition.configuration  # type: ignore

        # 1. 嵌套深度检查
        current_depth = context.subworkflow_depth
        if current_depth >= config.max_nesting_depth:
            return NodeExecutionResult(
                success=False,
                error_code=WF_SUBWORKFLOW_DEPTH_EXCEEDED,
                error_message=(
                    f"子工作流嵌套深度 {current_depth} 超过上限 {config.max_nesting_depth}"
                ),
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )

        # 2. 载入子工作流定义
        sub_definition = self._load_subworkflow(config)
        if sub_definition is None:
            return NodeExecutionResult(
                success=False,
                error_code=WF_SUBWORKFLOW_NOT_FOUND,
                error_message=f"子工作流定义不存在: {config.subworkflow_definition_id}",
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )

        # 3. 输入映射（统一装配，unified_node_input T3.3：
        #    「键在值为 null → 传递 null」既有语义由装配四态天然保持）
        input_outcome = self._map_inputs(config, context)
        if isinstance(input_outcome, AssemblyFailure):
            return NodeExecutionResult(
                success=False,
                error_code=input_outcome.error_code,
                error_message=f"子工作流输入映射失败：{input_outcome.error_message}",
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )
        sub_inputs = input_outcome.values

        # 4. 生成子工作流实例标识
        subworkflow_id = f"sub_{uuid.uuid4().hex[:12]}"

        # 5. 记录启动事件
        self._emit_event(
            instance_id=context.instance_id,
            event_type=EventType.SUBWORKFLOW_STARTED,
            node_id=context.node_definition.node_id,
            subworkflow_id=subworkflow_id,
            nesting_depth=current_depth + 1,
            payload={"subworkflow_definition_id": config.subworkflow_definition_id},
        )

        # 6. 递归编译（带缓存）
        compiled = self._compile_subworkflow(sub_definition)

        # 7. 执行子工作流（委托引擎）
        sub_result = await self._execute_subworkflow(
            compiled, sub_inputs, subworkflow_id, current_depth + 1
        )

        # 8. 输出映射回父状态
        output_value = self._map_outputs(config, sub_result)

        # 9. 记录完成事件
        self._emit_event(
            instance_id=context.instance_id,
            event_type=EventType.SUBWORKFLOW_COMPLETED,
            node_id=context.node_definition.node_id,
            subworkflow_id=subworkflow_id,
            nesting_depth=current_depth + 1,
            payload={"success": sub_result.get("success", False)},
        )

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.output_name,
            output_value=output_value,
            duration_ms=duration_ms,
            metadata={
                "subworkflow_id": subworkflow_id,
                "nesting_depth": current_depth + 1,
            },
        )

    def _load_subworkflow(self, config: SubWorkflowNodeConfiguration) -> Any:
        """从仓储载入子工作流定义。"""
        if self._workflow_repo is None:
            return None
        try:
            version = config.subworkflow_version
            return self._workflow_repo.load(config.subworkflow_definition_id, version)
        except Exception:
            return None

    def _map_inputs(
        self, config: SubWorkflowNodeConfiguration, context: NodeExecutionContext
    ) -> AssemblyOutcome:
        """按 input_mapping 从父状态提取字段构造子工作流输入（统一装配，T3.3）。

        装配失败返回 AssemblyFailure（键值缺失/语法非法 → 既有码；
        路径类型不匹配 → 统一码）；「键在值为 null → 传递 null」既有语义
        由装配四态天然保持。
        """
        snapshot = dict(context.shared_state.node_outputs)
        return assemble_keyed_mapping(
            config.input_mapping,
            snapshot,
            error_code=WF_SUBWORKFLOW_INPUT_MAPPING_FAILED,
            node_definitions=context.node_definitions,
        )

    def _compile_subworkflow(self, sub_definition: Any) -> Any:
        """递归编译子工作流（按 definition_id+version 缓存）。"""
        cache_key = f"{sub_definition.definition_id}:{sub_definition.definition_version}"
        if cache_key not in self._compile_cache:
            if self._engine is not None:
                self._compile_cache[cache_key] = self._engine.compile(sub_definition)
            else:
                self._compile_cache[cache_key] = sub_definition
        return self._compile_cache[cache_key]

    async def _execute_subworkflow(
        self, compiled: Any, inputs: Dict[str, Any], subworkflow_id: str, depth: int
    ) -> Dict[str, Any]:
        """执行子工作流并返回输出。"""
        if self._engine is None:
            return {"success": True, "outputs": inputs}
        try:
            result = await self._engine.start(compiled, inputs)
            return {"success": True, "outputs": result}
        except Exception as exc:
            return {"success": False, "error": str(exc), "outputs": {}}

    def _map_outputs(
        self, config: SubWorkflowNodeConfiguration, sub_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """按 output_mapping 将子工作流输出映射回父状态。"""
        outputs = sub_result.get("outputs", {})
        mapped: Dict[str, Any] = {}
        for parent_field, sub_output_name in config.output_mapping.items():
            if isinstance(outputs, dict):
                mapped[parent_field] = outputs.get(sub_output_name)
        return mapped

    def _emit_event(
        self,
        instance_id: str,
        event_type: EventType,
        node_id: str,
        subworkflow_id: str,
        nesting_depth: int,
        payload: Dict[str, Any],
    ) -> None:
        """记录子工作流嵌套事件。"""
        if self._event_repo is None:
            return
        try:
            from orchestration_platform.domain.models.execution_event import ExecutionEvent
            event = ExecutionEvent(
                event_id=f"evt_{uuid.uuid4().hex[:12]}",
                instance_id=instance_id,
                event_type=event_type,
                node_id=node_id,
                sequence=0,
                payload=payload,
                subworkflow_id=subworkflow_id,
                nesting_depth=nesting_depth,
            )
            self._event_repo.append(event)
        except Exception:
            pass
