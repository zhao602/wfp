# =============================================================================
# 动态注入节点执行器（engine/nodes/dynamic_injector_node_executor.py）
#
# 职责：实现 DYNAMIC_INJECTOR 节点类型，运行时按条件求值动态生成节点实例。
# 设计意图（对应 spec 5.1.1.9~5.1.1.12、design 2.4.3、任务 T27）：
#   1. 复用 RestrictedExpressionEvaluator 安全求值注入条件；
#   2. 按 NodeTemplate 实例化新节点，标识格式 {template_id}_{seq} 保证唯一；
#   3. 幂等保证——同一轮次同一条件多次求值为真时仅注入一次；
#   4. 注入数量上限——超过 max_injection_count 停止注入并告警；
#   5. 注入事件——NODE_INJECTED 含注入节点标识与模板标识。
# =============================================================================

import hashlib
import time
import uuid
from typing import Any, List, Set

from orchestration_platform.domain.models.enums import EventType, NodeType
from orchestration_platform.domain.models.node_configurations import (
    DynamicInjectorNodeConfiguration,
    NodeTemplate,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)


class DynamicInjectorNodeExecutor:
    """动态注入节点执行器：运行时按条件生成节点实例并接入调度图。

    构造注入：
        event_repository —— 事件仓储（记录注入事件）
        compiled_workflow —— 编译后工作流（用于 inject_node/inject_edge）
    """

    def __init__(
        self,
        event_repository: Any = None,
        compiled_workflow: Any = None,
    ) -> None:
        self._event_repo = event_repository
        self._compiled_workflow = compiled_workflow
        self._injected_keys: Set[str] = set()
        self._injected_count: int = 0

    @property
    def node_type(self) -> NodeType:
        return NodeType.DYNAMIC_INJECTOR

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行动态注入节点。"""
        start_time = time.perf_counter()
        config: DynamicInjectorNodeConfiguration = context.node_definition.configuration  # type: ignore

        # 1. 注入条件求值（复用受限表达式求值器）
        should_inject = self._evaluate_condition(
            config.injection_condition, context.shared_state
        )

        if not should_inject:
            return NodeExecutionResult(
                success=True,
                output_name=context.node_definition.output_name,
                output_value={"injected_count": 0},
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )

        # 2. 检查注入数量上限
        if self._injected_count >= config.max_injection_count:
            return NodeExecutionResult(
                success=True,
                output_name=context.node_definition.output_name,
                output_value={
                    "injected_count": 0,
                    "warning": "已达注入数量上限，跳过本次注入",
                },
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )

        # 3. 幂等检查
        round_index = context.round_index
        condition_hash = hashlib.sha256(
            config.injection_condition.encode("utf-8")
        ).hexdigest()[:16]
        idempotency_key = f"{context.node_definition.node_id}:{condition_hash}:{round_index}"

        if idempotency_key in self._injected_keys:
            return NodeExecutionResult(
                success=True,
                output_name=context.node_definition.output_name,
                output_value={"injected_count": 0, "reason": "幂等跳过"},
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )

        self._injected_keys.add(idempotency_key)

        # 4. 实例化并注入节点模板
        injected_node_ids: List[str] = []
        for template in config.node_templates:
            if self._injected_count >= config.max_injection_count:
                break

            injected_id = self._instantiate_template(template)
            if injected_id is None:
                continue

            injected_node_ids.append(injected_id)
            self._injected_count += 1

            # 记录注入事件
            self._emit_injection_event(
                instance_id=context.instance_id,
                source_node_id=context.node_definition.node_id,
                injected_node_id=injected_id,
                template_id=template.template_id,
                condition=config.injection_condition,
                round_index=round_index,
            )

            # 注入到编译工作流
            self._inject_into_workflow(injected_id, config.target_position)

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.output_name,
            output_value={
                "injected_count": len(injected_node_ids),
                "injected_node_ids": injected_node_ids,
            },
            duration_ms=duration_ms,
        )

    def _evaluate_condition(self, expression: str, shared_state: Any) -> bool:
        """安全求值注入条件表达式。

        复用调度器的 RestrictedExpressionEvaluator，仅允许比较与逻辑运算。
        """
        try:
            from orchestration_platform.engine.scheduler import (
                RestrictedExpressionEvaluator,
            )

            evaluator = RestrictedExpressionEvaluator()
            # DC-09 传参修正（unified_node_input T3.6）：求值器签名接收 SharedState
            # （内部访问 state.node_outputs 属性）；修正前传 dict 触发 AttributeError
            # 被吞 → 含变量表达式恒 False；修正后 injection_condition 引用真正生效。
            result = evaluator.evaluate(expression, shared_state)
            return bool(result)
        except Exception:
            return False

    def _instantiate_template(self, template: NodeTemplate) -> Any:
        """按模板实例化新节点，标识格式 {template_id}_{seq}。"""
        seq = self._injected_count + 1
        node_id = f"{template.template_id}_{seq}"
        return node_id

    def _inject_into_workflow(self, node_id: str, target_position: Any) -> None:
        """将注入节点接入编译工作流。

        若编译工作流支持 inject_node/inject_edge 方法则调用，
        否则跳过（实际注入由调度器在下一轮处理）。
        """
        if self._compiled_workflow is None:
            return
        inject_node = getattr(self._compiled_workflow, "inject_node", None)
        if inject_node is not None:
            try:
                inject_node(node_id)
                if target_position is not None:
                    inject_edge = getattr(self._compiled_workflow, "inject_edge", None)
                    if inject_edge is not None:
                        inject_edge(node_id, target_position)
            except Exception:
                pass

    def _emit_injection_event(
        self,
        instance_id: str,
        source_node_id: str,
        injected_node_id: str,
        template_id: str,
        condition: str,
        round_index: int,
    ) -> None:
        """记录 NODE_INJECTED 事件。"""
        if self._event_repo is None:
            return
        try:
            from orchestration_platform.domain.models.execution_event import ExecutionEvent

            event = ExecutionEvent(
                event_id=f"evt_{uuid.uuid4().hex[:12]}",
                instance_id=instance_id,
                event_type=EventType.NODE_INJECTED,
                node_id=source_node_id,
                sequence=0,
                payload={
                    "injected_node_id": injected_node_id,
                    "template_id": template_id,
                    "condition": condition,
                    "round_index": round_index,
                },
            )
            self._event_repo.append(event)
        except Exception:
            pass

    def reset(self) -> None:
        """重置注入状态（新实例执行前调用）。"""
        self._injected_keys.clear()
        self._injected_count = 0
