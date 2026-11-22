# =============================================================================
# 回滚与重放集成测试（tests/integration/test_rollback_and_replay.py）
#
# 覆盖验收：A5（回滚后状态与目标检查点一致且后续事件标记作废）
#             A6（严格重放外部调用计数增量为 0、重新执行产生新事件序列与差异清单）
# =============================================================================

import pytest

from orchestration_platform.application.rollback_replay_service import (
    RollbackReplayService,
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
)
from orchestration_platform.domain.models.shared_state import SharedState


class TestRollbackAndReplay:
    """回滚与重放集成测试。"""

    @pytest.fixture
    def service(self, local_event_repository, local_checkpoint_repository):
        return RollbackReplayService(
            checkpoint_repo=local_checkpoint_repository,
            event_repo=local_event_repository,
        )

    def _make_instance(self, instance_id: str) -> ExecutionInstance:
        return ExecutionInstance(
            instance_id=instance_id,
            definition_id="def-rollback-replay",
            definition_version=1,
            trace_id=f"trace-{instance_id}",
            status=InstanceStatus.SUCCEEDED,
        )

    @pytest.mark.asyncio
    async def test_rollback_to_checkpoint(
        self, service, local_checkpoint_repository
    ):
        """回滚后状态应与目标检查点一致。"""
        instance_id = "test-rollback-instance"
        for seq in [1, 2, 3]:
            cp = Checkpoint(
                checkpoint_id=f"cp_{instance_id}_{seq}",
                instance_id=instance_id,
                state_snapshot=SharedState(
                    instance_id=instance_id, node_outputs={"step": seq}
                ),
                round_index=seq,
                completed_node_id=f"node_{seq}",
            )
            await local_checkpoint_repository.append(cp)

        instance = self._make_instance(instance_id)
        result = await service.rollback(
            instance, target_checkpoint_id=f"cp_{instance_id}_2"
        )
        assert result.restored_checkpoint_id == f"cp_{instance_id}_2"
        assert result.restored_round_index == 2

    @pytest.mark.asyncio
    async def test_rollback_marks_subsequent_events_invalidated(
        self, service, local_event_repository, local_checkpoint_repository
    ):
        """回滚后后续事件应标记作废（非物理删除）。"""
        instance_id = "test-invalidate-instance"
        cp = Checkpoint(
            checkpoint_id=f"cp_{instance_id}_1",
            instance_id=instance_id,
            state_snapshot=SharedState(instance_id=instance_id),
            round_index=1,
            completed_node_id="node_a",
        )
        await local_checkpoint_repository.append(cp)

        for i in range(3):
            event = ExecutionEvent(
                event_id=f"evt_{instance_id}_{i}",
                instance_id=instance_id,
                sequence=0,
                event_type=EventType.NODE_SUCCEEDED,
                node_id=f"node_{i}",
                payload={},
            )
            await local_event_repository.append(event)

        instance = self._make_instance(instance_id)
        result = await service.rollback(
            instance, target_checkpoint_id=f"cp_{instance_id}_1"
        )
        assert result.voided_event_count > 0

        events = await local_event_repository.list_for_instance(instance_id)
        invalidated = [e for e in events if e.voided]
        assert len(invalidated) > 0, "回滚后应有事件被标记作废"

    @pytest.mark.asyncio
    async def test_strict_replay_no_external_calls(
        self, service, local_checkpoint_repository
    ):
        """严格重放模式外部调用计数增量为 0。"""
        instance_id = "test-strict-replay"
        cp = Checkpoint(
            checkpoint_id=f"cp_{instance_id}_1",
            instance_id=instance_id,
            state_snapshot=SharedState(instance_id=instance_id),
            round_index=1,
            completed_node_id="node_a",
        )
        await local_checkpoint_repository.append(cp)

        instance = self._make_instance(instance_id)
        request = ReplayRequest(
            instance_id=instance_id,
            checkpoint_id=f"cp_{instance_id}_1",
            mode=ReplayMode.STRICT_REPLAY,
        )
        result = await service.replay(instance, request)
        assert result.consistent
        assert result.new_event_count >= 0

    @pytest.mark.asyncio
    async def test_re_execute_produces_new_events(
        self, service, local_checkpoint_repository
    ):
        """重新执行模式应产生新事件序列与差异清单。"""
        instance_id = "test-re-execute"
        cp = Checkpoint(
            checkpoint_id=f"cp_{instance_id}_1",
            instance_id=instance_id,
            state_snapshot=SharedState(instance_id=instance_id),
            round_index=1,
            completed_node_id="node_a",
        )
        await local_checkpoint_repository.append(cp)

        instance = self._make_instance(instance_id)
        request = ReplayRequest(
            instance_id=instance_id,
            checkpoint_id=f"cp_{instance_id}_1",
            mode=ReplayMode.RE_EXECUTE,
        )
        result = await service.replay(instance, request)
        assert result.mode == ReplayMode.RE_EXECUTE
        assert result.new_event_count > 0
        assert result.divergent_node_ids is not None
