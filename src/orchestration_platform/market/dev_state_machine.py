# =============================================================================
# 用户开发提交状态机薄适配（market/dev_state_machine.py）
#
# 职责（agent_market_user_development T15 / CHG-05 / DC-04）：
#   1. DevSubmissionStateMachine.transition(submission, to_state, operator_id,
#      reason)：先调既有 ContributionStateMachine.can_transition() /
#      is_terminal()（纯函数，只读调用）判定；合法则自生成
#      StateTransitionRecord（复用既有模型）追加到 submission.state_history；
#   2. 非法流转 / 终态后再流转 → InstanceStateConflictError
#      （MARKET_DEV_INVALID_TRANSITION / 409），状态不变、不部分写入
#      （T15.2 / INV-D-20）；
#   3. 不新增并列状态机枚举（复用 ContributionState 七态，INV-DEV-16）；
#      state_machine.py 逐字节零改动（INV-D-19）。
# 约束：Python 3.10 兼容；零第三方新增依赖。
# =============================================================================

from datetime import datetime, timezone

from orchestration_platform.domain.errors import (
    MARKET_DEV_INVALID_TRANSITION,
    InstanceStateConflictError,
)
from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.market.dev_models import DevSubmission
from orchestration_platform.mcp_tool_market.contribution.models import (
    StateTransitionRecord,
)
from orchestration_platform.mcp_tool_market.contribution.state_machine import (
    ContributionStateMachine,
)


def _utcnow() -> datetime:
    """当前 UTC 时间（带时区，可序列化）。"""
    return datetime.now(timezone.utc)


class DevSubmissionStateMachine:
    """用户开发提交状态机（薄适配，DC-04）。

    迁移合法性完全委托既有 ContributionStateMachine 纯函数（七态语义，
    T15.2：DRAFT→SUBMITTED；SUBMITTED→SECURITY_SCAN|REJECTED；
    SECURITY_SCAN→REVIEWING|REJECTED|自环；REVIEWING→APPROVED|REJECTED；
    APPROVED→PUBLISHED；PUBLISHED/REJECTED 终态）；本类只做 DevSubmission
    主体的记录生成与字段更新。
    """

    @staticmethod
    def transition(
        submission: DevSubmission,
        to_state: ContributionState,
        operator_id: str,
        reason: str = "",
    ) -> DevSubmission:
        """执行状态迁移（合法时追加 StateTransitionRecord，T15.1）。

        Raises:
            InstanceStateConflictError: 非法流转 / 终态后再流转
                （MARKET_DEV_INVALID_TRANSITION / 409；主体不做任何变更）。
        """
        from_state = submission.state
        if ContributionStateMachine.is_terminal(from_state):
            raise InstanceStateConflictError(
                code=MARKET_DEV_INVALID_TRANSITION,
                message=(
                    f"提交 {submission.submission_id} 处于终态 "
                    f"{from_state.value}，不可流转"
                ),
                details={
                    "submission_id": submission.submission_id,
                    "from_state": from_state.value,
                    "to_state": to_state.value,
                },
            )
        if not ContributionStateMachine.can_transition(from_state, to_state):
            raise InstanceStateConflictError(
                code=MARKET_DEV_INVALID_TRANSITION,
                message=f"非法状态流转：{from_state.value} → {to_state.value}",
                details={
                    "submission_id": submission.submission_id,
                    "from_state": from_state.value,
                    "to_state": to_state.value,
                },
            )
        record = StateTransitionRecord(
            from_state=from_state,
            to_state=to_state,
            operator_id=operator_id,
            reason=reason,
        )
        submission.state = to_state
        submission.state_history.append(record)
        submission.updated_at = _utcnow()
        return submission
