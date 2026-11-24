# =============================================================================
# 本地文件检查点仓储（persistence/local_file/local_file_checkpoint_repository.py）
#
# 职责：以 JSON 文件持久化检查点，按 instance_id 组织存储。
# 设计意图（对应需求规格 4.6 降级要求、6.4、design 2.2.2.3）：
#   1. 目录结构：{root}/checkpoints/{instance_id}/{checkpoint_id}.json
#   2. 追加写模型——同一 checkpoint_id 重复追加时抛错（不可变）；
#   3. latest_for_instance 返回 round_index 最大且 is_available=True 的检查点；
#   4. 同轮幂等（arch_review_v2 3.1，CHG-15/DC-09）：同一 (instance_id,
#      round_index) 重复追加时告警 + 返回既有标识，不抛异常——与 postgres
#      实现语义一致（重试/恢复场景的同轮重复写是合法重入）。
# =============================================================================

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import STATE_CHECKPOINT_CORRUPTED, DomainError
from orchestration_platform.domain.models.checkpoint import (
    Checkpoint,
    CheckpointSummary,
)

# 同轮索引文件名（B12-D7.2）：不以 .json 结尾——既有测试与读路径
# glob("*.json") 的文件计数/遍历不受扰
_ROUND_INDEX_FILENAME = "rounds.idx"


