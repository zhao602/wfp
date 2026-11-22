# =============================================================================
# 数据转换节点执行器（engine/nodes/data_transform_node_executor.py）
#
# 职责：实现 DATA_TRANSFORM 节点类型，对数据集执行操作链并返回结果。
# 设计意图（expand_data_transform_ops design §2.1.3，CHG-B15）：
#   1. 主输入经 assemble_single_reference(expected_kind="dataset") 装配，
#      unified DC-10 四态收窄语义不变（不得回退）；
#   2. 经 normalize_operations 取链（唯一权威解释入口，DC-01）；
#      链步数超上限 → fail-fast DATA_TRANSFORM_CHAIN_TOO_LONG（INV-15）；
#   3. 逐步骤经注册表分派：未注册操作 → fail-fast DATA_TRANSFORM_UNKNOWN_OPERATION
#      （无静默降级）；步失败（failure_code）→ fail-fast 且含步骤定位
#      「步骤 #{index}（{operation}）」（OP-12，静默吞异常路径已删除）；
#   4. 可降级步（degraded=True）置空 + 计数告警累加至 metadata.warnings，不中断链；
#   5. 上一步输出 = 下一步输入（串行顺序）；
#   6. 链尾按 output_format 投影值形态（DC-04：缺省/None ≡ dataset 原样返回，
#      逐字等价；命名输出键仍为 output_name，INV-10 仅值形态改变）。
# 既有接口签名不变（INV-01）；map/filter 表达式仍受求值器白名单约束（INV-14）。
# =============================================================================

import time
from typing import Any, Dict, List

from orchestration_platform.domain.errors import (
    DATA_TRANSFORM_CHAIN_TOO_LONG,
    DATA_TRANSFORM_UNKNOWN_OPERATION,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    DataTransformNodeConfiguration,
    normalize_operations,
)
from orchestration_platform.engine.input_assembly import (
    AssemblyFailure,
    assemble_single_reference,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.engine.nodes.data_transform_ops import (
    MAX_CHAIN_STEPS,
    StepContext,
    get_handler,
)
from orchestration_platform.engine.nodes.data_transform_ops.output_format import (
    OutputFormatProjector,
)


class DataTransformNodeExecutor:
    """数据转换节点执行器：对数据集执行操作链。"""

    @property
    def node_type(self) -> NodeType:
        return NodeType.DATA_TRANSFORM

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行数据转换操作链。"""
        start_time = time.perf_counter()
        config: DataTransformNodeConfiguration = context.node_definition.configuration  # type: ignore
        node_id = context.node_definition.node_id

        # 输入数据集装配（统一装配，unified_node_input DC-10 四态收窄）：
        # 键缺失 → NODE_INPUT_REFERENCE_MISSING（收窄：现状静默 success+空数据集）；
        # 存在但非 list（含 null）→ NODE_INPUT_TYPE_MISMATCH（收窄）；
        # 空列表 → 正常装配空数据集（保持，FR-14/AC-06）；list → 正常装配（保持）。
        snapshot = dict(context.shared_state.node_outputs)
        input_outcome = assemble_single_reference(
            config.input_dataset_reference,
            snapshot,
            expected_kind="dataset",
            node_definitions=context.node_definitions,
        )
        if isinstance(input_outcome, AssemblyFailure):
            return self._failure(
                node_id,
                input_outcome.error_code,
                f"输入装配失败：{input_outcome.error_message}",
                start_time,
            )
        dataset: List[Dict[str, Any]] = input_outcome.value

        # 链归一化（DC-01 唯一权威）与步数上限（INV-15）
        steps = normalize_operations(config)
        if len(steps) > MAX_CHAIN_STEPS:
            return self._failure(
                node_id,
                DATA_TRANSFORM_CHAIN_TOO_LONG,
                f"操作链步数 {len(steps)} 超上限 {MAX_CHAIN_STEPS}",
                start_time,
            )

        # 串行执行：上一步输出 = 下一步输入（design §2.1.3 链式执行流程）
        warnings: List[str] = []
        for step_index, step in enumerate(steps):
            handler = get_handler(step.operation)
            if handler is None:
                return self._failure(
                    node_id,
                    DATA_TRANSFORM_UNKNOWN_OPERATION,
                    f"步骤 #{step_index}（{step.operation.value}）：操作未注册",
                    start_time,
                )

            outcome = handler.apply(
                StepContext(
                    step_index=step_index,
                    step=step,
                    left_dataset=dataset,
                    shared_state_snapshot=snapshot,
                )
            )

            if outcome.failure_code is not None:
                return self._failure(
                    node_id,
                    outcome.failure_code,
                    (
                        f"步骤 #{step_index}（{step.operation.value}）："
                        f"{outcome.failure_message or '步骤执行失败'}"
                    ),
                    start_time,
                )

            if outcome.warnings:
                warnings.extend(outcome.warnings)
            dataset = outcome.value

        metadata: Dict[str, Any] = {}
        if warnings:
            metadata["warnings"] = warnings

        # 链尾输出格式投影（DC-04）：缺省/None ≡ dataset 原样返回（逐字等价）；
        # 命名输出键仍为 output_name（INV-10，仅值形态改变）
        output_value = OutputFormatProjector.project(
            dataset, config.output_format, config.output_format_params
        )

        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.output_name,
            output_value=output_value,
            duration_ms=int((time.perf_counter() - start_time) * 1000),
            metadata=metadata,
        )

    @staticmethod
    def _failure(
        node_id: str,
        error_code: str,
        message: str,
        start_time: float,
    ) -> NodeExecutionResult:
        """构造 fail-fast 结果（错误消息含节点定位，步失败另含步骤定位）。"""
        return NodeExecutionResult(
            success=False,
            error_code=error_code,
            error_message=f"数据转换节点 {node_id} {message}",
            duration_ms=int((time.perf_counter() - start_time) * 1000),
        )
