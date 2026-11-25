# =============================================================================
# 本地文件执行事件仓储（persistence/local_file/local_file_event_repository.py）
#
# 职责：以 JSON 文件持久化执行事件，保证不可变追加与序号严格递增。
# 设计意图（对应需求规格 4.6 降级要求、6.5、4.2.5、design 2.2.2.3）：
#   1. 目录结构：{root}/events/{instance_id}/events.json（单文件追加列表）
#   2. 不可变追加——void_after 仅置 voided=True，绝不物理删除；
#   3. sequence 严格递增——追加时校验新序号 = 当前最大序号 + 1。
# =============================================================================

import json
from pathlib import Path
from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import WF_VALIDATION_FAILED, DomainError
from orchestration_platform.domain.models.execution_event import ExecutionEvent


class LocalFileEventRepository:
    """执行事件仓储——本地文件实现。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        """构造仓储实例。

        参数：
            storage_root —— 存储根目录；缺省读取配置 local_storage_root。
        """
        root = storage_root or get_settings().local_storage_root
        self._base_dir = Path(root) / "events"

    def _instance_dir(self, instance_id: str) -> Path:
        """返回指定实例的事件目录路径。"""
        return self._base_dir / instance_id

    def _events_file(self, instance_id: str) -> Path:
        """返回指定实例的事件列表文件路径。"""
        return self._instance_dir(instance_id) / "events.json"

    def _load_events(self, instance_id: str) -> List[dict]:
        """加载实例的全部事件原始数据（JSON 列表）。"""
        file_path = self._events_file(instance_id)
        if not file_path.exists():
            return []
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_events(self, instance_id: str, events: List[dict]) -> None:
        """保存实例的全部事件列表。"""
        file_path = self._events_file(instance_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            json.dumps(events, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    async def append(self, event: ExecutionEvent) -> int:
        """追加执行事件，返回事件序号。

        保证同一实例内 sequence 严格递增。
        若事件已携带 sequence，校验其等于当前最大序号 + 1；
        若未携带（sequence=0 或负数），则自动分配。
        """
        events_raw = self._load_events(event.instance_id)
        current_max_seq = max((e.get("sequence", 0) for e in events_raw), default=0)

        if event.sequence > 0:
            # 校验携带的序号是否连续
            if event.sequence != current_max_seq + 1:
                raise DomainError(
                    WF_VALIDATION_FAILED,
                    f"事件序号不连续：期望 {current_max_seq + 1}，实际 {event.sequence}",
                )
            assigned_sequence = event.sequence
        else:
            assigned_sequence = current_max_seq + 1
            event = event.model_copy(update={"sequence": assigned_sequence})

        events_raw.append(event.model_dump(mode="json"))
        self._save_events(event.instance_id, events_raw)
        return assigned_sequence

    async def list_for_instance(
        self, instance_id: str, include_voided: bool = True
    ) -> List[ExecutionEvent]:
        """列出指定实例的全部事件，按 sequence 升序排列。"""
        events_raw = self._load_events(instance_id)
        events: List[ExecutionEvent] = []
        for raw in events_raw:
            if not include_voided and raw.get("voided", False):
                continue
            try:
                events.append(ExecutionEvent.model_validate(raw))
            except Exception:
                continue
        events.sort(key=lambda e: e.sequence)
        return events

    async def list_instance_ids(self) -> List[str]:
        """列出有事件目录的全部实例标识（B16 F4 审批队列扫描源）。"""
        if not self._base_dir.exists():
            return []
        return [
            entry.name
            for entry in self._base_dir.iterdir()
            if entry.is_dir() and self._events_file(entry.name).exists()
        ]

    async def void_after(self, instance_id: str, sequence: int) -> int:
        """将 sequence 大于给定值的事件批量标记为 voided=True。

        返回被作废的事件数量。绝不物理删除。
        """
        events_raw = self._load_events(instance_id)
        voided_count = 0
        for raw in events_raw:
            if raw.get("sequence", 0) > sequence and not raw.get("voided", False):
                raw["voided"] = True
                voided_count += 1

        if voided_count > 0:
            self._save_events(instance_id, events_raw)
        return voided_count
