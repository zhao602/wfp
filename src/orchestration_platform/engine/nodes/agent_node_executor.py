# =============================================================================
# Agent 节点执行器（engine/nodes/agent_node_executor.py）
#
# 职责：调用 LLM 完成推理，支持 ReAct 式工具调用循环、结构化输出校验、主备模型切换。
# 设计意图（对应 spec 5.3.1.1-4、design 2.5.3；merge_tool_into_agent_node DC-D / FR-4）：
#   1. 调用 ILlmClient.infer 完成推理；
#   2. 若返回工具调用意图则经 ToolInvoker 执行并回填后继续推理（设最大轮次）；
#   3. 输出经结构化校验，不合规按配置重试，耗尽返回 NODE_AGENT_OUTPUT_INVALID；
#   4. 主模型失败自动切备用并写 STATUS_CHANGED 事件标记；
#   5. INV-9 缺省等价：未注入任何工具调用器（映射为空）时，execute() 行为与
#      改造前逐字节一致（不进入 ReAct 循环，主备切换/输出校验逻辑仅抽取不改写）；
#   6. INV-13 观察回填隔离：工具观察文本仅进入 LLM 请求的 user_message 拼接，
#      不写入共享状态（node_outputs 仍仅由最终 NodeExecutionResult 产出）；
#   7. context_strategy（差异化扩展）：节点配置 context_strategy 非空时，ReAct
#      观察按轮收集为观察块并经 context_engine 既有窗口策略裁剪后回填
#      （None=既有字符串追加路径，行为与扩展前逐字节一致）。
# =============================================================================

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple, Union

