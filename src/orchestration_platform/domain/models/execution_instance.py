# =============================================================================
# 执行实例模型与应用层 DTO（domain/models/execution_instance.py）
#
# 职责：定义执行实例对象，以及启动/回滚/重放/审核等用例的请求与结果对象。
# 设计意图（对应需求规格 6.3）：
#   实例绑定定义版本快照，后续定义变更不影响已启动实例；
#   幂等键支持同一键在有效期内只创建一个实例。
# =============================================================================

from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.enums import InstanceStatus, ReplayMode, ReviewDecision, TriggerType


def _utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class ExecutionInstance(BaseModel):
    """执行实例（对应需求规格 6.3）。"""

    instance_id: str = Field(description="实例标识（全局唯一）")
    definition_id: str = Field(description="所属定义标识")
    definition_version: int = Field(description="绑定定义版本（快照）")
    status: InstanceStatus = Field(default=InstanceStatus.PENDING, description="实例状态")
    inputs: Dict[str, JsonValue] = Field(default_factory=dict, description="输入参数")
    outputs: Dict[str, JsonValue] = Field(default_factory=dict, description="最终输出")
    idempotency_key: Optional[str] = Field(default=None, description="幂等键")
    trigger_type: TriggerType = Field(default=TriggerType.MANUAL, description="触发方式")
    trace_id: str = Field(description="追踪标识（贯穿实例全部日志与事件）")
    started_at: datetime = Field(default_factory=_utc_now, description="开始时间")
    finished_at: Optional[datetime] = Field(default=None, description="结束时间（进入终态后必填）")
    current_node_id: Optional[str] = Field(default=None, description="当前节点标识")
    error: Optional[Dict[str, JsonValue]] = Field(default=None, description="失败时的错误信息")


class DefinitionRef(BaseModel):
    """工作流定义引用：保存后返回的标识 + 版本。"""

    definition_id: str = Field(description="定义标识")
    definition_version: int = Field(description="定义版本号")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")


class StartInstanceRequest(BaseModel):
    """启动实例请求。"""

    definition_id: str = Field(description="定义标识")
    definition_version: Optional[int] = Field(default=None, description="定义版本（缺省用最新版本）")
    inputs: Dict[str, JsonValue] = Field(default_factory=dict, description="实例输入参数")
    idempotency_key: Optional[str] = Field(default=None, description="幂等键")
    trigger_type: TriggerType = Field(default=TriggerType.MANUAL, description="触发方式")
    trigger_context: Dict[str, JsonValue] = Field(default_factory=dict, description="触发上下文（供模板映射）")


class RollbackResult(BaseModel):
    """回滚结果。"""

    instance_id: str = Field(description="实例标识")
    restored_checkpoint_id: str = Field(description="恢复到的目标检查点标识")
    restored_round_index: int = Field(description="恢复到的执行轮次")
    voided_event_count: int = Field(description="被作废的事件数量")


class ReplayRequest(BaseModel):
    """重放请求。"""

    instance_id: str = Field(description="实例标识")
    checkpoint_id: str = Field(description="重放起始检查点标识")
    mode: ReplayMode = Field(default=ReplayMode.STRICT_REPLAY, description="重放模式")


class ReplayResult(BaseModel):
    """重放结果。"""

    instance_id: str = Field(description="实例标识")
    mode: ReplayMode = Field(description="重放模式")
    consistent: bool = Field(description="路径一致性标记（严格重放应为真）")
    divergent_node_ids: List[str] = Field(default_factory=list, description="输出与历史不一致的节点清单")
    new_event_count: int = Field(default=0, description="新增事件数量")


class ReviewDecisionRequest(BaseModel):
    """人工审核决策请求。"""

    decision: ReviewDecision = Field(description="审核决策：通过/驳回")
    comment: str = Field(default="", description="审核意见")
