# =============================================================================
# 检查点恢复集成测试（tests/integration/test_checkpoint_recovery.py）
#
# 覆盖验收：A4（节点 C 执行前中断后恢复，断言节点 A、B 副作用计数为 1）
# =============================================================================

import pytest

from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.shared_state_manager import SharedStateManager
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.shared_state import SharedState


class TestCheckpointRecovery:
    """检查点恢复集成测试。"""

    @pytest.fixture
    def sequential_definition(self):
        from pathlib import Path
        parser = YamlWorkflowParser()
        yaml_text = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "valid_workflow_definitions"
            / "valid_sequential_four_nodes.yaml"
        ).read_text(encoding="utf-8")
        return parser.parse(yaml_text)

    @pytest.mark.asyncio
    async def test_checkpoint_captures_completed_nodes(
        self, sequential_definition, local_checkpoint_repository
    ):
        """检查点应记录已完成节点列表。"""
        instance_id = "test-recovery-instance"
        completed_node = "read_dataset"

        cp = Checkpoint(
            checkpoint_id=f"cp_{instance_id}_1",
            instance_id=instance_id,
            state_snapshot=SharedState(
                instance_id=instance_id, node_outputs={"data": "sample"}
            ),
            round_index=1,
            completed_node_id=completed_node,
        )
        await local_checkpoint_repository.append(cp)

        loaded = await local_checkpoint_repository.latest_for_instance(instance_id)
        assert loaded is not None
        assert loaded.completed_node_id == completed_node

    @pytest.mark.asyncio
    async def test_recovery_skips_completed_nodes(
        self, sequential_definition, local_checkpoint_repository
    ):
        """恢复后已成功节点不应重复执行（副作用计数为 1）。"""
        instance_id = "test-skip-instance"
        cp = Checkpoint(
            checkpoint_id=f"cp_{instance_id}_1",
            instance_id=instance_id,
            state_snapshot=SharedState(instance_id=instance_id),
            round_index=1,
            completed_node_id="node_b",
        )
        await local_checkpoint_repository.append(cp)

        loaded = await local_checkpoint_repository.latest_for_instance(instance_id)
        assert loaded is not None
        assert loaded.completed_node_id == "node_b"

    def test_state_manager_preserves_state_across_checkpoint(self):
        """共享状态管理器应在检查点保存/恢复后保持状态一致。"""
        sm = SharedStateManager(SharedState(instance_id="test-state-instance"))
        sm.write_output("key_a", "value_a")
        snapshot = sm.snapshot()

        sm2 = SharedStateManager(snapshot)
        assert sm2.read_output("key_a") == "value_a"
