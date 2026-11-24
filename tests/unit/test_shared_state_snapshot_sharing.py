# =============================================================================
# SharedState 快照结构共享测试（tests/unit/test_shared_state_snapshot_sharing.py）
#
# 覆盖验收：roadmap_b12_data_chunking T4（B12-D2.1/D2.3）
#   1. 顶层隔离：快照后 write_output / 计数 / 分支 / 并行三表更新不影响既有快照；
#   2. 值共享不变量：快照 node_outputs 值 is 原值（文档化不变量）；
#   3. 历史检查点稳定性：两检查点间继续执行，早期快照数据不变；
#   4. pending_diff 快照置空语义（瞬态工作区不入快照）。
# 安全前提：D2.2 不可变审计（写入即不可变）——本文件同时守护该前提不被回归。
# =============================================================================

from orchestration_platform.domain.models.shared_state import SharedState


def _state_with_data() -> SharedState:
    return SharedState(
        instance_id="inst-1",
        node_outputs={"trigger_out": [{"v": 1}, {"v": 2}]},
        loop_counters={"loop-1": 3},
        branch_selections={"branch-1": "node-b"},
        current_node_id="node-2",
        round_index=2,
        parallel_positions={"join-1": {"b1": "n1", "b2": "n2"}},
        parallel_arrivals={"join-1": ["b1"]},
        parallel_cancelled={"join-1": ["b2"]},
    )


class TestTopLevelIsolation:
    def test_write_output_after_snapshot_isolated(self):
        """快照后 write_output 不影响既有快照（顶层隔离语义不变）。"""
        state = _state_with_data()
        snap = state.snapshot()

        state.write_output("new_out", [{"v": 9}])

        assert "new_out" not in snap.node_outputs
        assert "new_out" in state.node_outputs

    def test_overwrite_after_snapshot_isolated(self):
        """快照后覆盖同名输出 → 既有快照保留旧值。"""
        state = _state_with_data()
        snap = state.snapshot()

        state.write_output("trigger_out", [{"v": 999}])

        assert snap.node_outputs["trigger_out"] == [{"v": 1}, {"v": 2}]
        assert state.node_outputs["trigger_out"] == [{"v": 999}]

    def test_loop_and_branch_tables_isolated(self):
        """循环计数/分支选择更新与既有快照隔离。"""
        state = _state_with_data()
        snap = state.snapshot()

        state.loop_counters["loop-1"] = 4
        state.loop_counters["loop-2"] = 1
        state.branch_selections["branch-1"] = "node-c"

        assert snap.loop_counters == {"loop-1": 3}
        assert snap.branch_selections == {"branch-1": "node-b"}

    def test_parallel_tables_isolated(self):
        """并行三表（含嵌套变异：位点推进/到达追加/取消追加）与既有快照隔离。"""
        state = _state_with_data()
        snap = state.snapshot()

        state.parallel_positions["join-1"]["b1"] = "n9"  # 嵌套位点推进
        state.parallel_positions["join-2"] = {"b3": "n3"}  # 顶层新区域
        state.parallel_arrivals["join-1"].append("b2")  # 嵌套到达追加
        state.parallel_cancelled["join-1"].append("b1")  # 嵌套取消追加
        state.parallel_arrivals["join-9"] = ["b9"]

        assert snap.parallel_positions == {"join-1": {"b1": "n1", "b2": "n2"}}
        assert snap.parallel_arrivals == {"join-1": ["b1"]}
        assert snap.parallel_cancelled == {"join-1": ["b2"]}

    def test_scalar_fields_copied(self):
        """标量字段（位点/轮次/实例标识）随快照复制。"""
        state = _state_with_data()
        snap = state.snapshot()

        state.current_node_id = "node-3"
        state.round_index = 5

        assert snap.current_node_id == "node-2"
        assert snap.round_index == 2
        assert snap.instance_id == "inst-1"


class TestValueSharing:
    def test_node_output_values_shared_by_reference(self):
        """值共享不变量：快照 node_outputs 值 is 原值（B12-D2.1 文档化不变量）。"""
        state = _state_with_data()
        rows = state.node_outputs["trigger_out"]

        snap = state.snapshot()

        assert snap.node_outputs["trigger_out"] is rows


class TestCheckpointStability:
    def test_early_snapshot_stable_across_progress(self):
        """两检查点间继续执行 → 早期快照数据不变（历史检查点稳定性）。"""
        state = _state_with_data()
        cp1 = state.snapshot()

        state.write_output("transform_out", [{"v": 3}, {"v": 4}, {"v": 5}])
        state.loop_counters["loop-1"] = 4
        cp2 = state.snapshot()

        state.write_output("final_out", [{"done": True}])

        # 早期检查点不受后续执行影响
        assert set(cp1.node_outputs) == {"trigger_out"}
        assert cp1.node_outputs["trigger_out"] == [{"v": 1}, {"v": 2}]
        assert cp1.loop_counters == {"loop-1": 3}
        # 中期检查点同样稳定
        assert set(cp2.node_outputs) == {"trigger_out", "transform_out"}
        assert cp2.loop_counters == {"loop-1": 4}
        # 活态包含全部
        assert set(state.node_outputs) == {"trigger_out", "transform_out", "final_out"}


class TestPendingDiffSnapshotSemantics:
    def test_pending_diff_emptied_in_snapshot(self):
        """pending_diff 为瞬态工作区：快照置空，活态 diff 不受影响。"""
        state = _state_with_data()
        state.pending_diff["wip"] = [{"v": 8}]

        snap = state.snapshot()

        assert snap.pending_diff == {}
        assert state.pending_diff == {"wip": [{"v": 8}]}

    def test_snapshot_roundtrip_via_serialization(self):
        """快照经序列化往返（检查点持久化路径）数据无损。"""
        state = _state_with_data()
        snap = state.snapshot()

        restored = SharedState.model_validate_json(snap.model_dump_json())

        assert restored == snap
        assert restored.node_outputs["trigger_out"] == [{"v": 1}, {"v": 2}]
