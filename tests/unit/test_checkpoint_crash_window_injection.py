# =============================================================================
# 检查点崩溃窗定向注入套件（tests/unit/test_checkpoint_crash_window_injection.py）
#
# 职责（roadmap B14 T4「kill-9 崩溃窗交叉引用」，design D5）：M3 kill-9
# drill（taskkill /F /T 强杀）实测的两处中途写窗，经对
# test_checkpoint_round_index.py 16 例（6 例 rounds.idx 自愈）覆盖分析
# 确认未被显式覆盖，按 D4 同范式补定向用例（既有 16 例文件零修改）：
#   W1 索引先行写序崩溃窗（append :134-144 先索引后数据文件——崩溃于
#      两者之间）：索引含幽灵项（round → 未写出的 checkpoint_id）且
#      无真实同轮文件 → 同轮再写回退全扫描放行新写 + 索引自愈指向
#      新真实文件；
#   W2 数据文件半写崩溃窗（崩溃于 write_text 中途）：索引指向半写
#      torn JSON → 同轮再写经「exists() 即信」吸收（B12-D7.2 文档化
#      口径，不重解析）；恢复路径 latest_for_instance 跳过 torn 回退
#      更早轮检查点（at-least-once 重放语义）。
# 交叉引用：m3_kill9_takeover drill 新鲜留档（_drills/
# m3_kill9_takeover_record.json，2026-11-25 PASS 6/6）覆盖真实强杀
# 全链路；本套件补静态窗语义。
# =============================================================================

import json

from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
    LocalFileCheckpointRepository,
)


def _mk_checkpoint(instance_id: str, round_index: int, checkpoint_id: str) -> Checkpoint:
    """构造测试检查点（与 test_checkpoint_round_index.py 同构口径）。"""
    return Checkpoint(
        checkpoint_id=checkpoint_id,
        instance_id=instance_id,
        state_snapshot=SharedState(
            instance_id=instance_id, node_outputs={"round": round_index}
        ),
        round_index=round_index,
    )


class TestIndexFirstCrashWindow:
    """W1：崩溃于「先索引后数据文件」写序之间（纯幽灵项，无真实文件）。"""

    async def test_w1_ghost_entry_falls_through_and_self_heals(self, tmp_path):
        """幽灵索引项（指向未写出的检查点）+ 无真实同轮文件 →
        同轮再写回退全扫描放行新写；索引自愈改指新真实文件。"""
        instance_dir = tmp_path / "checkpoints" / "inst-w1"
        instance_dir.mkdir(parents=True)
        # 模拟崩溃现场：rounds.idx 已更新，cp_ghost.json 未写出
        (instance_dir / "rounds.idx").write_text(
            json.dumps({"1": "cp_ghost"}), encoding="utf-8"
        )

        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        returned = await repo.append(_mk_checkpoint("inst-w1", 1, "cp_w1_new"))

        assert returned == "cp_w1_new", "无真实同轮文件 → 放行新写"
        assert (instance_dir / "cp_w1_new.json").exists()
        index = json.loads(
            (instance_dir / "rounds.idx").read_text(encoding="utf-8")
        )
        assert index == {"1": "cp_w1_new"}, "索引自愈：幽灵项改指新真实文件"


class TestTornDataFileCrashWindow:
    """W2：崩溃于数据文件 write_text 中途（索引指向半写 torn JSON）。"""

    async def test_w2_torn_file_absorbed_and_skipped_on_resume(self, tmp_path):
        """半写 torn 文件 + 索引指向它 → 同轮再写经 exists() 即信吸收
        （不重写、不重解析）；恢复路径跳过 torn 回退更早轮检查点。"""
        # 恢复基线：round 0 完整检查点
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        await repo.append(_mk_checkpoint("inst-w2", 0, "cp_w2_r0"))

        # 模拟 round 1 崩溃中途：索引先行已指向 cp_w2_r1，数据文件半写
        instance_dir = tmp_path / "checkpoints" / "inst-w2"
        index = json.loads(
            (instance_dir / "rounds.idx").read_text(encoding="utf-8")
        )
        index["1"] = "cp_w2_r1"
        (instance_dir / "rounds.idx").write_text(
            json.dumps(index), encoding="utf-8"
        )
        (instance_dir / "cp_w2_r1.json").write_text(
            '{"checkpoint_id": "cp_w2_r1", "ro', encoding="utf-8"
        )

        # 同轮再写：exists() 即信吸收（B12-D7.2 文档化口径）
        returned = await repo.append(_mk_checkpoint("inst-w2", 1, "cp_w2_r1_retry"))
        assert returned == "cp_w2_r1", "索引命中且文件存在 → 吸收返回既有标识"
        assert not (instance_dir / "cp_w2_r1_retry.json").exists(), "吸收不产生新文件"

        # 恢复路径：latest_for_instance 跳过 torn → 回退 round 0（at-least-once）
        latest = await repo.latest_for_instance("inst-w2")
        assert latest is not None
        assert latest.checkpoint_id == "cp_w2_r0"
        assert latest.round_index == 0
