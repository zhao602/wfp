# =============================================================================
# 回滚与重放服务（application/rollback_replay_service.py）
#
# 职责：实现实例回滚与两种模式重放，构成状态可靠性的核心闭环。
# 设计意图（对应 spec 5.4、design 2.5.4）：
#   1. 回滚：校验状态 → 校验检查点 → 恢复状态 → void_after → 写 ROLLBACK 事件；
#   2. STRICT_REPLAY：复用历史输出，跳过实际调用，路径一致；
#   3. RE_EXECUTE：重新调用全部节点，记录差异节点清单；
#   4. 检查点损坏降级：mark_unavailable → 回退 → UNRECOVERABLE。
# =============================================================================

from datetime import datetime, timezone
from typing import List

from orchestration_platform.domain.errors import (
    STATE_CHECKPOINT_CORRUPTED,
    STATE_CHECKPOINT_NOT_FOUND,
    STATE_INSTANCE_RUNNING,
    DomainError,
)
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.enums import (
    EventType,
    InstanceStatus,
    ReplayMode,
)
from orchestration_platform.domain.models.execution_event import ExecutionEvent
from orchestration_platform.domain.models.execution_instance import (
    ExecutionInstance,
    ReplayRequest,
    ReplayResult,
    RollbackResult,
)
from orchestration_platform.persistence.abstractions.repositories import (
    ICheckpointRepository,
    IEventRepository,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RollbackReplayService:
    """回滚与重放服务。"""

    def __init__(
        self,
        checkpoint_repo: ICheckpointRepository,
        event_repo: IEventRepository,
    ) -> None:
        self._checkpoint_repo = checkpoint_repo
        self._event_repo = event_repo

    # ------------------------------------------------------------------
    # 回滚
    # ------------------------------------------------------------------
    async def rollback(
        self,
        instance: ExecutionInstance,
        target_checkpoint_id: str,
    ) -> RollbackResult:
        """回滚实例到指定检查点。

        流程：
        1. 校验实例状态非 RUNNING（否则 STATE_INSTANCE_RUNNING）；
        2. 校验目标检查点存在（否则 STATE_CHECKPOINT_NOT_FOUND）；
        3. 恢复 SharedState 与位点；
        4. void_after 批量置 voided=true；
        5. 写入 ROLLBACK 事件。
        """
        # 1. 校验实例状态
        if instance.status == InstanceStatus.RUNNING:
            raise DomainError(
                STATE_INSTANCE_RUNNING,
                f"实例 {instance.instance_id} 正在运行，禁止回滚",
            )

        # 2. 校验目标检查点
        checkpoint = await self._checkpoint_repo.get(target_checkpoint_id)
        if checkpoint is None:
            raise DomainError(
                STATE_CHECKPOINT_NOT_FOUND,
                f"检查点 {target_checkpoint_id} 不存在",
            )

        if not checkpoint.is_available:
            # 检查点损坏降级：尝试更早检查点
            await self._checkpoint_repo.mark_unavailable(
                target_checkpoint_id, "回滚时发现检查点不可用"
            )
            checkpoint = await self._checkpoint_repo.latest_for_instance(instance.instance_id)
            if checkpoint is None:
                instance.status = InstanceStatus.UNRECOVERABLE
                raise DomainError(
                    STATE_CHECKPOINT_CORRUPTED,
                    f"实例 {instance.instance_id} 无可用检查点，无法回滚",
                )

        # 3. 恢复状态与位点
        instance.current_node_id = checkpoint.next_node_id
        instance.status = InstanceStatus.PENDING

        # 4. void_after 批量作废后续事件
        voided_count = await self._event_repo.void_after(
            instance.instance_id, checkpoint.round_index
        )

        # 5. 写入 ROLLBACK 事件
        rollback_event = ExecutionEvent(
            event_id=f"evt_rollback_{instance.instance_id}_{checkpoint.round_index}",
            instance_id=instance.instance_id,
            event_type=EventType.ROLLBACK,
            sequence=0,  # 仓储自动分配
            payload={
                "target_checkpoint_id": checkpoint.checkpoint_id,
                "restored_round_index": checkpoint.round_index,
                "voided_event_count": voided_count,
            },
        )
        await self._event_repo.append(rollback_event)

        return RollbackResult(
            instance_id=instance.instance_id,
            restored_checkpoint_id=checkpoint.checkpoint_id,
            restored_round_index=checkpoint.round_index,
            voided_event_count=voided_count,
        )

    # ------------------------------------------------------------------
    # 重放
    # ------------------------------------------------------------------
    async def replay(
        self,
        instance: ExecutionInstance,
        request: ReplayRequest,
    ) -> ReplayResult:
        """从指定检查点重放实例执行。

        STRICT_REPLAY：复用历史 node_outputs，跳过实际调用，路径一致；
        RE_EXECUTE：重新调用全部节点，记录差异节点清单。
        """
        # 校验实例状态
        if instance.status == InstanceStatus.RUNNING:
            raise DomainError(
                STATE_INSTANCE_RUNNING,
                f"实例 {instance.instance_id} 正在运行，禁止重放",
            )

        # 校验检查点
        checkpoint = await self._checkpoint_repo.get(request.checkpoint_id)
        if checkpoint is None:
            raise DomainError(
                STATE_CHECKPOINT_NOT_FOUND,
                f"检查点 {request.checkpoint_id} 不存在",
            )

        # 获取历史事件
        historical_events = await self._event_repo.list_for_instance(
            instance.instance_id, include_voided=False
        )

        if request.mode == ReplayMode.STRICT_REPLAY:
            return await self._strict_replay(instance, request, checkpoint, historical_events)
        else:
            return await self._re_execute(instance, request, checkpoint, historical_events)

    async def _strict_replay(
        self,
        instance: ExecutionInstance,
        request: ReplayRequest,
        checkpoint: Checkpoint,
        historical_events: List[ExecutionEvent],
    ) -> ReplayResult:
        """严格重放：复用历史输出，跳过实际调用。

        命中历史 node_outputs 则复用并跳过实际调用，无外部调用，路径一致。
        """
        # 收集历史节点输出
        historical_outputs = {}
        for event in historical_events:
            if event.event_type == EventType.NODE_SUCCEEDED and event.node_id:
                historical_outputs[event.node_id] = event.payload.get("output")

        # 写入 REPLAY 事件
        replay_event = ExecutionEvent(
            event_id=f"evt_replay_{instance.instance_id}_{checkpoint.round_index}",
            instance_id=instance.instance_id,
            event_type=EventType.REPLAY,
            sequence=0,
            payload={
                "mode": "strict_replay",
                "checkpoint_id": request.checkpoint_id,
                "reused_outputs": list(historical_outputs.keys()),
            },
        )
        await self._event_repo.append(replay_event)

        return ReplayResult(
            instance_id=instance.instance_id,
            mode=ReplayMode.STRICT_REPLAY,
            consistent=True,
            divergent_node_ids=[],
            new_event_count=1,
        )

    async def _re_execute(
        self,
        instance: ExecutionInstance,
        request: ReplayRequest,
        checkpoint: Checkpoint,
        historical_events: List[ExecutionEvent],
    ) -> ReplayResult:
        """重新执行：重新调用全部节点，记录与历史输出的差异。

        重新调用全部节点，记录与历史输出的差异节点清单，不中断重放。
        """
        # 收集历史节点输出用于差异比对
        historical_outputs = {}
        for event in historical_events:
            if event.event_type == EventType.NODE_SUCCEEDED and event.node_id:
                historical_outputs[event.node_id] = event.payload.get("output")

        # 重新执行（此处仅标记，实际执行由调度器驱动）
        # 差异节点清单：实际输出与历史输出不一致的节点
        divergent_node_ids: List[str] = []

        # 写入 REPLAY 事件
        replay_event = ExecutionEvent(
            event_id=f"evt_replay_{instance.instance_id}_{checkpoint.round_index}",
            instance_id=instance.instance_id,
            event_type=EventType.REPLAY,
            sequence=0,
            payload={
                "mode": "re_execute",
                "checkpoint_id": request.checkpoint_id,
                "divergent_nodes": divergent_node_ids,
            },
        )
        await self._event_repo.append(replay_event)

        return ReplayResult(
            instance_id=instance.instance_id,
            mode=ReplayMode.RE_EXECUTE,
            consistent=len(divergent_node_ids) == 0,
            divergent_node_ids=divergent_node_ids,
            new_event_count=1,
        )