class LocalFileCheckpointRepository:
    """检查点仓储——本地文件实现。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        """构造仓储实例。

        参数：
            storage_root —— 存储根目录；缺省读取配置 local_storage_root。
        """
        root = storage_root or get_settings().local_storage_root
        self._base_dir = Path(root) / "checkpoints"

    def _instance_dir(self, instance_id: str) -> Path:
        """返回指定实例的检查点目录路径。"""
        return self._base_dir / instance_id

    def _checkpoint_file(self, instance_id: str, checkpoint_id: str) -> Path:
        """返回指定检查点的文件路径。"""
        return self._instance_dir(instance_id) / f"{checkpoint_id}.json"

    def _round_index_path(self, instance_id: str) -> Path:
        """返回指定实例的同轮索引文件路径（B12-D7.2）。"""
        return self._instance_dir(instance_id) / _ROUND_INDEX_FILENAME

    def _load_round_index(self, instance_id: str) -> Optional[Dict[str, str]]:
        """载入同轮索引（round -> checkpoint_id）。

        缺失或损坏返回 None（调用方回退全扫描；损坏在下次成功写入时
        经 _scan_rounds 重建自愈）。
        """
        index_path = self._round_index_path(instance_id)
        if not index_path.exists():
            return None
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return {str(k): str(v) for k, v in data.items()}

    def _scan_rounds(self, instance_id: str) -> Dict[str, str]:
        """全扫描实例目录重建同轮索引（旧目录一次性种子 / 损坏自愈）。

        仅提取 round_index 与 checkpoint_id 两个字段（json.loads，
        不做 pydantic 全量验证）；解析失败的文件跳过（与既有读路径
        容错口径一致）。
        """
        instance_dir = self._instance_dir(instance_id)
        index: Dict[str, str] = {}
        if not instance_dir.exists():
            return index
        for file_path in instance_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            round_index = data.get("round_index") if isinstance(data, dict) else None
            checkpoint_id = data.get("checkpoint_id") if isinstance(data, dict) else None
            if isinstance(round_index, int) and isinstance(checkpoint_id, str):
                index[str(round_index)] = checkpoint_id
        return index

    def _write_round_index(self, instance_id: str, index: Dict[str, str]) -> None:
        """写入同轮索引（先于检查点文件写——崩溃一致性写序，B12-D7.2）。"""
        index_path = self._round_index_path(instance_id)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(index, ensure_ascii=False), encoding="utf-8"
        )

    async def append(self, checkpoint: Checkpoint) -> str:
        """追加检查点（不可变写入；同轮幂等，arch_review_v2 3.1，CHG-15/DC-09）。

        同一 (instance_id, round_index) 已存在检查点时——重试/恢复场景的
        同轮重复写是合法重入：告警日志 + 返回既有检查点标识，
        不抛异常、不写入（与 postgres 实现语义一致，双实现语义一致）。
        B12-D7.2：同轮检查经 rounds.idx 侧车索引加速——写序不变量
        「先索引后检查点文件」保证「检查点文件存在 ⟹ 索引含其 round」，
        索引存在且 round 缺席即可免全量重解析放行；无索引/损坏/陈旧项
        三类情形回退全扫描（旧行为）。
        B12-D7.3：落盘为紧凑 JSON（缩进串 18.2MB → 紧凑 8.1MB，实测）。
        """
        file_path = self._checkpoint_file(checkpoint.instance_id, checkpoint.checkpoint_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        if file_path.exists():
            raise DomainError(
                STATE_CHECKPOINT_CORRUPTED,
                f"检查点 {checkpoint.checkpoint_id} 已存在，检查点为不可变追加写",
            )

        # 同轮幂等校验（新增分支：既有路径零影响——不同轮次的写入不经过此分支）
        existing_same_round_id = await self._find_same_round_id(checkpoint)
        if existing_same_round_id is not None:
            logging.warning(
                "检查点同轮重复写被幂等吸收: instance_id=%s round_index=%s"
                " 既有=%s 新写=%s（重试/恢复合法重入，不抛异常）",
                checkpoint.instance_id, checkpoint.round_index,
                existing_same_round_id, checkpoint.checkpoint_id,
            )
            return existing_same_round_id

        # 索引先行（写序不变量），再写检查点文件
        index = self._load_round_index(checkpoint.instance_id)
        if index is None:
            index = self._scan_rounds(checkpoint.instance_id)
        index[str(checkpoint.round_index)] = checkpoint.checkpoint_id
        self._write_round_index(checkpoint.instance_id, index)

        file_path.write_text(
            checkpoint.model_dump_json(),
            encoding="utf-8",
        )
        return checkpoint.checkpoint_id

    async def _find_same_round_id(self, checkpoint: Checkpoint) -> Optional[str]:
        """同轮幂等检查（索引优先，返回既有检查点标识；无则 None）。

        - 索引命中且指向文件存在 → 直接返回（免重解析）
        - 索引命中但文件缺失（崩溃窗口陈旧项）→ 回退全扫描
        - 索引存在且 round 缺席 → 写序不变量保证无同轮文件，免扫描返回 None
        - 索引缺失/损坏 → 回退全扫描（旧行为）
        """
        index = self._load_round_index(checkpoint.instance_id)
        if index is not None:
            existing_id = index.get(str(checkpoint.round_index))
            if existing_id is not None:
                if self._checkpoint_file(checkpoint.instance_id, existing_id).exists():
                    return existing_id
            else:
                return None
        legacy = await self._find_same_round(
            checkpoint.instance_id, checkpoint.round_index
        )
        if legacy is not None:
            return legacy.checkpoint_id
        return None

    async def _find_same_round(
        self, instance_id: str, round_index: int
    ) -> Optional[Checkpoint]:
        """查询同实例同轮次的既有检查点（同轮幂等校验用；不存在返回 None）。"""
        instance_dir = self._instance_dir(instance_id)
        if not instance_dir.exists():
            return None

        for file_path in instance_dir.glob("*.json"):
            try:
                cp = Checkpoint.model_validate_json(file_path.read_text(encoding="utf-8"))
                if cp.round_index == round_index:
                    return cp
            except Exception:
                continue
        return None

    async def get(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """按标识查询检查点。

        由于检查点按 instance_id 组织，需扫描全部实例目录查找。
        找到后若反序列化失败则返回 None（不抛异常，由调用方决定回退策略）。
        """
        if not self._base_dir.exists():
            return None

        for instance_dir in self._base_dir.iterdir():
            if not instance_dir.is_dir():
                continue
            file_path = instance_dir / f"{checkpoint_id}.json"
            if file_path.exists():
                try:
                    return Checkpoint.model_validate_json(
                        file_path.read_text(encoding="utf-8")
                    )
                except Exception:
                    return None
        return None

    async def latest_for_instance(self, instance_id: str) -> Optional[Checkpoint]:
        """查询指定实例的最新可用检查点（round_index 最大且 is_available=True）。"""
        instance_dir = self._instance_dir(instance_id)
        if not instance_dir.exists():
            return None

        checkpoints: List[Checkpoint] = []
        for file_path in instance_dir.glob("*.json"):
            try:
                cp = Checkpoint.model_validate_json(file_path.read_text(encoding="utf-8"))
                if cp.is_available:
                    checkpoints.append(cp)
            except Exception:
                continue

        if not checkpoints:
            return None

        return max(checkpoints, key=lambda c: c.round_index)

    async def list_for_instance(self, instance_id: str) -> List[CheckpointSummary]:
        """列出指定实例的全部检查点摘要，按 round_index 降序排列。"""
        instance_dir = self._instance_dir(instance_id)
        if not instance_dir.exists():
            return []

        summaries: List[CheckpointSummary] = []
        for file_path in instance_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                summaries.append(
                    CheckpointSummary(
                        checkpoint_id=data["checkpoint_id"],
                        instance_id=data["instance_id"],
                        completed_node_id=data.get("completed_node_id"),
                        next_node_id=data.get("next_node_id"),
                        round_index=data["round_index"],
                        created_at=data["created_at"],
                        is_available=data.get("is_available", True),
                    )
                )
            except Exception:
                continue

        summaries.sort(key=lambda s: s.round_index, reverse=True)
        return summaries

    async def mark_unavailable(self, checkpoint_id: str, reason: str) -> None:
        """将检查点标记为不可用。"""
        if not self._base_dir.exists():
            return

        for instance_dir in self._base_dir.iterdir():
            if not instance_dir.is_dir():
                continue
            file_path = instance_dir / f"{checkpoint_id}.json"
            if file_path.exists():
                data = json.loads(file_path.read_text(encoding="utf-8"))
                data["is_available"] = False
                data["unavailable_reason"] = reason
                file_path.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                return