from orchestration_platform.context_engine.models import AgentContext, ConversationTurn
from orchestration_platform.context_engine.window_strategies import (
    ImportanceRankingStrategy,
    SlidingWindowStrategy,
    StructuralLosslessStrategy,
    SummaryCompressionStrategy,
)
from orchestration_platform.domain.errors import (
    NODE_AGENT_OUTPUT_INVALID,
    NODE_LLM_UNAVAILABLE,
    NODE_TOOL_ARGUMENT_MISMATCH,
    NODE_TOOL_TIMEOUT,
)
from orchestration_platform.domain.models.enums import (
    ContextWindowStrategyType,
    NodeType,
)
from orchestration_platform.domain.models.node_configurations import AgentNodeConfiguration
from orchestration_platform.engine.abstractions.engine_config import EngineSettings
from orchestration_platform.engine.input_assembly import (
    AssemblyFailure,
    assemble_agent_inputs,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.integrations.abstractions.external_clients import (
    ILlmClient,
    InferenceRequest,
    InferenceResult,
    IToolInvoker,
)


class AgentNodeExecutor:
    """Agent 节点执行器：概率性推理 + ReAct 工具循环 + 主备切换。"""

    def __init__(
        self,
        llm_client: ILlmClient,
        fallback_llm_client: Optional[ILlmClient] = None,
        tool_invoker: Optional[Any] = None,
        tool_invokers: Optional[Dict[str, IToolInvoker]] = None,
        settings: Optional[EngineSettings] = None,
    ) -> None:
        """构造 Agent 执行器。

        参数：
            llm_client          —— 主 LLM 客户端
            fallback_llm_client —— 备用 LLM 客户端（主模型失败时切换）
            tool_invoker        —— 沙箱化工具调用器（偏差修正 T140，可选，None 保持既有行为）；
                                    传入单实例时内部并入工具映射（兼容语义）
            tool_invokers       —— 工具标识 -> 调用器实例 的映射（merge_tool_into_agent_node
                                    DC-D，可选；None/空映射时行为与改造前一致，INV-9）
            settings            —— 引擎运行配置（M4 B10：缺省 None → EngineSettings()
                                    默认值；宿主注入生效 env 覆盖）
        """
        self._llm_client = llm_client
        self._fallback_client = fallback_llm_client
        self._settings = settings or EngineSettings()
        # 工具调用器映射：tool_invokers 全量 + tool_invoker 单实例并入
        invokers: Dict[str, IToolInvoker] = dict(tool_invokers) if tool_invokers else {}
        if tool_invoker is not None:
            invokers[tool_invoker.tool_identifier] = tool_invoker
        self._tool_invokers: Dict[str, IToolInvoker] = invokers

    @property
    def node_type(self) -> NodeType:
        return NodeType.AGENT

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行 Agent 节点：LLM 推理 + 工具调用循环 + 输出校验。"""
        config: AgentNodeConfiguration = context.node_definition.configuration

        # 构建推理请求（用户消息双模式，unified_node_input T3.7/DC-03）
        user_message_or_failure = self._build_user_message(context)
        if isinstance(user_message_or_failure, AssemblyFailure):
            return NodeExecutionResult(
                success=False,
                error_code=user_message_or_failure.error_code,
                error_message=(
                    f"Agent 节点 {context.node_definition.node_id} "
                    f"{user_message_or_failure.error_message}"
                ),
            )
        user_message = user_message_or_failure
        request = InferenceRequest(
            model_identifier=config.model_identifier,
            system_prompt=config.system_prompt,
            user_message=user_message,
            available_tools=config.available_tools,
            output_schema=config.output_schema,
        )

        tool_rounds_used = 0
        tool_calls_executed = 0
        current_request = request

        if self._tool_invokers:
            # ReAct 闭环（DC-D / FR-4）：意图消费 → 工具执行 → 观察回填 → 再推理
            # 返回闭环内最后一轮推理请求（含观察回填），供输出校验重试复用；
            # 不落实例状态（执行器为注册表单例，须保证并发执行隔离）
            result, failure, tool_rounds_used, tool_calls_executed, current_request, compression_events = (
                await self._run_react_loop(request, config, context)
            )
            if failure is not None:
                return failure
        else:
            # INV-9 缺省等价：无工具调用器时走既有路径（行为与改造前一致）
            result, failure = await self._infer_with_fallback(request)
            if failure is not None:
                return failure
            current_request = request

        # 结构化输出校验
        output_value = result.structured_output or {"content": result.content}
        # roadmap B7 W1b：usage 语义取「产出最终输出那次推理」
        # （重试成功即重试结果；ReAct 循环中间推理不计）
        usage_source: InferenceResult = result
        if config.output_schema:
            validation_error = self._validate_output_schema(output_value, config.output_schema)
            if validation_error:
                max_retries = config.output_max_retries or self._settings.agent_output_max_retries
                if max_retries > 0:
                    # 重试推理（简化：仅重试一次，完整实现应循环重试）
                    retry_result = await self._llm_client.infer(current_request)
                    if retry_result.success:
                        output_value = retry_result.structured_output or {"content": retry_result.content}
                        usage_source = retry_result
                        validation_error = self._validate_output_schema(output_value, config.output_schema)
                if validation_error:
                    return NodeExecutionResult(
                        success=False,
                        error_code=NODE_AGENT_OUTPUT_INVALID,
                        error_message=f"输出结构不合规：{validation_error}",
                    )

        metadata: Dict[str, Any] = {"model_identifier": config.model_identifier}
        # roadmap B7 W1b：provider 返回 usage 时透传（未返回不写键）
        if usage_source.usage is not None:
            metadata["token_usage"] = dict(usage_source.usage)
        if self._tool_invokers:
            # 仅新增观察键，不改既有键（INV-9：无工具路径 metadata 与改造前一致）
            metadata["tool_rounds_used"] = tool_rounds_used
            metadata["tool_calls_executed"] = tool_calls_executed
            if compression_events > 0:
                metadata["context_compression_events"] = compression_events

        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.effective_output_name,
            output_value=output_value,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # ReAct 闭环（DC-D / FR-4，私有方法不构成对外契约）
    # ------------------------------------------------------------------

    async def _run_react_loop(
        self,
        request: InferenceRequest,
        config: AgentNodeConfiguration,
        context: NodeExecutionContext,
    ) -> Tuple[
        Optional[InferenceResult],
        Optional[NodeExecutionResult],
        int,
        int,
        InferenceRequest,
        int,
    ]:
        """ReAct 循环：推理 → 解析意图 → 执行工具 → 观察回填 → 再推理。

        返回 (成功推理结果, 失败节点结果, tool_rounds_used, tool_calls_executed,
        最后一轮推理请求, context_compression_events)，成功与失败二者恰一为 None。

        不落实例状态（执行器为注册表单例，须保证并发执行隔离）。

        轮次上限：节点级 max_tool_rounds 优先，缺省取全局 agent_max_tool_rounds；
        达上限仍有调用意图 → NODE_AGENT_OUTPUT_INVALID 终止（禁止无限循环）。

        上下文策略（context_strategy，差异化扩展）：
        - None → 既有路径：观察逐轮字符串追加拼接（行为与改造前逐字节一致）；
        - 非空 → 观察按轮收集为观察块，每轮回填前经对应窗口策略裁剪早期观察块
          （context_engine 能力接线），再与基础消息拼接。
        """
        max_rounds = config.max_tool_rounds or self._settings.agent_max_tool_rounds
        rounds_used = 0
        tool_calls_executed = 0
        compression_events = 0
        current_request = request
        # 观察块收集（context_strategy 非空时启用；None 时保持既有字符串追加路径）
        base_user_message = request.user_message
        observation_blocks: List[str] = []

        while True:
            result, failure = await self._infer_with_fallback(current_request)
            if failure is not None:
                return (
                    None,
                    failure,
                    rounds_used,
                    tool_calls_executed,
                    current_request,
                    compression_events,
                )

            tool_calls = self._extract_tool_calls(result.tool_call_intent)
            if not tool_calls:
                # 无工具调用意图：跳出循环 → 输出校验（既有逻辑）
                return (
                    result,
                    None,
                    rounds_used,
                    tool_calls_executed,
                    current_request,
                    compression_events,
                )

            if rounds_used >= max_rounds:
                # 轮次耗尽仍有调用意图：明确失败终止（FR-4 规则 2）
                return None, NodeExecutionResult(
                    success=False,
                    error_code=NODE_AGENT_OUTPUT_INVALID,
                    error_message=f"工具调用轮次超限（{max_rounds} 轮），Agent 仍产生工具调用意图",
                    metadata={
                        "tool_rounds_used": rounds_used,
                        "tool_calls_executed": tool_calls_executed,
                    },
                ), rounds_used, tool_calls_executed, current_request, compression_events

            # 逐个执行工具调用并生成观察记录块（INV-13：仅进 user_message）
            observation_lines: List[str] = []
            timeout = context.timeout_seconds
            for tool_name, arguments, parse_error in tool_calls:
                if parse_error is not None:
                    observation_lines.append(
                        self._format_observation(
                            tool_name,
                            {
                                "error_code": NODE_TOOL_ARGUMENT_MISMATCH,
                                "error": parse_error,
                            },
                        )
                    )
                else:
                    observation_lines.append(
                        await self._execute_tool_call(tool_name, arguments, config, timeout)
                    )
                tool_calls_executed += 1

            rounds_used += 1
            # 观察回填（不写共享状态，INV-13）：
            if config.context_strategy is None:
                # 既有路径：追加拼入下一轮 user_message（与改造前逐字节一致）
                observed_message = current_request.user_message
                if observed_message:
                    observed_message += "\n"
                observed_message += "\n".join(observation_lines)
            else:
                # 策略路径：观察按轮收集为观察块，经窗口策略裁剪后重组 user_message
                observation_blocks.append("\n".join(observation_lines))
                compressed_blocks, compressed = self._apply_context_strategy(
                    observation_blocks, context, config.context_strategy
                )
                if compressed:
                    compression_events += 1
                observed_message = "\n".join(
                    part for part in [base_user_message] + compressed_blocks if part
                )

            current_request = InferenceRequest(
                model_identifier=request.model_identifier,
                system_prompt=request.system_prompt,
                user_message=observed_message,
                available_tools=request.available_tools,
                output_schema=request.output_schema,
            )

    def _apply_context_strategy(
        self,
        observation_blocks: List[str],
        context: NodeExecutionContext,
        strategy_type: ContextWindowStrategyType,
    ) -> Tuple[List[str], bool]:
        """对历史观察块应用配置的窗口策略（context_engine 能力接线）。

        将观察块包装为 AgentContext 的对话轮次，按 strategy_type 选择既有
        窗口策略对象执行裁剪，返回 (裁剪后观察块文本列表, 是否发生压缩)。
        策略类型经参数传递（不落实例状态，保证并发执行隔离）。
        """
        strategy_map = {
            ContextWindowStrategyType.SLIDING_WINDOW: lambda: SlidingWindowStrategy(
                window_size=10
            ),
            ContextWindowStrategyType.SUMMARY_COMPRESSION: lambda: SummaryCompressionStrategy(
                max_tokens=500, retain_recent=5
            ),
            ContextWindowStrategyType.IMPORTANCE_RANKING: lambda: ImportanceRankingStrategy(
                threshold=0.5, retain_recent=3
            ),
            ContextWindowStrategyType.STRUCTURAL_LOSSLESS: lambda: (
                StructuralLosslessStrategy()
                if self._settings.structural_lossless_pruning_enabled
                else SlidingWindowStrategy(window_size=10)
            ),
        }
        builder = strategy_map.get(strategy_type)
        if builder is None:
            return observation_blocks, False

        agent_context = AgentContext(
            instance_id=context.instance_id,
            node_id=context.node_definition.node_id,
            conversation_history=[
                ConversationTurn(role="tool", content=block)
                for block in observation_blocks
            ],
        )
        compressed_context = builder().apply(agent_context)
        compressed_blocks = [turn.content for turn in compressed_context.conversation_history]
        compressed = compressed_blocks != observation_blocks
        return compressed_blocks, compressed


    async def _execute_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        config: AgentNodeConfiguration,
        timeout_seconds: int,
    ) -> str:
        """执行单个工具调用，返回观察文本（三分支，节点不因单次调用失败终止）。

        分支（FR-4 规则 3/4/5）：
            1. 工具名不在绑定列表 → 拒绝观察（未绑定即不可见）；
            2. 绑定但无对应调用器 → 「工具不存在」观察（降级，节点不失败）；
            3. 可调用 → 实际执行；成功观察=工具输出 JSON，超时/失败观察=错误信息。
        """
        if tool_name not in config.available_tools:
            return self._format_observation(
                tool_name, {"error": f"工具 {tool_name} 未绑定，不可调用"}
            )

        invoker = self._tool_invokers.get(tool_name)
        if invoker is None:
            return self._format_observation(tool_name, {"error": f"工具 {tool_name} 不存在"})

        try:
            result = await invoker.invoke(arguments, timeout_seconds)
        except (TimeoutError, asyncio.TimeoutError):
            # Python 3.10 中 asyncio.TimeoutError 与内建 TimeoutError 不同类，两者均捕获
            return self._format_observation(
                tool_name,
                {
                    "error_code": NODE_TOOL_TIMEOUT,
                    "error": f"工具 {tool_name} 调用超时（{timeout_seconds} 秒）",
                },
            )
        except Exception as e:
            return self._format_observation(
                tool_name, {"error": f"工具 {tool_name} 调用失败：{e}"}
            )

        if not result.success:
            return self._format_observation(
                tool_name,
                {
                    "error_code": result.error_code,
                    "error": result.error_message or f"工具 {tool_name} 调用失败",
                },
            )
        return self._format_observation(tool_name, result.output)

    @staticmethod
    def _format_observation(tool_name: str, observation: Any) -> str:
        """格式化观察记录块：TOOL_RESULT[标识]: JSON 序列化观察。"""
        return f"TOOL_RESULT[{tool_name}]: {json.dumps(observation, ensure_ascii=False, default=str)}"

    @staticmethod
    def _extract_tool_calls(
        intent: Optional[Dict[str, Any]],
    ) -> List[Tuple[str, Optional[Dict[str, Any]], Optional[str]]]:
        """解析工具调用意图。

        返回 (tool_name, arguments, parse_error) 列表；arguments 为 JSON 字符串时
        解析为 dict，解析失败时 arguments 为 None 且 parse_error 非空
        （按入参不匹配观察处理，FR-4 规则 5 语义）。
        """
        if not intent:
            return []
        raw_calls = intent.get("tool_calls")
        if not isinstance(raw_calls, list):
            return []

        calls: List[Tuple[str, Optional[Dict[str, Any]], Optional[str]]] = []
        for raw in raw_calls:
            if not isinstance(raw, dict):
                continue
            function = raw.get("function")
            if not isinstance(function, dict):
                continue
            tool_name = function.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, dict):
                calls.append((tool_name, arguments, None))
                continue
            if isinstance(arguments, str):
                try:
                    parsed = json.loads(arguments)
                    if isinstance(parsed, dict):
                        calls.append((tool_name, parsed, None))
                        continue
                except (ValueError, TypeError):
                    pass
            calls.append(
                (
                    tool_name,
                    None,
                    f"工具 {tool_name} 调用入参解析失败（入参不匹配）",
                )
            )
        return calls

    # ------------------------------------------------------------------
    # 既有逻辑抽取（行为零变化，INV-9）
    # ------------------------------------------------------------------

    async def _infer_with_fallback(
        self, request: InferenceRequest
    ) -> Tuple[Optional[InferenceResult], Optional[NodeExecutionResult]]:
        """主模型推理，失败切备用（原主备切换逻辑抽取，行为零变化）。

        返回 (成功推理结果, None) 或 (None, 失败节点结果)。
        """
        result = await self._llm_client.infer(request)

        if not result.success and self._fallback_client is not None:
            fallback_request = InferenceRequest(
                model_identifier=self._settings.llm_fallback_model
                or request.model_identifier,
                system_prompt=request.system_prompt,
                user_message=request.user_message,
                available_tools=request.available_tools,
                output_schema=request.output_schema,
            )
            result = await self._fallback_client.infer(fallback_request)
            if not result.success:
                return None, NodeExecutionResult(
                    success=False,
                    error_code=NODE_LLM_UNAVAILABLE,
                    error_message=f"主备模型均不可用：{result.error_message}",
                    metadata={"model_switched": True},
                )
        elif not result.success:
            return None, NodeExecutionResult(
                success=False,
                error_code=NODE_LLM_UNAVAILABLE,
                error_message=f"LLM 推理失败：{result.error_message}",
            )

        return result, None

    def _build_user_message(
        self, context: NodeExecutionContext
    ) -> Union[str, AssemblyFailure]:
        """从共享状态构建用户消息（双模式，unified_node_input DC-03/T3.7）。

        - config.inputs is None → 隐式全量：node_outputs 全量平铺
          （既有路径逐字符不变，INV-D08）；
        - config.inputs == [] → 显式空清单：用户消息上下文条目为空；
        - 非空 → 显式选择集：assemble_agent_inputs 装配，按「alias ?? 命名输出名」
          组织为既有「名字: 值」平铺文本格式；任一条目失败 → 整体失败
          （fail-fast，统一码，返回 AssemblyFailure 由 execute 构造失败结果）；
          清单外命名输出不进入用户消息（INV-12 收窄）。
        """
        config: AgentNodeConfiguration = context.node_definition.configuration
        if config.inputs is None:
            # 隐式全量模式（存量 YAML 默认，既有代码原样保留）
            outputs = context.shared_state.node_outputs
            if not outputs:
                return ""
            parts = []
            for name, value in outputs.items():
                parts.append(f"{name}: {value}")
            return "\n".join(parts)

        # 显式模式（[] 空清单 → 空消息；非空 → 选择集装配）
        snapshot = dict(context.shared_state.node_outputs)
        outcome = assemble_agent_inputs(
            config.inputs, snapshot, node_definitions=context.node_definitions
        )
        if isinstance(outcome, AssemblyFailure):
            return outcome
        if not outcome.values:
            return ""
        parts = []
        for name, value in outcome.values.items():
            parts.append(f"{name}: {value}")
        return "\n".join(parts)

    def _validate_output_schema(
        self, output: dict, schema: dict
    ) -> Optional[str]:
        """校验输出是否符合结构约束（字段名 -> 类型）。"""
        for field_name, expected_type in schema.items():
            if field_name not in output:
                return f"缺少必填字段：{field_name}"
            actual_value = output[field_name]
            if not self._check_type(actual_value, expected_type):
                return f"字段 {field_name} 类型不匹配：期望 {expected_type}，实际 {type(actual_value).__name__}"
        return None

    @staticmethod
    def _check_type(value, expected_type: str) -> bool:
        """检查值是否符合期望类型字符串。"""
        type_map = {
            "string": str, "str": str,
            "integer": int, "int": int,
            "number": (int, float), "float": float,
            "boolean": bool, "bool": bool,
            "object": dict, "dict": dict,
            "array": list, "list": list,
        }
        expected = type_map.get(expected_type.lower())
        if expected is None:
            return True
        return isinstance(value, expected)
