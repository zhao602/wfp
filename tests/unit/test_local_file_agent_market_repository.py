# =============================================================================
# 本地文件 Agent 市场仓储测试（tests/unit/test_local_file_agent_market_repository.py）
#
# 覆盖 T4.1 机检：保存 / 读取 / 列表 / 存在性 / 损坏 JSON 跳过 /
# 构造即建目录 / entry_id 转义 / 幂等覆盖 / 存储路径注入（T4.3）。
# 涉及不变式：INV-D09、DC-12；风险 R-9（降级底座）。
# =============================================================================

from types import SimpleNamespace

import orchestration_platform.persistence.local_file.local_file_agent_market_repository as agent_market_repo_module
from orchestration_platform.market.models import AgentMarketEntry
from orchestration_platform.persistence.abstractions.market_repositories import (
    IAgentMarketRepository,
)
from orchestration_platform.persistence.local_file.local_file_agent_market_repository import (
    LocalFileAgentMarketRepository,
)


def _make_entry(entry_id: str, name: str = "测试条目") -> AgentMarketEntry:
    """构造最小合法 Agent 市场条目。"""
    return AgentMarketEntry(entry_id=entry_id, name=name)


class TestConstructor:
    """构造行为：构造即 makedirs + 存储路径注入（T4.3）。"""

    def test_constructor_creates_missing_directory(self, tmp_path):
        root = tmp_path / "agent_market"
        assert not root.exists()
        LocalFileAgentMarketRepository(storage_root=str(root))
        assert root.exists()

    def test_implements_iagent_market_repository(self, tmp_path):
        repo = LocalFileAgentMarketRepository(
            storage_root=str(tmp_path / "agent_market")
        )
        assert isinstance(repo, IAgentMarketRepository)

    async def test_default_root_injected_from_settings_field(
        self, tmp_path, monkeypatch
    ):
        """T4.3：路径可由 settings.agent_market_storage_root 字段注入。"""
        root = tmp_path / "from_settings"
        monkeypatch.setattr(
            agent_market_repo_module,
            "get_settings",
            lambda: SimpleNamespace(agent_market_storage_root=str(root)),
        )
        repo = LocalFileAgentMarketRepository()
        await repo.save(_make_entry("e-settings"))
        assert (root / "e-settings.json").exists()

    async def test_default_root_fallback_before_settings_field(
        self, tmp_path, monkeypatch
    ):
        """T11 字段落地前回退约定默认值 ./.data/agent_market（chdir 隔离）。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            agent_market_repo_module,
            "get_settings",
            lambda: SimpleNamespace(),
        )
        repo = LocalFileAgentMarketRepository()
        await repo.save(_make_entry("e-fallback"))
        assert (tmp_path / ".data" / "agent_market" / "e-fallback.json").exists()


class TestSaveAndGet:
    """保存与读取语义（单条目单 JSON / 幂等覆盖 / 转义）。"""

    async def test_save_writes_single_json_file(self, tmp_path):
        root = tmp_path / "agent_market"
        repo = LocalFileAgentMarketRepository(storage_root=str(root))
        await repo.save(_make_entry("e-1"))
        files = list(root.glob("*.json"))
        assert len(files) == 1
        assert files[0].name == "e-1.json"

    async def test_get_round_trips_entry(self, tmp_path):
        repo = LocalFileAgentMarketRepository(
            storage_root=str(tmp_path / "agent_market")
        )
        entry = _make_entry("e-rt", "往返条目")
        await repo.save(entry)
        loaded = await repo.get("e-rt")
        assert loaded is not None
        assert loaded.entry_id == "e-rt"
        assert loaded.name == "往返条目"
        assert loaded.status == entry.status
        assert loaded.source_kind == entry.source_kind
        assert loaded.created_at == entry.created_at
        assert loaded == entry

    async def test_get_missing_returns_none(self, tmp_path):
        repo = LocalFileAgentMarketRepository(
            storage_root=str(tmp_path / "agent_market")
        )
        assert await repo.get("missing-entry") is None

    async def test_save_idempotent_overwrite(self, tmp_path):
        root = tmp_path / "agent_market"
        repo = LocalFileAgentMarketRepository(storage_root=str(root))
        await repo.save(_make_entry("e-1", "第一版"))
        await repo.save(_make_entry("e-1", "第二版"))
        assert len(list(root.glob("*.json"))) == 1
        loaded = await repo.get("e-1")
        assert loaded is not None
        assert loaded.name == "第二版"

    async def test_entry_id_escaping(self, tmp_path):
        root = tmp_path / "agent_market"
        repo = LocalFileAgentMarketRepository(storage_root=str(root))
        await repo.save(_make_entry("group/item-1", "转义条目"))
        assert (root / "group_item-1.json").exists()
        assert not (root / "group").exists()
        loaded = await repo.get("group/item-1")
        assert loaded is not None
        assert loaded.name == "转义条目"


class TestListAll:
    """列表语义：排序 / 空目录 / 损坏 JSON 跳过（DC-12 降级底座）。"""

    async def test_list_all_returns_entries_sorted_by_entry_id(self, tmp_path):
        repo = LocalFileAgentMarketRepository(
            storage_root=str(tmp_path / "agent_market")
        )
        await repo.save(_make_entry("b-entry"))
        await repo.save(_make_entry("a-entry"))
        entries = await repo.list_all()
        assert [e.entry_id for e in entries] == ["a-entry", "b-entry"]

    async def test_list_all_empty_directory_returns_empty_list(self, tmp_path):
        repo = LocalFileAgentMarketRepository(
            storage_root=str(tmp_path / "agent_market")
        )
        assert await repo.list_all() == []

    async def test_list_all_skips_corrupted_json_without_raising(self, tmp_path):
        root = tmp_path / "agent_market"
        repo = LocalFileAgentMarketRepository(storage_root=str(root))
        await repo.save(_make_entry("valid-entry"))
        (root / "broken.json").write_text("{ 这不是合法 JSON", encoding="utf-8")
        entries = await repo.list_all()
        assert [e.entry_id for e in entries] == ["valid-entry"]


class TestExists:
    """存在性语义。"""

    async def test_exists_true_after_save(self, tmp_path):
        repo = LocalFileAgentMarketRepository(
            storage_root=str(tmp_path / "agent_market")
        )
        await repo.save(_make_entry("e-exists"))
        assert await repo.exists("e-exists") is True

    async def test_exists_false_when_missing(self, tmp_path):
        repo = LocalFileAgentMarketRepository(
            storage_root=str(tmp_path / "agent_market")
        )
        assert await repo.exists("missing-entry") is False
