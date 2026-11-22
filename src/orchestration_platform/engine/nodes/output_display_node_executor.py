# =============================================================================
# 输出展示节点执行器（engine/nodes/output_display_node_executor.py）
#
# 职责：实现 OUTPUT_DISPLAY 节点类型，读取上游命名输出并原样透出。
# 设计意图（对应 add_output_display_node spec.md / design.md DC-3/DC-5）：
#   1. 纯内存读取（shared_state.read_output），无文件/网络/数据库 I/O；
#   2. 引用缺失时以空值成功完成（Q-1，与 DataTransformNodeExecutor 空值先例一致）；
#   3. 引用留空时读取 node_outputs 快照作为上游全部命名输出汇总（Q-2）；
#   4. 对读取值不做任何映射/过滤/聚合，直接透传（spec 5.2.1.3）；
#   5. 命名输出写回由调度器统一执行，执行器自身不写共享状态（INV-6/INV-7）；
#   6. 引用集合多选：逐条容错聚合为「引用原文 → 值」映射（OP-06/CHG-04）；
#      恰 1 条仍走既有单引用路径，结果与扩展前逐字节一致（AC-09）。
# =============================================================================

import time
from typing import Optional

from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    OutputDisplayNodeConfiguration,
    normalize_output_references,
)
from orchestration_platform.engine.input_assembly import (
    AssemblyFailure,
    assemble_reference_collection,
    assemble_single_reference,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)


class OutputDisplayNodeExecutor:
    """输出展示节点执行器：读取上游命名输出并原样透出。"""

    @property
    def node_type(self) -> NodeType:
        return NodeType.OUTPUT_DISPLAY

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """读取上游输出并原样透传（纯内存读取，无 I/O）。"""
        start_time = time.perf_counter()
        config: OutputDisplayNodeConfiguration = context.node_definition.configuration  # type: ignore

        output_value = self._read_output_value(config, context)

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.output_name,
            output_value=output_value,
            duration_ms=duration_ms,
        )

    def _read_output_value(
        self, config: OutputDisplayNodeConfiguration, context: NodeExecutionContext
    ) -> Optional[JsonValue]:
        """按引用分支读取输出值（透传，不转换；CHG-04 三分支）。

        引用集合为空：返回 node_outputs 快照原值（上游全部命名输出，Q-2）；
        恰 1 条：走既有 assemble_single_reference 容错特例
        （unified_node_input T3.5，missing_is_none=True，缺失/类型不匹配均以
        None 占位成功，Q-1 既有容错语义不回退）——结构性保证单条结果与
        扩展前逐字节一致（DC-06/AC-09）；
        多条：走 assemble_reference_collection 逐条容错聚合，返回
        「引用原文 → 值」键值映射（OP-06，保序）。
        """
        refs = normalize_output_references(config.input_reference)
        if not refs:
            return context.shared_state.node_outputs
        snapshot = dict(context.shared_state.node_outputs)
        if len(refs) == 1:
            outcome = assemble_single_reference(
                refs[0],
                snapshot,
                missing_is_none=True,
                node_definitions=context.node_definitions,
            )
            return None if isinstance(outcome, AssemblyFailure) else outcome.value
        outcome = assemble_reference_collection(refs, snapshot, missing_is_none=True)
        return None if isinstance(outcome, AssemblyFailure) else outcome.values