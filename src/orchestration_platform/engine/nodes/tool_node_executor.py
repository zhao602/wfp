# =============================================================================
# Tool 节点执行器（engine/nodes/tool_node_executor.py）
#
# 职责：通过 ToolInvoker 调用本地函数或 MCP 工具，确定性调用。
# 设计意图（对应 spec 5.3.1.5-6、design 2.5.3）：
#   1. 相同入参返回相同结果（无状态）；
#   2. MCP 超时 → NODE_TOOL_TIMEOUT，工具不存在 → NODE_TOOL_NOT_FOUND，
#      参数不匹配 → NODE_TOOL_ARGUMENT_MISMATCH（不重试）。
# =============================================================================

from typing import Dict, Optional

from orchestration_platform.domain.errors import (
    NODE_TOOL_ARGUMENT_MISMATCH,
    NODE_TOOL_NOT_FOUND,
    NODE_TOOL_TIMEOUT,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import ToolNodeConfiguration
from orchestration_platform.engine.input_assembly import (
    AssemblyFailure,
    assemble_keyed_mapping,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.integrations.abstractions.external_clients import (
    IToolInvoker,
)


class ToolNodeExecutor:
    """Tool 节点执行器：确定性工具调用。"""

    def __init__(self, tool_invokers: Optional[Dict[str, IToolInvoker]] = None) -> None:
        """构造 Tool 执行器。

        参数：
            tool_invokers —— 工具标识 -> 调用器实例 的映射；
                             缺省为空，需通过 register_tool 登记。
        """
        self._invokers: Dict[str, IToolInvoker] = tool_invokers or {}

    def register_tool(self, invoker: IToolInvoker) -> None:
        """登记工具调用器。"""
        self._invokers[invoker.tool_identifier] = invoker

    @property
    def node_type(self) -> NodeType:
        return NodeType.TOOL

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行 Tool 节点：调用工具并返回结果。"""
        config: ToolNodeConfiguration = context.node_definition.configuration

        # 查找工具调用器
        invoker = self._invokers.get(config.tool_identifier)
        if invoker is None:
            return NodeExecutionResult(
                success=False,
                error_code=NODE_TOOL_NOT_FOUND,
                error_message=f"工具 {config.tool_identifier} 不存在",
            )

        # 构建入参：静态参数 + 从共享状态映射的参数
        # （统一装配，unified_node_input T3.2：static_arguments 常量直传保持，
        #   互斥校验既有；「键在值为 null → 正常传递 null」为登记的行为修正点）
        snapshot = dict(context.shared_state.node_outputs)
        outcome = assemble_keyed_mapping(
            config.argument_mapping,
            snapshot,
            error_code=NODE_TOOL_ARGUMENT_MISMATCH,
            node_definitions=context.node_definitions,
        )
        if isinstance(outcome, AssemblyFailure):
            return NodeExecutionResult(
                success=False,
                error_code=outcome.error_code,
                error_message=f"工具 {config.tool_identifier} 入参映射失败：{outcome.error_message}",
            )
        arguments: Dict[str, JsonValue] = {**config.static_arguments, **outcome.values}

        # 调用工具
        timeout = context.timeout_seconds
        try:
            result = await invoker.invoke(arguments, timeout)
        except TimeoutError:
            return NodeExecutionResult(
                success=False,
                error_code=NODE_TOOL_TIMEOUT,
                error_message=f"工具 {config.tool_identifier} 调用超时（{timeout} 秒）",
            )

        if not result.success:
            return NodeExecutionResult(
                success=False,
                error_code=result.error_code or NODE_TOOL_NOT_FOUND,
                error_message=result.error_message or f"工具 {config.tool_identifier} 调用失败",
            )

        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.effective_output_name,
            output_value=result.output,
            metadata={"tool_identifier": config.tool_identifier, "tool_source": config.tool_source},
        )
