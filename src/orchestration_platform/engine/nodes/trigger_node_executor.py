# =============================================================================
# 触发器节点执行器（engine/nodes/trigger_node_executor.py）
#
# 职责：运行时仅作入口标记，支持手动/定时/数据事件三类触发方式。
# 设计意图（对应 spec 5.3.1.10-12）：
#   1. 声明式节点——运行时不执行业务逻辑，仅标记入口；
#   2. 参数模板映射——将触发上下文映射为实例输入，映射失败返回明确错误；
#   3. 模板取值来源为 context.shared_state.node_outputs 的顶层键（触发上下文载体，
#      其初值为实例启动入参）；「起始节点无上游产出」不等于「上下文为空」。
# =============================================================================

from orchestration_platform.domain.errors import TRIGGER_PAYLOAD_MAPPING_FAILED
from orchestration_platform.domain.models.enums import NodeType, TriggerType
from orchestration_platform.domain.models.node_configurations import TriggerNodeConfiguration
from orchestration_platform.engine.input_assembly import (
    AssemblyFailure,
    assemble_keyed_mapping,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)


class TriggerNodeExecutor:
    """触发器节点执行器：入口标记 + 参数模板映射。"""

    @property
    def node_type(self) -> NodeType:
        return NodeType.TRIGGER

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行触发器节点：映射触发上下文为实例输入。

        非空模板时输出 = 触发上下文经模板收敛的结果（全有或全无，任一引用
        字段缺失即整体失败）；空模板时输出 = 入口标记（trigger_type）。
        """
        config: TriggerNodeConfiguration = context.node_definition.configuration

        # 参数模板映射：将触发上下文字段引用映射为实例输入
        # （统一装配，unified_node_input T3.1：基线顶格键与既有 .get() 读取等价；
        #   「键在值为 null → 正常传递 null」为登记的行为修正点，spec §9.3）
        if config.input_template:
            snapshot = dict(context.shared_state.node_outputs)
            outcome = assemble_keyed_mapping(
                config.input_template,
                snapshot,
                error_code=TRIGGER_PAYLOAD_MAPPING_FAILED,
                node_definitions=context.node_definitions,
            )
            if isinstance(outcome, AssemblyFailure):
                return NodeExecutionResult(
                    success=False,
                    error_code=outcome.error_code,
                    error_message=f"触发参数映射失败，{outcome.error_message}",
                )

            return NodeExecutionResult(
                success=True,
                output_name=context.node_definition.effective_output_name,
                output_value=outcome.values,
            )

        # 无模板时仅标记入口成功
        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.effective_output_name,
            output_value={"trigger_type": config.trigger_type.value},
        )
