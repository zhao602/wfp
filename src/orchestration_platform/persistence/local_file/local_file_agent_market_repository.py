# =============================================================================
# 本地文件 Agent 市场仓储（persistence/local_file/local_file_agent_market_repository.py）
#
# 职责：以 JSON 文件持久化 Agent 市场条目，单条目单 JSON。
# 设计意图（对应 design DC-02 / CHG-04、任务 T4.1、T4.3）：
#   1. 构造即 makedirs（对齐既有 LocalFileToolMarketRepository 范式）；
#   2. entry_id 转义为文件名（"/" 与 "\" 替换为 "_"）；
#   3. list_all 内损坏 JSON 跳过不抛错（DC-12 降级底座）；
#   4. 存储路径可由 settings.agent_market_storage_root 注入（T11 追加，
#      落地前回退约定默认值 ./.data/agent_market）。
# =============================================================================

from pathlib import Path
from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.market.models import AgentMarketEntry


def _default_storage_root() -> str:
    """解析缺省存储根目录。

    T11 落地 settings.agent_market_storage_root 后以该字段为准；
    此前回退约定默认值 ./.data/agent_market（DC-02）。
    """
    settings = get_settings()
    return getattr(settings, "agent_market_storage_root", "./.data/agent_market")


class LocalFileAgentMarketRepository:
    """Agent 市场条目仓储——本地文件实现（IAgentMarketRepository）。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        """构造仓储实例；构造即确保目录存在。

        参数：
            storage_root —— 存储根目录；缺省读取 settings.agent_market_storage_root
            （T11 追加；落地前回退 ./.data/agent_market）。
        """
        root = storage_root or _default_storage_root()
        self._base_dir = Path(root)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, entry_id: str) -> Path:
        """生成存储文件路径（entry_id 转义为文件名，单条目单 JSON）。"""
        safe_id = entry_id.replace("/", "_").replace("\\", "_")
        return self._base_dir / f"{safe_id}.json"

    async def save(self, entry: AgentMarketEntry) -> None:
        """保存条目（同标识幂等覆盖）。"""
        self._file_path(entry.entry_id).write_text(
            entry.model_dump_json(indent=2),
            encoding="utf-8",
        )

    async def get(self, entry_id: str) -> Optional[AgentMarketEntry]:
        """按标识读取条目；不存在返回 None。"""
        path = self._file_path(entry_id)
        if not path.exists():
            return None
        return AgentMarketEntry.model_validate_json(path.read_text(encoding="utf-8"))

    async def list_all(self) -> List[AgentMarketEntry]:
        """列出全部条目（按 entry_id 排序）；损坏 JSON 跳过不抛错。"""
        entries: List[AgentMarketEntry] = []
        for file_path in sorted(self._base_dir.glob("*.json")):
            try:
                entries.append(
                    AgentMarketEntry.model_validate_json(
                        file_path.read_text(encoding="utf-8")
                    )
                )
            except Exception:
                continue
        entries.sort(key=lambda e: e.entry_id)
        return entries

    async def exists(self, entry_id: str) -> bool:
        """检查条目是否存在。"""
        return self._file_path(entry_id).exists()
