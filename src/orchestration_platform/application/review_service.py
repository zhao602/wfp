# =============================================================================
# 人工审核决策服务（application/review_service.py）
#
# 职责：HITL review 决策处理——前置状态校验、approve 续跑、reject/timeout_action
#      终态映射、决策留痕（STATUS_CHANGED 执行事件 + governance.review_decision）。
# 设计意图（arch_review_v2_tasks_wave2 G2，design DC-27 / spec §6.4，CHG-45）：
#   1. 仅 WAITING_REVIEW 实例可提交决策（INSTANCE_NOT_WAITING_REVIEW → 409）；
#   2. approve → 经 Wave1 resume_checkpoint 机制续跑（挂起检查点复用，PA-7）；
#   3. reject → 按门节点 review_timeout_action 映射终态（不自动重跑）；
#   4. timeout_action → REVIEW_TIMEOUT 既有枚举值（零新增状态，INV-04）；
#   5. 决策留痕：ExecutionEvent(STATUS_CHANGED 既有成员) + P7 领域事件发布；
#   6. 并发决策以内存注册表状态更新为单写点（单实例部署边界，§6.3 R4）。
# =============================================================================

import logging
import uuid
from typing import Any, Callable, Dict, Optional

from orchestration_platform.domain.errors import (
    INSTANCE_NOT_WAITING_REVIEW,
    STATE_CHECKPOINT_NOT_FOUND,
    WF_VALIDATION_FAILED,
    DomainError,
    InstanceStateConflictError,
)
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.domain_event import (
    DEFAULT_TENANT_ID,
    DomainEvent,
    summarize_text,
)
from orchestration_platform.domain.models.enums import EventType, InstanceStatus
from orchestration_platform.domain.models.execution_event import ExecutionEvent
from orchestration_platform.engine.abstractions.domain_event_publisher import (
    IDomainEventPublisher,
)
from orchestration_platform.persistence.abstractions.repositories import (
    ICheckpointRepository,
    IEventRepository,
)


