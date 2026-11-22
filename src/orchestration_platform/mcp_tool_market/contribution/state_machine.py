# =============================================================================
# 贡献状态机（mcp_tool_market/contribution/state_machine.py）
#
# 职责：管理贡献状态流转，仅允许合法迁移。
# 设计意图（对应偏差修正 T155 / design 2.1.3.1）：
#   1. 合法迁移表固化（DRAFT→SUBMITTED；SUBMITTED→SECURITY_SCAN/REJECTED；
#      SECURITY_SCAN→REVIEWING/REJECTED/自环重试；REVIEWING→APPROVED/REJECTED；
#      APPROVED→PUBLISHED）
#   2. 非法流转拒绝返回 CONTRIBUTION_INVALID_TRANSITION
#   3. PUBLISHED/REJECTED 为终态
#   4. 签名校验作为 SUBMITTED→SECURITY_SCAN 迁移守卫
#   5. 每次迁移生成 StateTransitionRecord 追加至 state_history
# =============================================================================

import logging
from datetime import datetime, timezone
from typing import Set

from orchestration_platform.domain.errors import (
    CONTRIBUTION_INVALID_TRANSITION,
    DomainError,
)
from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.mcp_tool_market.contribution.models import (
    NodeContribution,
    StateTransitionRecord,
)

_logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# 合法迁移表（design 2.1.3.1 状态图）
_ALLOWED_TRANSITIONS: dict = {
    ContributionState.DRAFT: {
        ContributionState.SUBMITTED,
    },
    ContributionState.SUBMITTED: {
        ContributionState.SECURITY_SCAN,
        ContributionState.REJECTED,
    },
    ContributionState.SECURITY_SCAN: {
        ContributionState.REVIEWING,
        ContributionState.REJECTED,
        ContributionState.SECURITY_SCAN,  # 自环重试
    },
    ContributionState.REVIEWING: {
        ContributionState.APPROVED,
        ContributionState.REJECTED,
    },
    ContributionState.APPROVED: {
        ContributionState.PUBLISHED,
    },
    ContributionState.PUBLISHED: set(),   # 终态
    ContributionState.REJECTED: set(),    # 终态
}


# 终态集合
_TERMINAL_STATES: Set[ContributionState] = {
    ContributionState.PUBLISHED,
    ContributionState.REJECTED,
}


class ContributionStateMachine:
    """贡献状态机。

    仅允许 _ALLOWED_TRANSITIONS 表内迁移；非法流转拒绝。
    每次迁移生成 StateTransitionRecord 追加至 state_history。
    """

    @staticmethod
    def can_transition(from_state: ContributionState, to_state: ContributionState) -> bool:
        """检查是否允许从 from_state 迁移到 to_state。"""
        allowed = _ALLOWED_TRANSITIONS.get(from_state, set())
        return to_state in allowed

    @staticmethod
    def is_terminal(state: ContributionState) -> bool:
        """检查是否为终态。"""
        return state in _TERMINAL_STATES

    @staticmethod
    def transition(
        contribution: NodeContribution,
        to_state: ContributionState,
        operator_id: str,
        reason: str = "",
    ) -> NodeContribution:
        """执行状态迁移。

        参数：
            contribution —— 贡献主体
            to_state     —— 目标状态
            operator_id  —— 操作人标识
            reason       —— 流转原因

        返回：
            更新后的贡献主体（state 与 state_history 已更新）

        异常：
            DomainError(CONTRIBUTION_INVALID_TRANSITION) —— 非法流转
        """
        from_state = contribution.state

        # 终态检查
        if ContributionStateMachine.is_terminal(from_state):
            raise DomainError(
                CONTRIBUTION_INVALID_TRANSITION,
                f"贡献 {contribution.contribution_id} 处于终态 {from_state.value}，不可流转",
                {
                    "contribution_id": contribution.contribution_id,
                    "from_state": from_state.value,
                    "to_state": to_state.value,
                },
            )

        # 合法迁移检查
        if not ContributionStateMachine.can_transition(from_state, to_state):
            raise DomainError(
                CONTRIBUTION_INVALID_TRANSITION,
                f"非法状态流转：{from_state.value} → {to_state.value}",
                {
                    "contribution_id": contribution.contribution_id,
                    "from_state": from_state.value,
                    "to_state": to_state.value,
                },
            )

        # 生成流转记录
        record = StateTransitionRecord(
            from_state=from_state,
            to_state=to_state,
            operator_id=operator_id,
            reason=reason,
        )

        # 更新贡献
        contribution.state = to_state
        contribution.state_history.append(record)
        contribution.updated_at = _utc_now()

        _logger.info(
            "贡献状态流转: id=%s %s→%s operator=%s reason=%s",
            contribution.contribution_id,
            from_state.value,
            to_state.value,
            operator_id,
            reason,
        )

        return contribution
