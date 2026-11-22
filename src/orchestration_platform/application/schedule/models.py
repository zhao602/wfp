# =============================================================================
# 定时触发计划契约模型（application/schedule/models.py）
#
# 职责：定义 TriggerSchedule 契约（arch_review_v2_tasks_wave2 G3，CHG-46 /
#       design §4.2 / DC-28 规则 5-6）。
# 设计意图：
#   1. schedule_id 为 {workflow_id}:{trigger_node_id} 组合键——缺省由模型
#      派生，显式传入须与派生值一致（防路径参数与请求体漂移）；
#   2. schedule（5 段式 cron）与 frequency_preset（10 种预设，PC-8 兼容）
#      二选一，并存时 schedule 优先（resolve_next_fire 统一入口裁决）；
#   3. next_fire_at / last_fired_key 为注册表维护的派生/幂等状态
#      （DC-28 规则 5/6），构造期缺省 None，由注册表/扫描器赋值。
# =============================================================================

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, model_validator

from orchestration_platform.domain.errors import (
    SCHEDULE_INVALID_CRON,
    WF_VALIDATION_FAILED,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.enums import FrequencyPresetEnum


class TriggerSchedule(BaseModel):
    """定时触发计划契约（design §4.2）。"""

    schedule_id: str = ""
    workflow_id: str
    trigger_node_id: str
    schedule: str = ""
    frequency_preset: Optional[FrequencyPresetEnum] = None
    enabled: bool = True
    next_fire_at: Optional[datetime] = None
    last_fired_key: Optional[str] = None

    @model_validator(mode="after")
    def _validate_and_normalize(self) -> "TriggerSchedule":
        """标识消毒 + 组合键一致性 + cron/预设二选一（design §4.2 规则列）。"""
        # 标识消毒：strip 回写
        self.workflow_id = (self.workflow_id or "").strip()
        self.trigger_node_id = (self.trigger_node_id or "").strip()
        self.schedule_id = (self.schedule_id or "").strip()
        self.schedule = (self.schedule or "").strip()
        # 空标识拒绝（WF_VALIDATION_FAILED → HTTP 422）
        if not self.workflow_id or not self.trigger_node_id:
            raise WorkflowValidationError(
                WF_VALIDATION_FAILED,
                "workflow_id 与 trigger_node_id 不能为空",
                details={
                    "workflow_id": self.workflow_id,
                    "trigger_node_id": self.trigger_node_id,
                },
            )
        # 组合键：缺省派生；显式传入须与派生值一致
        derived_id = f"{self.workflow_id}:{self.trigger_node_id}"
        if self.schedule_id and self.schedule_id != derived_id:
            raise WorkflowValidationError(
                WF_VALIDATION_FAILED,
                f"schedule_id 须为 {{workflow_id}}:{{trigger_node_id}} 组合键（期望 {derived_id}）",
                details={"schedule_id": self.schedule_id, "expected": derived_id},
            )
        self.schedule_id = derived_id
        # cron/预设二选一（均缺 → SCHEDULE_INVALID_CRON，HTTP 422）
        if not self.schedule and self.frequency_preset is None:
            raise WorkflowValidationError(
                SCHEDULE_INVALID_CRON,
                "schedule（5 段式 cron）与 frequency_preset 须至少提供一项",
                details={
                    "workflow_id": self.workflow_id,
                    "trigger_node_id": self.trigger_node_id,
                },
            )
        return self
