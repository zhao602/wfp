# =============================================================================
# 大模型节点执行器（engine/nodes/llm_node_executor.py）
#
# 职责：单轮大模型推理节点（llm_code_nodes_v2 CHG-07 / DC-05 / spec §5.4）。
# 设计意图：
#   1. 入口先判能力开关 llm_node_enabled，关闭 → LLM_NODE_DISABLED
#      （不装配、不调用客户端；DC-15「始终注册 + 内部判开关」）；
#   2. 每次 execute() 即时读取节点配置并组装采样值对象（构造期不读配置、
#      不缓存，AC-18 改配置后重新执行即用新值）；
#   3. 用户消息经 assemble_user_message 装配（三态语义与 agent 一致，AC-46），
#      失败透传 NODE_INPUT_REFERENCE_MISSING / NODE_INPUT_TYPE_MISMATCH；
#   4. 经 ILlmClient.infer 单轮推理（只读共享状态，不写任何键，AC-26）；
#   5. 客户端缺失或推理失败 → LLM_INFERENCE_FAILED（不抛异常）；
#      error_status_code ∈ {400,422} 且 error_detail 含采样参数名 →
#      LLM_PARAM_UNSUPPORTED 且指明参数（DC-10 启发式，识别失败不谎报）；
#   6. output_schema 登记不生效（DC-09）：请求不携带、执行器零读取、零结构
#      化校验、零重生成（OP-04 薄定位，AC-13）；
#   7. 成功输出恒 {"content": <文本>}（无校验、无重试、无二次加工）；
#      失败时 output_value=None（INV-11）；
#   8. metadata 含 model_identifier + 采样摘要（不含凭据，FR-51/AC-53）+
#      切换时 model_switched=True（DC-08/AC-22）。
# 注：llm_node_enabled 已落地（任务 2.2，默认 False）；getattr 容错读取保留
#     （字段缺失视同关闭，FR-03-b，减少触碰面）。
# =============================================================================

from typing import Any, Dict, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    LLM_INFERENCE_FAILED,
    LLM_NODE_DISABLED,
    LLM_PARAM_UNSUPPORTED,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    LlmNodeConfiguration,
    LlmSamplingParameters,
)
from orchestration_platform.domain.values.llm_sampling_spec import resolve_sampling
from orchestration_platform.engine.input_assembly import (
    AssemblyFailure,
    assemble_user_message,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.integrations.abstractions.external_clients import (
    ILlmClient,
    InferenceRequest,
    InferenceResult,
)

# DC-10 参数名识别的候选清单（与采样登记表同序，INV-12；识别为启发式，D-02）
_SAMPLING_PARAM_NAMES: tuple = (
    "temperature",
    "top_p",
    "top_k",
    "max_tokens",
    "stop",
    "seed",
)


class LlmNodeExecutor:
    """大模型节点执行器：装配 → 单轮推理 → 透出（薄链路，DC-05/OP-04）。"""

    def __init__(self, llm_client: Optional[ILlmClient] = None) -> None:
        """构造大模型节点执行器（不读节点配置、不建立网络连接）。"""
        self._llm_client = llm_client
        self._settings = get_settings()

    @property
    def node_type(self) -> NodeType:
        return NodeType.LLM

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行大模型节点：装配 → 单轮推理 → 透出（不写共享状态）。"""
        node_id = context.node_definition.node_id

        # ① 能力开关：关闭 → LLM_NODE_DISABLED（不装配、不调用）
        if not getattr(self._settings, "llm_node_enabled", False):
            return NodeExecutionResult(
                success=False,
                error_code=LLM_NODE_DISABLED,
                error_message=f"大模型节点 {node_id} 功能未启用（开关 llm_node_enabled 关闭）",
            )

        # ② 即时读取配置，组装采样值对象（每次执行重新读取，不缓存）
        config: LlmNodeConfiguration = context.node_definition.configuration
        sampling = LlmSamplingParameters(
            temperature=config.temperature,
            top_p=config.top_p,
            top_k=config.top_k,
            max_tokens=config.max_tokens,
            stop=config.stop,
            seed=config.seed,
        )
        resolved_sampling: Dict[str, Any] = resolve_sampling(sampling)

        # ③ 用户消息装配（三态语义与 agent 一致，AC-46）
        outputs_snapshot = dict(context.shared_state.node_outputs)
        user_message_or_failure = assemble_user_message(
            config.inputs, outputs_snapshot, config.user_prompt
        )
        if isinstance(user_message_or_failure, AssemblyFailure):
            return NodeExecutionResult(
                success=False,
                error_code=user_message_or_failure.error_code,
                error_message=(
                    f"大模型节点 {node_id} {user_message_or_failure.error_message}"
                ),
            )
        user_message = user_message_or_failure

        # ④ 构造推理请求（只读快照；output_schema 不随请求发送——DC-09 登记不生效）
        request = InferenceRequest(
            model_identifier=config.model_identifier,
            system_prompt=config.system_prompt,
            user_message=user_message,
            sampling=sampling,
        )

        # ⑤ 客户端缺失或推理失败 → LLM_INFERENCE_FAILED / LLM_PARAM_UNSUPPORTED
        if self._llm_client is None:
            return NodeExecutionResult(
                success=False,
                error_code=LLM_INFERENCE_FAILED,
                error_message=f"大模型节点 {node_id} 推理失败：LLM 客户端未配置",
            )
        result = await self._llm_client.infer(request)
        if not result.success:
            return self._map_inference_failure(node_id, result)

        # ⑥/⑦ 输出形态：恒 {"content": 文本}（无校验、无重试、无二次加工，DC-05）
        output_value: Dict[str, Any] = {"content": result.content}

        # ⑧ metadata：模型标识 + 采样摘要（不含凭据）+ 切换标记
        metadata: Dict[str, Any] = {
            "model_identifier": config.model_identifier,
            "sampling": resolved_sampling,
        }
        if result.model_switched:
            metadata["model_switched"] = True
        # roadmap B7 W1b：provider 返回 usage 时透传（未返回不写键）；
        # 归一化在记账侧（usage_recorder.record_node_usage）
        if result.usage is not None:
            metadata["token_usage"] = dict(result.usage)

        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.effective_output_name,
            output_value=output_value,
            metadata=metadata,
        )

    @staticmethod
    def _map_inference_failure(
        node_id: str, result: InferenceResult
    ) -> NodeExecutionResult:
        """推理失败映射（DC-10 / AC-20）：参数拒绝启发式识别，失败不谎报。

        - error_status_code ∈ {400,422} 且 error_detail 含采样参数名 →
          LLM_PARAM_UNSUPPORTED（指明参数）；
        - 其余（含识别失败）→ LLM_INFERENCE_FAILED，消息含状态码（若有）。
        """
        status = result.error_status_code
        if status in (400, 422) and result.error_detail:
            matched = [
                name for name in _SAMPLING_PARAM_NAMES if name in result.error_detail
            ]
            if matched:
                return NodeExecutionResult(
                    success=False,
                    error_code=LLM_PARAM_UNSUPPORTED,
                    error_message=(
                        f"大模型节点 {node_id} 上游模型服务拒绝采样参数："
                        f"{'、'.join(matched)}（状态码 {status}）"
                    ),
                )
        message = f"大模型节点 {node_id} 推理失败：{result.error_message or '未知原因'}"
        if status is not None and str(status) not in message:
            message += f"（状态码 {status}）"
        return NodeExecutionResult(
            success=False,
            error_code=LLM_INFERENCE_FAILED,
            error_message=message,
        )
