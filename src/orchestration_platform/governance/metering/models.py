# =============================================================================
# Token 计量领域模型（governance/metering/models.py）
#
# 职责：定义 Token 用量记录的领域模型。
# 设计意图（对应 spec 5.2、design 2.5）：
#   1. 按实例/节点/Agent 三维采集 Token 用量；
#   2. 异步采集不阻塞执行主流程；
#   3. 用量记录持久化后不可修改。
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TokenUsageRecord(BaseModel):
    """Token 用量记录：单次 LLM 调用的 Token 消耗。

    按实例/节点/Agent 三维采集，异步写入不阻塞执行。
    """

    record_id: str = Field(description="记录唯一标识")
    tenant_id: str = Field(description="所属租户标识")
    instance_id: str = Field(description="执行实例标识")
    node_id: str = Field(description="节点标识")
    agent_node_id: Optional[str] = Field(
        default=None, description="Agent 节点标识（非 Agent 节点为 None）"
    )
    model_identifier: str = Field(description="模型标识")
    prompt_tokens: int = Field(default=0, ge=0, description="输入 Token 数")
    completion_tokens: int = Field(default=0, ge=0, description="输出 Token 数")
    total_tokens: int = Field(default=0, ge=0, description="总 Token 数")
    recorded_at: datetime = Field(default_factory=_utc_now, description="记录时间")
    workflow_definition_id: Optional[str] = Field(
        default=None, description="工作流定义标识"
    )
