# =============================================================================
# 本地文件 Tool 市场仓储（persistence/local_file/local_file_tool_market_repository.py）
#
# 职责：以 JSON 文件持久化 Tool 市场条目元数据，单条目单 JSON。
# 设计意图（对应 design DC-02 / CHG-05、任务 T4.2、T4.3）：
#   1. 机制同 LocalFileAgentMarketRepository（构造即 makedirs / 转义 / 损坏跳过）；
#   2. 本类承载市场侧元数据扩展位——工具注册主落盘路径仍复用既有
#      mcp_tool_market 仓储（Z-06：既有仓储零改动），本类不替代；
#   3. 存储路径可由 settings.tool_market_storage_root 注入（T11 追加，
#      落地前回退约定默认值 ./.data/tool_market）。
# =============================================================================

from pathlib import Path
from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.market.models import ToolMarketEntry


def _default_storage_root() -> str:
    """解析缺省存储根目录。

    T11 落地 settings.tool_market_storage_root 后以该字段为准；
    此前回退约定默认值 ./.data/tool_market（DC-02）。
    """
    settings = get_settings()
    return getattr(settings, "tool_market_storage_root", "./.data/tool_market")


class LocalFileToolMarketRepository:
    """Tool 市场条目仓储——本地文件实现（IToolMarketRepository）。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        """构造仓储实例；构造即确保目录存在。

        参数：
            storage_root —— 存储根目录；缺省读取 settings.tool_market_storage_root
            （T11 追加；落地前回退 ./.data/tool_market）。
        """
        root = storage_root or _default_storage_root()
        self._base_dir = Path(root)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, tool_identifier: str) -> Path:
        """生成存储文件路径（tool_identifier 转义为文件名，单条目单 JSON）。"""
        safe_id = tool_identifier.replace("/", "_").replace("\\", "_")
        return self._base_dir / f"{safe_id}.json"

    async def save(self, entry: ToolMarketEntry) -> None:
        """保存条目（同标识幂等覆盖）。"""
        self._file_path(entry.tool_identifier).write_text(
            entry.model_dump_json(indent=2),
            encoding="utf-8",
        )

    async def list_all(self) -> List[ToolMarketEntry]:
        """列出全部条目（按 tool_identifier 排序）；损坏 JSON 跳过不抛错。"""
        entries: List[ToolMarketEntry] = []
        for file_path in sorted(self._base_dir.glob("*.json")):
            try:
                entries.append(
                    ToolMarketEntry.model_validate_json(
                        file_path.read_text(encoding="utf-8")
                    )
                )
            except Exception:
                continue
        entries.sort(key=lambda e: e.tool_identifier)
        return entries

    async def exists(self, tool_identifier: str) -> bool:
        """检查条目是否存在。"""
        return self._file_path(tool_identifier).exists()
