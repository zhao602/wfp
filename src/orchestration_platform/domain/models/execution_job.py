# =============================================================================
# 执行作业领域模型（domain/models/execution_job.py）
#
# 职责：执行队列作业条目（roadmap-v3 中期窗口 M1，F1 执行队列与提交路径统一）。
# 设计意图（plan D2）：
#   1. 提交侧（ExecutionSubmitter）入队、worker 侧（M2）认领执行的数据载体；
#   2. 租约字段承载 at-least-once 接管语义（plan D3）：
#      lease_owner/lease_expires_at + attempts（认领即持租约，过期可被接管）；
#   3. idempotency_key 部分唯一（先例 execution_instance 幂等键，
#      table_definitions.sql:46-48）——同键重复提交返回既有作业；
#   4. 状态机：pending（待认领）→ running（持租约执行中）→ 终态
#      （completed/failed/cancelled）；已取消作业为终态不重投（plan D4）。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

# 作业状态常量（自由字符串，与 InstanceRuntime.status 风格一致）
JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"

# 作业终态集合（终态不重投、不重认领）
JOB_TERMINAL_STATUSES = (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_CANCELLED,
)


class ExecutionJob(BaseModel):
    """执行队列作业：提交侧与 worker 侧的数据契约。"""

    job_id: str = Field(description="作业唯一标识（UUID）")
    instance_id: str = Field(description="关联工作流实例标识")
    tenant_id: Optional[str] = Field(
        default=None, description="租户标识（None = 治理降级/cron 无租户）"
    )
    definition_id: str = Field(description="工作流定义标识（worker 重取定义编译）")
    definition_version: Optional[int] = Field(
        default=None, description="定义版本（None = 最新版本）"
    )
    yaml_text: Optional[str] = Field(
        default=None, description="定义 YAML 文本（提交侧携带则入队，可选）"
    )
    initial_inputs: Dict[str, Any] = Field(
        default_factory=dict, description="初始输入（SharedState.node_outputs 起点）"
    )
    trigger_source: str = Field(
        default="manual",
        description="触发来源（manual/cron/re_execute/review_resume，对齐 run_and_record）",
    )
    trace_id: str = Field(default="", description="链路追踪标识")
    resume_checkpoint_id: Optional[str] = Field(
        default=None, description="恢复检查点标识（接管/恢复续跑位点）"
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description="提交幂等键（缺省以 instance_id 充当；同键非终态重复提交返回既有作业）",
    )
    status: str = Field(
        default=JOB_STATUS_PENDING,
        description="作业状态（pending/running/completed/failed/cancelled）",
    )
    lease_owner: Optional[str] = Field(
        default=None, description="当前租约持有者（worker 标识）"
    )
    lease_expires_at: Optional[str] = Field(
        default=None, description="租约到期时刻（ISO8601；认领/心跳时刷新）"
    )
    attempts: int = Field(
        default=0, ge=0, description="认领尝试次数（每次认领/接管 +1）"
    )
    last_error: Optional[str] = Field(
        default=None, description="最近一次失败原因（fail 时落值）"
    )
    cancel_requested: bool = Field(
        default=False,
        description="取消请求标志（M3 P5：API cancel 置位，worker 心跳轮询发现后取消执行；幂等置位）",
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="入队时刻（ISO8601）",
    )

    def is_terminal(self) -> bool:
        """作业是否处于终态（终态不重投、不重认领）。"""
        return self.status in JOB_TERMINAL_STATUSES