class ReviewDecisionService:
    """人工审核决策服务（DC-27）。"""

    def __init__(
        self,
        checkpoint_repo: ICheckpointRepository,
        event_repo: IEventRepository,
        domain_event_publisher: Optional[IDomainEventPublisher] = None,
    ) -> None:
        """构造注入与 RollbackReplayService 同范式（CHG-45/DC-27 规则 1）。

        domain_event_publisher：领域事件发布器（发布点 P7）；缺省 None 时
        零发布（决策留痕仍完整——执行事件通道独立于发布器，DC-31）。
        """
        self._checkpoint_repo = checkpoint_repo
        self._event_repo = event_repo
        self._domain_event_publisher = domain_event_publisher

    async def submit_decision(
        self,
        *,
        instance_id: str,
        decision: str,
        current_status: str,
        actor: str,
        comment: str = "",
        trace_id: str = "",
        tenant_id: str = DEFAULT_TENANT_ID,
        suspended_checkpoint_id: Optional[str] = None,
        get_gate_config: Callable[[str], Any],
        set_status: Callable[[str], None],
        resume_runner: Callable[[Checkpoint], Any],
    ) -> Dict[str, Any]:
        """处理一次人工审核决策，返回决策结果摘要（路由直接回显）。

        顺序（DC-27）：
        1. 决策值域校验（approve/reject/timeout_action 三值，spec §6.4 规则 1）；
        2. 前置状态校验：仅 WAITING_REVIEW 可提交（规则 2，E-W2-09 前置范式）；
        3. 挂起检查点解析：suspended_checkpoint_id 优先，重启场景
           latest_for_instance 回退（规则 3 / AC-01）；
        4. 终态映射：approve → running（续跑由 resume_runner 承担，不置终态）；
           reject → review_timeout_action 映射（wait→failed / 其余→cancelled）；
           timeout_action → review_timeout（design §4.3 终态映射）；
        5. set_status 单写点（并发决策收窄竞态，§6.3 R4）；
        6. 决策留痕：STATUS_CHANGED 执行事件 + governance.review_decision（P7）；
        7. approve 时 resume_runner(挂起检查点) 唤醒续跑。

        状态/上下文经回调注入而非服务直连路由注册表（服务层不依赖 API 层，
        与 RollbackReplayService 同分层边界）。
        """
        # 1. 决策值域校验（路由层 Literal 已挡一道，此处为服务层独立防线）
        if decision not in ("approve", "reject", "timeout_action"):
            raise DomainError(
                WF_VALIDATION_FAILED,
                f"非法决策值：{decision}（取值域 approve/reject/timeout_action）",
            )

        # 2. 前置状态校验（仅 WAITING_REVIEW；重复/并发决策天然被拒，幂等）
        if current_status != InstanceStatus.WAITING_REVIEW.value:
            raise InstanceStateConflictError(
                INSTANCE_NOT_WAITING_REVIEW,
                f"实例 {instance_id} 当前状态为 {current_status}，"
                f"仅等待审核（waiting_review）实例可提交审核决策",
            )

        # 3. 挂起检查点解析（内存登记优先；重启场景 latest_for_instance 回退）
        checkpoint: Optional[Checkpoint] = None
        if suspended_checkpoint_id:
            checkpoint = await self._checkpoint_repo.get(suspended_checkpoint_id)
        if checkpoint is None:
            checkpoint = await self._checkpoint_repo.latest_for_instance(instance_id)
        if checkpoint is None:
            raise DomainError(
                STATE_CHECKPOINT_NOT_FOUND,
                f"实例 {instance_id} 无可用挂起检查点，无法处理审核决策",
            )

        # 4. 终态映射
        gate_config = (
            get_gate_config(checkpoint.completed_node_id)
            if checkpoint.completed_node_id
            else None
        )
        # 词表对齐（design §4.3 以 cancel|fail 描述 reject 映射 ↔ 节点配置实际
        # 取值域 review_timeout_action: Literal["reject","wait"]，批次记录
        # reconcile 留痕）："wait" → fail 分支（FAILED）；
        # "reject"/缺省 → 默认 cancel 分支（CANCELLED）
        timeout_action = str(
            getattr(gate_config, "review_timeout_action", "") or "reject"
        )
        if decision == "approve":
            target_status = InstanceStatus.RUNNING.value
        elif decision == "reject":
            target_status = (
                InstanceStatus.FAILED.value
                if timeout_action == "wait"
                else InstanceStatus.CANCELLED.value
            )
        else:
            target_status = InstanceStatus.REVIEW_TIMEOUT.value

        # 5. 状态单写点（先置态后留痕后唤醒：崩溃窗口内决策可由事件流追溯）
        set_status(target_status)

        # 6. 决策留痕——执行事件（EventType.STATUS_CHANGED 既有成员）
        decision_event = ExecutionEvent(
            event_id=f"evt_review_{instance_id}_{uuid.uuid4().hex[:8]}",
            instance_id=instance_id,
            event_type=EventType.STATUS_CHANGED,
            sequence=0,  # 仓储自动分配
            node_id=checkpoint.completed_node_id,
            payload={
                "decision": decision,
                "actor": actor,
                "from_status": current_status,
                "to_status": target_status,
                "checkpoint_id": checkpoint.checkpoint_id,
                "comment": summarize_text(comment),
            },
        )
        await self._event_repo.append(decision_event)

        # 6b. 决策留痕——领域事件（发布点 P7；构造/发布失败静默旁路，DC-31）
        if self._domain_event_publisher is not None:
            try:
                review_event = DomainEvent(
                    event_type="governance.review_decision",
                    instance_id=instance_id,
                    tenant_id=tenant_id or DEFAULT_TENANT_ID,
                    trace_id=trace_id or instance_id,
                    node_id=checkpoint.completed_node_id,
                    payload={
                        "instance_id": instance_id,
                        "node_id": checkpoint.completed_node_id,
                        "decision": decision,
                        "actor": actor,
                        "comment": summarize_text(comment),
                    },
                )
                await self._domain_event_publisher.publish_domain_event(review_event)
            except Exception:
                logging.warning(
                    "governance.review_decision 领域事件发布失败（降级吞异常）:"
                    " instance_id=%s, decision=%s",
                    instance_id,
                    decision,
                    exc_info=True,
                )

        result: Dict[str, Any] = {
            "instance_id": instance_id,
            "decision": decision,
            "status": target_status,
            "node_id": checkpoint.completed_node_id,
            "checkpoint_id": checkpoint.checkpoint_id,
        }

        # 7. approve 唤醒续跑（resume_runner 承担重建 Scheduler + 后台任务）
        if decision == "approve":
            resume_runner(checkpoint)
            result["resumed_from_checkpoint_id"] = checkpoint.checkpoint_id

        return result
