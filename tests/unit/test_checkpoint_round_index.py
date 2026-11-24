# =============================================================================
# B12-D7 lite 专项测试（tests/unit/test_checkpoint_round_index.py）
#
# 覆盖验收：roadmap_b12_data_chunking design D7.1–D7.3（T5 改道，用户裁决）
#   1. 播种零拷贝：SharedState.for_seeding 值引用直通（免 JsonValue 深验证
#      复制）+ 缺省字段补全 + snapshot/序列化兼容 + from_framework_state 热路径；
#   2. same-round 侧车索引（rounds.idx）：索引命中吸收 / 可信缺席免扫描 /
#      无索引回退全扫描 / 陈旧项回退 / 损坏自愈；
#   3. 紧凑 dump：落盘文件无缩进且可解析回等值检查点。
# 既有测试零修改（同轮幂等行为护栏见 test_checkpoint_resume.py）。
# =============================================================================

import json
from unittest.mock import AsyncMock

from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.adapter.langgraph_state_mapping import (
    LangGraphStateMapping,
)
from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
    LocalFileCheckpointRepository,
)


def _mk_checkpoint(instance_id: str, round_index: int, checkpoint_id: str) -> Checkpoint:
    """构造测试检查点（小状态快照）。"""
    return Checkpoint(
        checkpoint_id=checkpoint_id,
        instance_id=instance_id,
        state_snapshot=SharedState(
            instance_id=instance_id, node_outputs={"round": round_index}
        ),
        round_index=round_index,
    )


# =============================================================================
# D7.1 播种零拷贝
# =============================================================================


class TestForSeedingZeroCopy:
    def test_seeding_shares_row_references(self):
        """零拷贝核心：播种后 node_outputs 值与输入为同一对象（is）。"""
        rows = [{"id": 1}, {"id": 2}]
        state = SharedState.for_seeding("inst-1", {"raw_data": rows})

        assert state.node_outputs["raw_data"] is rows

    def test_seeding_fills_defaults(self):
        """免验证构造仍补全全部缺省字段（与常规构造等价）。"""
        state = SharedState.for_seeding("inst-1", {})

        assert state.instance_id == "inst-1"
        assert state.node_outputs == {}
        assert state.loop_counters == {}
        assert state.branch_selections == {}
        assert state.current_node_id is None
        assert state.round_index == 0
        assert state.pending_diff == {}
        assert state.parallel_positions == {}
        assert state.parallel_arrivals == {}
        assert state.parallel_cancelled == {}

    def test_seeding_current_node_id_passthrough(self):
        state = SharedState.for_seeding("inst-1", {}, current_node_id="entry")

        assert state.current_node_id == "entry"

    def test_seeding_snapshot_compatible(self):
        """播种状态经 snapshot（B12-D2.1）后顶层隔离 + 值共享保持。"""
        rows = [{"id": 1}]
        state = SharedState.for_seeding("inst-1", {"raw_data": rows})
        snap = state.snapshot()

        state.write_output("extra", [{"id": 2}])

        assert "extra" not in snap.node_outputs
        assert snap.node_outputs["raw_data"] is rows

    def test_seeding_serialization_roundtrip(self):
        """播种状态经检查点序列化往返无损（JsonValue 兼容）。"""
        state = SharedState.for_seeding(
            "inst-1", {"raw_data": [{"id": 1, "name": "甲"}]}
        )

        restored = SharedState.model_validate_json(state.model_dump_json())

        assert restored == state

    def test_from_framework_state_shares_references(self):
        """langgraph 每步热路径（D7.1 第 5 点）：框架状态转换零拷贝。"""
        rows = [{"id": 1}]
        state_dict = {
            "instance_id": "inst-1",
            "node_outputs": {"raw_data": rows},
            "loop_counters": {"loop-1": 2},
            "branch_selections": {},
            "current_node_id": "node-2",
            "round_index": 3,
        }

        state = LangGraphStateMapping.from_framework_state(state_dict)

        assert state.node_outputs["raw_data"] is rows
        assert state.loop_counters == {"loop-1": 2}
        assert state.current_node_id == "node-2"
        assert state.round_index == 3


# =============================================================================
# D7.1a 执行结果零验证（base.NodeExecutionResult.output_value Any 直通）
# =============================================================================


class TestNodeExecutionResultZeroCopy:
    def test_output_value_passes_through(self):
        """执行器输出数据集经 NodeExecutionResult 构造零拷贝（is 同一对象）。"""
        from orchestration_platform.engine.nodes.base import NodeExecutionResult

        rows = [{"id": 1}, {"id": 2}]
        result = NodeExecutionResult(
            success=True, output_name="out", output_value=rows
        )

        assert result.output_value is rows

    def test_output_value_defaults_and_metadata(self):
        """缺省字段与 metadata 通道行为保持。"""
        from orchestration_platform.engine.nodes.base import NodeExecutionResult

        result = NodeExecutionResult(success=False, error_code="X")

        assert result.output_value is None
        assert result.output_name is None
        assert result.metadata == {}
        assert result.success is False

    def test_output_value_serialization_roundtrip(self):
        """Any 直通后 JSON 兼容值仍可正常序列化（JsonValue 语义等价）。"""
        import json

        from orchestration_platform.engine.nodes.base import NodeExecutionResult

        result = NodeExecutionResult(
            success=True,
            output_name="out",
            output_value={"rows": [{"id": 1, "name": "甲"}], "count": 1},
        )

        assert json.loads(result.model_dump_json())["output_value"] == {
            "rows": [{"id": 1, "name": "甲"}],
            "count": 1,
        }


