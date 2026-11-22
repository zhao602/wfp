# =============================================================================
# 市场仓储工厂测试（tests/unit/test_market_repository_factory.py）
#
# 覆盖 T6.2 机检：local_file → 本地实现；postgres → 方法内延迟导入
# （模块顶层零 psycopg）；未知后端 → ValueError；既有三方法参数名与返回
# 类型注解不变（反射比对）；T5 结构性断言：新 PG 模块顶层零 psycopg /
# 零 asyncpg；T4.2 扩展位基础行为。
# 涉及不变式：Z-11、INV-D01 延伸、INV-D06、INV-D07。
# =============================================================================

import ast
import inspect
from pathlib import Path
from typing import Optional

import pytest

from orchestration_platform.market.models import ToolMarketEntry
from orchestration_platform.persistence.abstractions.market_repositories import (
    IAgentMarketRepository,
    IToolMarketRepository,
)
from orchestration_platform.persistence.abstractions.repositories import (
    ICheckpointRepository,
    IEventRepository,
    IWorkflowRepository,
)
from orchestration_platform.persistence.repository_factory import RepositoryFactory
from orchestration_platform.tool_discovery.models import ToolSource

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "orchestration_platform"
_NEW_POSTGRES_MODULES = [
    _SRC_ROOT / "persistence" / "postgres" / "postgres_agent_market_repository.py",
    _SRC_ROOT / "persistence" / "postgres" / "postgres_tool_market_repository.py",
]
_EXISTING_METHODS = [
    ("create_workflow_repository", IWorkflowRepository),
    ("create_checkpoint_repository", ICheckpointRepository),
    ("create_event_repository", IEventRepository),
]


def _top_level_import_names(source: str) -> set:
    """解析模块顶层（非函数体内）导入的模块名集合。"""
    tree = ast.parse(source)
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


def _assert_factory_method_signature(method, protocol) -> None:
    """断言工厂方法签名形态：storage_backend: Optional[str] = None → protocol。"""
    signature = inspect.signature(method)
    params = list(signature.parameters)
    assert params == ["storage_backend"]
    param = signature.parameters["storage_backend"]
    assert param.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert param.default is None
    assert param.annotation == Optional[str]
    assert signature.return_annotation is protocol


class TestExistingMethodsUnchanged:
    """既有三方法参数名与返回类型注解不变（反射比对，Z-11）。"""

    @pytest.mark.parametrize("method_name,protocol", _EXISTING_METHODS)
    def test_existing_method_signature_unchanged(self, method_name, protocol):
        method = getattr(RepositoryFactory, method_name)
        _assert_factory_method_signature(method, protocol)


class TestNewMarketFactoryMethods:
    """新增两静态方法：分支选择 + 延迟导入 + 未知后端 ValueError（CHG-08）。"""

    def test_create_agent_market_repository_local_file(self, tmp_path, monkeypatch):
        from orchestration_platform.persistence.local_file.local_file_agent_market_repository import (
            LocalFileAgentMarketRepository,
        )

        monkeypatch.chdir(tmp_path)
        repo = RepositoryFactory.create_agent_market_repository("local_file")
        assert isinstance(repo, LocalFileAgentMarketRepository)
        assert isinstance(repo, IAgentMarketRepository)

    def test_create_tool_market_repository_local_file(self, tmp_path, monkeypatch):
        from orchestration_platform.persistence.local_file.local_file_tool_market_repository import (
            LocalFileToolMarketRepository,
        )

        monkeypatch.chdir(tmp_path)
        repo = RepositoryFactory.create_tool_market_repository("local_file")
        assert isinstance(repo, LocalFileToolMarketRepository)
        assert isinstance(repo, IToolMarketRepository)

    def test_postgres_repositories_constructible_without_driver(self):
        """postgres 分支构造不触发 psycopg 导入（无驱动沙箱可实例化）。"""
        from orchestration_platform.persistence.postgres.postgres_agent_market_repository import (
            PostgresAgentMarketRepository,
        )
        from orchestration_platform.persistence.postgres.postgres_tool_market_repository import (
            PostgresToolMarketRepository,
        )

        agent_repo = RepositoryFactory.create_agent_market_repository("postgres")
        tool_repo = RepositoryFactory.create_tool_market_repository("postgres")
        assert isinstance(agent_repo, PostgresAgentMarketRepository)
        assert isinstance(tool_repo, PostgresToolMarketRepository)
        assert isinstance(agent_repo, IAgentMarketRepository)
        assert isinstance(tool_repo, IToolMarketRepository)

    @pytest.mark.parametrize(
        "method_name,protocol",
        [
            ("create_agent_market_repository", IAgentMarketRepository),
            ("create_tool_market_repository", IToolMarketRepository),
        ],
    )
    def test_new_method_signature_parity(self, method_name, protocol):
        _assert_factory_method_signature(
            getattr(RepositoryFactory, method_name), protocol
        )

    def test_unknown_backend_raises_value_error_agent(self):
        with pytest.raises(ValueError):
            RepositoryFactory.create_agent_market_repository("unsupported_backend")

    def test_unknown_backend_raises_value_error_tool(self):
        with pytest.raises(ValueError):
            RepositoryFactory.create_tool_market_repository("unsupported_backend")


