# =============================================================================
# Token 用量采集器（governance/metering/token_meter.py）
#
# 职责：从推理结果提取 Token 用量并构造用量记录。
# 设计意图（对应 spec 5.2、design 2.5、任务 T57.1）：
#   1. 从 InferenceResult 提取 input_tokens / output_tokens / model_id；
#   2. 采集时机：Agent 节点执行完成后；
#   3. 采集开销 ≤ 5 毫秒（异步入队，不计入写入开销）；
#   4. 用量记录含实例标识、节点标识、Agent 标识、模型标识、时间戳、tenant_id。
# =============================================================================

import uuid
from typing import Any, Dict, Optional

from orchestration_platform.governance.metering.models import TokenUsageRecord


class TokenMeter:
    """Token 用量采集器。

    从推理结果提取 Token 用量数据，构造 TokenUsageRecord。
    """

    def __init__(self) -> None:
        """初始化用量采集器。"""
        pass

    def collect_usage(
        self,
        tenant_id: str,
        instance_id: str,
        node_id: str,
        inference_result: Dict[str, Any],
        agent_node_id: Optional[str] = None,
        workflow_definition_id: Optional[str] = None,
    ) -> TokenUsageRecord:
        """从推理结果提取 Token 用量，构造用量记录。

        Args:
            tenant_id: 租户标识。
            instance_id: 执行实例标识。
            node_id: 节点标识。
            inference_result: 推理结果字典，含 input_tokens / output_tokens / model_id。
            agent_node_id: Agent 节点标识（非 Agent 节点为 None）。
            workflow_definition_id: 工作流定义标识。

        Returns:
            TokenUsageRecord 用量记录。
        """
        prompt_tokens = int(inference_result.get("input_tokens", 0))
        completion_tokens = int(inference_result.get("output_tokens", 0))
        total_tokens = prompt_tokens + completion_tokens
        model_identifier = str(inference_result.get("model_id", "unknown"))

        return TokenUsageRecord(
            record_id=f"usage_{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            instance_id=instance_id,
            node_id=node_id,
            agent_node_id=agent_node_id,
            model_identifier=model_identifier,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            workflow_definition_id=workflow_definition_id,
        )

    def collect_from_inference_result(
        self,
        tenant_id: str,
        instance_id: str,
        node_id: str,
        inference_result: Any,
        agent_node_id: Optional[str] = None,
        workflow_definition_id: Optional[str] = None,
    ) -> Optional[TokenUsageRecord]:
        """从 InferenceResult 对象提取用量（兼容字典与对象两种形式）。

        Args:
            inference_result: 推理结果，可为字典或含 input_tokens/output_tokens/model_id 属性的对象。

        Returns:
            TokenUsageRecord（成功时），None（无用量数据时）。
        """
        if isinstance(inference_result, dict):
            data = inference_result
        elif hasattr(inference_result, "model_dump"):
            data = inference_result.model_dump()
        elif hasattr(inference_result, "__dict__"):
            data = inference_result.__dict__
        else:
            return None

        if not data.get("input_tokens") and not data.get("output_tokens"):
            return None

        return self.collect_usage(
            tenant_id=tenant_id,
            instance_id=instance_id,
            node_id=node_id,
            inference_result=data,
            agent_node_id=agent_node_id,
            workflow_definition_id=workflow_definition_id,
        )
