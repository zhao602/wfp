# =============================================================================
# 仓储实现行为一致性单元测试（tests/unit/test_repository_implementations.py）
#
# 覆盖验收：A4、A5（双实现通过同一套行为一致性用例）
# =============================================================================

import pytest

from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser


class TestRepositoryImplementations:
    """本地文件仓储行为一致性测试。"""

    @pytest.fixture
    def sample_definition(self):
        """解析一个合法定义供仓储测试使用。"""
        parser = YamlWorkflowParser()
        from pathlib import Path
        yaml_text = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "valid_workflow_definitions"
            / "valid_sequential_four_nodes.yaml"
        ).read_text(encoding="utf-8")
        return parser.parse(yaml_text)

    @pytest.mark.asyncio
    async def test_save_and_load_workflow(self, local_workflow_repository, sample_definition):
        """保存后加载应得到等价定义。"""
        await local_workflow_repository.save(sample_definition)
        loaded = await local_workflow_repository.get(
            sample_definition.definition_id,
            sample_definition.definition_version,
        )
        assert loaded.definition_id == sample_definition.definition_id
        assert loaded.name == sample_definition.name
        assert len(loaded.nodes) == len(sample_definition.nodes)

    @pytest.mark.asyncio
    async def test_load_nonexistent_raises(self, local_workflow_repository):
        """加载不存在的定义应抛出异常。"""
        from orchestration_platform.domain.errors import ResourceNotFoundError
        with pytest.raises(ResourceNotFoundError):
            await local_workflow_repository.get("nonexistent", 1)

    @pytest.mark.asyncio
    async def test_event_sequence_strictly_increasing(self, local_event_repository):
        """事件序号应严格递增。"""
        from orchestration_platform.domain.models.execution_event import ExecutionEvent
        from orchestration_platform.domain.models.enums import EventType

        instance_id = "test-instance"
        sequences = []
        for i in range(5):
            event = ExecutionEvent(
                event_id=f"evt-{i}",
                instance_id=instance_id,
                sequence=0,
                event_type=EventType.NODE_SUCCEEDED,
                node_id=f"node_{i}",
                payload={},
            )
            seq = await local_event_repository.append(event)
            sequences.append(seq)

        for i in range(1, len(sequences)):
            assert sequences[i] > sequences[i - 1], "事件序号未严格递增"

    @pytest.mark.asyncio
    async def test_checkpoint_save_and_load(self, local_checkpoint_repository):
        """检查点保存后加载应得到等价状态。"""
        from orchestration_platform.domain.models.checkpoint import Checkpoint
        from orchestration_platform.domain.models.shared_state import SharedState

        instance_id = "test-instance"
        checkpoint = Checkpoint(
            checkpoint_id="cp-1",
            instance_id=instance_id,
            completed_node_id="node_a",
            state_snapshot=SharedState(
                instance_id=instance_id,
                node_outputs={"key": "value"},
            ),
            round_index=1,
        )
        await local_checkpoint_repository.append(checkpoint)
        loaded = await local_checkpoint_repository.latest_for_instance(instance_id)
        assert loaded is not None
        assert loaded.instance_id == instance_id
        assert loaded.completed_node_id == "node_a"

    @pytest.mark.asyncio
    async def test_checkpoint_history_ordered(self, local_checkpoint_repository):
        """检查点历史应按轮次有序。"""
        from orchestration_platform.domain.models.checkpoint import Checkpoint
        from orchestration_platform.domain.models.shared_state import SharedState

        instance_id = "test-instance"
        for rnd in [1, 3, 2]:
            cp = Checkpoint(
                checkpoint_id=f"cp-{rnd}",
                instance_id=instance_id,
                completed_node_id=f"node_{rnd}",
                state_snapshot=SharedState(instance_id=instance_id),
                round_index=rnd,
            )
            await local_checkpoint_repository.append(cp)

        history = await local_checkpoint_repository.list_for_instance(instance_id)
        round_indices = [cp.round_index for cp in history]
        assert round_indices == sorted(round_indices, reverse=True)