class TestProtocolShape:
    """Protocol 方法集形态：IAgentMarketRepository 4 方法 / IToolMarketRepository 3 方法。"""

    def test_agent_market_repository_protocol_methods(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        repo = RepositoryFactory.create_agent_market_repository("local_file")
        for name in ("save", "get", "list_all", "exists"):
            assert hasattr(repo, name)
            assert inspect.iscoroutinefunction(getattr(repo, name))

    def test_tool_market_repository_protocol_methods(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        repo = RepositoryFactory.create_tool_market_repository("local_file")
        for name in ("save", "list_all", "exists"):
            assert hasattr(repo, name)
            assert inspect.iscoroutinefunction(getattr(repo, name))


class TestPostgresLazyImportStructure:
    """T5 结构性断言：模块顶层零 psycopg / 零 asyncpg，方法内延迟导入存在。"""

    @pytest.mark.parametrize("module_path", _NEW_POSTGRES_MODULES)
    def test_no_top_level_psycopg_or_asyncpg_imports(self, module_path):
        source = module_path.read_text(encoding="utf-8")
        names = _top_level_import_names(source)
        assert "psycopg" not in names
        assert "asyncpg" not in names

    @pytest.mark.parametrize("module_path", _NEW_POSTGRES_MODULES)
    def test_lazy_psycopg_import_exists_in_methods(self, module_path):
        source = module_path.read_text(encoding="utf-8")
        assert "import psycopg" in source
        assert "asyncpg" not in source


class TestLocalFileToolMarketRepositoryBasics:
    """T4.2：接口与 IToolMarketRepository 一致；机制同 T4.1（市场侧扩展位）。"""

    @staticmethod
    def _make_tool_entry(tool_identifier: str) -> ToolMarketEntry:
        return ToolMarketEntry(
            tool_identifier=tool_identifier,
            name="测试工具",
            source=ToolSource.PLATFORM,
        )

    async def test_save_and_list_round_trip(self, tmp_path):
        from orchestration_platform.persistence.local_file.local_file_tool_market_repository import (
            LocalFileToolMarketRepository,
        )

        repo = LocalFileToolMarketRepository(storage_root=str(tmp_path / "tool_market"))
        await repo.save(self._make_tool_entry("get_workflow"))
        entries = await repo.list_all()
        assert [e.tool_identifier for e in entries] == ["get_workflow"]
        assert entries[0].source == ToolSource.PLATFORM

    async def test_exists_round_trip(self, tmp_path):
        from orchestration_platform.persistence.local_file.local_file_tool_market_repository import (
            LocalFileToolMarketRepository,
        )

        repo = LocalFileToolMarketRepository(storage_root=str(tmp_path / "tool_market"))
        assert await repo.exists("get_workflow") is False
        await repo.save(self._make_tool_entry("get_workflow"))
        assert await repo.exists("get_workflow") is True