# =============================================================================
# D7.2 same-round 侧车索引（rounds.idx）
# =============================================================================


class TestRoundIndexAbsorb:
    async def test_same_round_absorbed_via_index(self, tmp_path):
        """同轮重复写经索引吸收：返回既有标识、不产生新文件。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        first_id = await repo.append(_mk_checkpoint("inst-a", 1, "cp_a1"))
        assert first_id == "cp_a1"

        returned = await repo.append(_mk_checkpoint("inst-a", 1, "cp_a2"))

        assert returned == "cp_a1"
        files = list((tmp_path / "checkpoints" / "inst-a").glob("*.json"))
        assert len(files) == 1
        index = json.loads(
            (tmp_path / "checkpoints" / "inst-a" / "rounds.idx").read_text(
                encoding="utf-8"
            )
        )
        assert index == {"1": "cp_a1"}

    async def test_different_rounds_grow_index(self, tmp_path):
        """不同轮次正常写入，索引随轮次增长。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        for round_index in (0, 1, 2):
            await repo.append(
                _mk_checkpoint("inst-b", round_index, f"cp_b{round_index}")
            )

        files = list((tmp_path / "checkpoints" / "inst-b").glob("*.json"))
        assert len(files) == 3
        index = json.loads(
            (tmp_path / "checkpoints" / "inst-b" / "rounds.idx").read_text(
                encoding="utf-8"
            )
        )
        assert index == {"0": "cp_b0", "1": "cp_b1", "2": "cp_b2"}

    async def test_index_present_round_absent_skips_scan(self, tmp_path, monkeypatch):
        """写序不变量：索引存在且 round 缺席 → 免全量重解析放行。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        await repo.append(_mk_checkpoint("inst-c", 0, "cp_c0"))

        spy = AsyncMock(side_effect=AssertionError("不应触发全扫描"))
        monkeypatch.setattr(LocalFileCheckpointRepository, "_find_same_round", spy)

        returned = await repo.append(_mk_checkpoint("inst-c", 1, "cp_c1"))

        assert returned == "cp_c1"
        assert spy.await_count == 0

    async def test_no_index_falls_back_to_scan(self, tmp_path, monkeypatch):
        """无索引目录（旧数据）：回退全扫描同轮检查（旧行为）。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        await repo.append(_mk_checkpoint("inst-d", 0, "cp_d0"))
        # 人工删除索引模拟旧目录（文件在、索引缺）
        (tmp_path / "checkpoints" / "inst-d" / "rounds.idx").unlink()

        spy = AsyncMock(
            side_effect=LocalFileCheckpointRepository._find_same_round
        )
        monkeypatch.setattr(
            LocalFileCheckpointRepository, "_find_same_round",
            lambda self, instance_id, round_index: spy(self, instance_id, round_index),
        )

        returned = await repo.append(_mk_checkpoint("inst-d", 1, "cp_d1"))

        assert returned == "cp_d1"
        assert spy.await_count == 1

        # 后续写入重建索引（自愈）：下一轮次免扫描
        assert spy.await_count == 1
        returned_again = await repo.append(_mk_checkpoint("inst-d", 2, "cp_d2"))
        assert returned_again == "cp_d2"
        assert spy.await_count == 1

    async def test_stale_index_entry_falls_back_to_scan(self, tmp_path):
        """崩溃窗口陈旧项（索引指向缺失文件）：回退全扫描取真实既有检查点。"""
        instance_dir = tmp_path / "checkpoints" / "inst-e"
        instance_dir.mkdir(parents=True)
        # 真实检查点文件（无索引时代写入）
        real = _mk_checkpoint("inst-e", 1, "cp_e1")
        (instance_dir / "cp_e1.json").write_text(real.model_dump_json(), encoding="utf-8")
        # 陈旧索引：round 1 指向不存在的 ghost
        (instance_dir / "rounds.idx").write_text(
            json.dumps({"1": "cp_ghost"}), encoding="utf-8"
        )

        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        returned = await repo.append(_mk_checkpoint("inst-e", 1, "cp_e2"))

        assert returned == "cp_e1", "陈旧索引项应回退全扫描吸收真实同轮检查点"

    async def test_corrupt_index_self_heals(self, tmp_path):
        """损坏索引：回退全扫描，且下次成功写入时重建为有效索引。"""
        instance_dir = tmp_path / "checkpoints" / "inst-f"
        instance_dir.mkdir(parents=True)
        (instance_dir / "rounds.idx").write_text("not-json{", encoding="utf-8")

        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        returned = await repo.append(_mk_checkpoint("inst-f", 0, "cp_f0"))

        assert returned == "cp_f0"
        index = json.loads(
            (instance_dir / "rounds.idx").read_text(encoding="utf-8")
        )
        assert index == {"0": "cp_f0"}


# =============================================================================
# D7.3 紧凑 dump
# =============================================================================


class TestCompactDump:
    async def test_checkpoint_file_is_compact_json(self, tmp_path):
        """落盘检查点为紧凑 JSON（无缩进换行）且可解析回等值对象。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        cp = _mk_checkpoint("inst-g", 0, "cp_g0")
        await repo.append(cp)

        text = (tmp_path / "checkpoints" / "inst-g" / "cp_g0.json").read_text(
            encoding="utf-8"
        )
        assert "\n" not in text, "落盘应为紧凑 JSON（B12-D7.3）"

        restored = Checkpoint.model_validate_json(text)
        assert restored.checkpoint_id == cp.checkpoint_id
        assert restored.round_index == cp.round_index
        assert restored.state_snapshot == cp.state_snapshot
