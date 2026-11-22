# =============================================================================
# 主备 LLM 客户端（integrations/llm/primary_fallback_llm_client.py）
#
# 职责：主模型调用失败或超时自动切换备用模型，并在结果中标记模型切换。
# 设计意图（对应 spec 5.3.1.1-4、design 2.5.3）：
#   1. 主模型失败 → 自动切备用 → 结果 model_switched=True（DC-08 / AC-22）；
#   2. 主备均失败 → 聚合失败，保留更可诊断一方的 error_status_code/error_detail
#      （DC-10 / AC-20，识别失败不谎报）；
#   3. 备用复用同一请求对象（D-06 / RISK-03：本期只标记，不改模型名切换）。
# =============================================================================

from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.integrations.abstractions.external_clients import (
    ILlmClient,
    InferenceRequest,
    InferenceResult,
)


class PrimaryFallbackLlmClient:
    """主备 LLM 客户端：主模型失败自动切备用。"""

    def __init__(self, primary: ILlmClient, fallback: Optional[ILlmClient] = None) -> None:
        self._primary = primary
        self._fallback = fallback
        self._settings = get_settings()

    @staticmethod
    def _more_diagnosable(primary: InferenceResult, fallback: InferenceResult) -> bool:
        """主通道诊断信息是否不劣于备用（状态码/摘要打分，平局取主）。"""

        def _score(result: InferenceResult) -> int:
            return (1 if result.error_status_code is not None else 0) + (
                1 if result.error_detail else 0
            )

        return _score(primary) >= _score(fallback)

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        """推理：先主后备，主失败时切备用并标记（备用复用同一请求对象）。"""
        result = await self._primary.infer(request)
        if result.success:
            return result

        if self._fallback is None:
            return result

        fallback_result = await self._fallback.infer(request)
        if fallback_result.success:
            # DC-08 / AC-22：备用成功 → 标记切换（保留备用结果全部字段）
            return fallback_result.model_copy(update={"model_switched": True})

        # 主备均失败：聚合失败，保留更可诊断一方的状态码/摘要（DC-10）
        chosen = (
            result
            if self._more_diagnosable(result, fallback_result)
            else fallback_result
        )
        return InferenceResult(
            success=False,
            error_message=f"主备模型均不可用：主={result.error_message}，备={fallback_result.error_message}",
            error_status_code=chosen.error_status_code,
            error_detail=chosen.error_detail,
        )
