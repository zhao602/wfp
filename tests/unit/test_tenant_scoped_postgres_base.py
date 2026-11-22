# =============================================================================
# 租户作用域 PostgreSQL 仓储基类行为断言
# （tests/unit/test_tenant_scoped_postgres_base.py）
#
# 职责：验证 TenantScopedPostgresRepository 的租户解析（fail-fast）、
#       RLS 会话变量注入（set_config 三参形式）、_ensure_session_tenant
#       外部连接路径语义（仅缺失时补设），以及六治理仓储 + 业务 3 仓储的
#       基类接入完整性（roadmap B1；实库 RLS 行为由 drill_rls.py 演练承担）。
# =============================================================================

from pathlib import Path
from typing import Any, List, Optional, Tuple

import pytest

import orchestration_platform.persistence.postgres.tenant_scoped_base as tsb_module
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantContextError,
    TenantScopedPostgresRepository,
)

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "orchestration_platform"
_GOVERNANCE_DIR = _SRC_ROOT / "governance" / "persistence" / "postgres"
_BUSINESS_DIR = _SRC_ROOT / "persistence" / "postgres"


# =============================================================================
# 测试替身：FakeAsyncConn / FakeAsyncPool（asyncpg 语义 mock，零真实库依赖）
# =============================================================================


class FakeTransaction:
    def __init__(self):
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1

    async def __aexit__(self, exc_type, exc, tb):
        self.exited += 1
        return False


class FakeAsyncConn:
    """asyncpg 连接语义 mock：记录 execute/fetchrow 调用与事务进出。"""

    def __init__(self, fetchrow_results: Optional[List[Any]] = None):
        self.execute_calls: List[Tuple[str, Tuple[Any, ...]]] = []
        self.fetchrow_calls: List[Tuple[str, Tuple[Any, ...]]] = []
        self.transactions: List[FakeTransaction] = []
        self._fetchrow_results = list(fetchrow_results or [])

    def transaction(self):
        t = FakeTransaction()
        self.transactions.append(t)
        return t

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return "OK"

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        if self._fetchrow_results:
            return self._fetchrow_results.pop(0)
        return None


class FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeAsyncPool:
    def __init__(self, conn):
        self._conn = conn
        self.acquire_count = 0

    def acquire(self):
        self.acquire_count += 1
        return FakeAcquire(self._conn)


class _StubSettings:
    def __init__(self, default_tenant_id: str = "tenant_default"):
        self.default_tenant_id = default_tenant_id


class _BareSettings:
    """无 default_tenant_id 属性的旧配置对象（向后兼容场景）。"""


def _patch_settings(monkeypatch, default_tenant_id: str = "tenant_default"):
    monkeypatch.setattr(
        tsb_module, "get_settings", lambda: _StubSettings(default_tenant_id)
    )


# =============================================================================
# A. 租户解析（fail-fast）
# =============================================================================


class TestTenantResolution:
    def test_resolve_tenant_explicit_param_wins(self, monkeypatch):
        """显式租户参数优先于默认租户。"""
        _patch_settings(monkeypatch)
        repo = TenantScopedPostgresRepository()
        assert repo._resolve_tenant("tenant-a") == "tenant-a"

    def test_resolve_tenant_none_falls_back_to_default(self, monkeypatch):
        """缺省参数回退 settings.default_tenant_id（Q3 裁决：默认租户兜底）。"""
        _patch_settings(monkeypatch)
        repo = TenantScopedPostgresRepository()
        assert repo._resolve_tenant(None) == "tenant_default"

    def test_resolve_tenant_empty_default_raises(self, monkeypatch):
        """默认租户未配置时 fail-fast 抛 TenantContextError；显式租户不受影响。"""
        _patch_settings(monkeypatch, default_tenant_id="")
        repo = TenantScopedPostgresRepository()
        with pytest.raises(TenantContextError):
            repo._resolve_tenant(None)
        assert repo._resolve_tenant("tenant-a") == "tenant-a"

    def test_resolve_tenant_settings_attribute_missing_raises(self, monkeypatch):
        """配置对象缺 default_tenant_id 属性时 fail-fast（向后兼容防御）。"""
        monkeypatch.setattr(tsb_module, "get_settings", lambda: _BareSettings())
        repo = TenantScopedPostgresRepository()
        with pytest.raises(TenantContextError):
            repo._resolve_default_tenant()


# =============================================================================
# B. 连接通道（RLS 会话变量注入）
# =============================================================================


class TestConnectionChannels:
    async def test_tenant_connection_sets_session_variable(self, monkeypatch):
        """_tenant_connection 事务级注入 app.current_tenant（set_config 三参形式）。"""
        _patch_settings(monkeypatch)
        conn = FakeAsyncConn()
        repo = TenantScopedPostgresRepository(pool=FakeAsyncPool(conn))
        async with repo._tenant_connection("tenant-a") as c:
            assert c is conn
        assert conn.execute_calls == [
            ("SELECT set_config('app.current_tenant', $1, true)", ("tenant-a",))
        ], "必须以 SELECT set_config(name, $1, true) 注入（SET LOCAL 不支持参数绑定）"

    async def test_tenant_connection_wraps_in_transaction(self, monkeypatch):
        """set_config(true) 为事务级——连接必须包在事务内，退出即提交。"""
        _patch_settings(monkeypatch)
        conn = FakeAsyncConn()
        repo = TenantScopedPostgresRepository(pool=FakeAsyncPool(conn))
        async with repo._tenant_connection("tenant-a"):
            pass
        assert len(conn.transactions) == 1, "单事务包装"
        assert conn.transactions[0].entered == 1, "事务进入"
        assert conn.transactions[0].exited == 1, "事务退出（提交）"

    async def test_admin_connection_sets_bypass(self):
        """_admin_connection 注入 app.bypass_rls='on'（运维/预租户上下文通道）。"""
        conn = FakeAsyncConn()
        repo = TenantScopedPostgresRepository(pool=FakeAsyncPool(conn))
        async with repo._admin_connection() as c:
            assert c is conn
        assert conn.execute_calls == [
            ("SELECT set_config('app.bypass_rls', 'on', true)", ())
        ], "运维旁路会话变量事务级注入"

    async def test_pool_injection_returns_injected_pool(self):
        """pool 注入构造兼容（B0 冒烟脚本口径：__init__(pool=pool) 继续可用）。"""
        conn = FakeAsyncConn()
        pool = FakeAsyncPool(conn)
        repo = TenantScopedPostgresRepository(pool=pool)
        assert await repo._get_pool() is pool


# =============================================================================
# C. _ensure_session_tenant（外部连接路径）
# =============================================================================


class TestEnsureSessionTenant:
    async def test_sets_when_missing(self):
        """会话未设租户变量时补设。"""
        conn = FakeAsyncConn(fetchrow_results=[{"current_tenant": None}])
        await TenantScopedPostgresRepository._ensure_session_tenant(conn, "tenant-a")
        assert conn.execute_calls == [
            ("SELECT set_config('app.current_tenant', $1, true)", ("tenant-a",))
        ]

    async def test_preserves_existing_context(self):
        """调用方已注入租户上下文时保持原值（不覆盖）。"""
        conn = FakeAsyncConn(fetchrow_results=[{"current_tenant": "tenant-b"}])
        await TenantScopedPostgresRepository._ensure_session_tenant(conn, "tenant-a")
        assert conn.execute_calls == [], "已注入的租户上下文不覆盖"


# =============================================================================
# D. 仓储接入完整性（六治理仓储 + 业务 3 仓储）
# =============================================================================


class TestRepositoryIntegration:
    def test_governance_repos_inherit_and_use_tenant_connection(self):
        """六治理仓储全部继承基类且方法经 _tenant_connection 注入。"""
        from orchestration_platform.governance.persistence.postgres.postgres_org_tree_repository import (
            PostgresOrgTreeRepository,
        )
        from orchestration_platform.governance.persistence.postgres.postgres_role_repository import (
            PostgresRoleRepository,
        )
        from orchestration_platform.governance.persistence.postgres.postgres_tenant_repository import (
            PostgresTenantRepository,
        )
        from orchestration_platform.governance.persistence.postgres.postgres_token_repository import (
            PostgresTokenRepository,
        )
        from orchestration_platform.governance.persistence.postgres.postgres_token_usage_repository import (
            PostgresTokenUsageRepository,
        )
        from orchestration_platform.governance.persistence.postgres.postgres_user_repository import (
            PostgresUserRepository,
        )

        classes = [
            PostgresUserRepository,
            PostgresTenantRepository,
            PostgresRoleRepository,
            PostgresOrgTreeRepository,
            PostgresTokenRepository,
            PostgresTokenUsageRepository,
        ]
        for cls in classes:
            assert issubclass(cls, TenantScopedPostgresRepository), (
                f"{cls.__name__} 必须继承 TenantScopedPostgresRepository"
            )
        for name in (
            "postgres_user_repository.py",
            "postgres_tenant_repository.py",
            "postgres_role_repository.py",
            "postgres_org_tree_repository.py",
            "postgres_token_repository.py",
            "postgres_token_usage_repository.py",
        ):
            source = (_GOVERNANCE_DIR / name).read_text(encoding="utf-8")
            assert "_tenant_connection(" in source, f"{name} 方法须经 _tenant_connection 注入"

    def test_admin_channel_methods(self):
        """预租户上下文入口走 _admin_connection：token 仓储 4 方法、tenant 仓储 list_tenants。"""
        token_src = (
            _GOVERNANCE_DIR / "postgres_token_repository.py"
        ).read_text(encoding="utf-8")
        assert token_src.count("self._admin_connection() as conn") == 4, (
            "get_by_hash / is_revoked / revoke_token / update_last_used 走旁路通道"
        )
        tenant_src = (
            _GOVERNANCE_DIR / "postgres_tenant_repository.py"
        ).read_text(encoding="utf-8")
        assert tenant_src.count("self._admin_connection() as conn") == 1, (
            "list_tenants 跨租户管理读走旁路通道"
        )

    def test_business_repos_optional_tenant_and_explicit_insert(self):
        """业务 3 仓储：继承基类 + 可选 tenant_id 参数 + INSERT 显式租户列。"""
        from orchestration_platform.persistence.postgres.postgres_checkpoint_repository import (
            PostgresCheckpointRepository,
        )
        from orchestration_platform.persistence.postgres.postgres_event_repository import (
            PostgresEventRepository,
        )
        from orchestration_platform.persistence.postgres.postgres_workflow_repository import (
            PostgresWorkflowRepository,
        )

        for cls in (
            PostgresWorkflowRepository,
            PostgresEventRepository,
            PostgresCheckpointRepository,
        ):
            assert issubclass(cls, TenantScopedPostgresRepository), (
                f"{cls.__name__} 必须继承 TenantScopedPostgresRepository"
            )

        wf = (_BUSINESS_DIR / "postgres_workflow_repository.py").read_text(encoding="utf-8")
        ev = (_BUSINESS_DIR / "postgres_event_repository.py").read_text(encoding="utf-8")
        cp = (
            _BUSINESS_DIR / "postgres_checkpoint_repository.py"
        ).read_text(encoding="utf-8")
        assert "(tenant_id, definition_id, definition_version, name, spec_version," in wf, (
            "workflow INSERT 列表显式含 tenant_id"
        )
        assert "(tenant_id, event_id, instance_id, event_type, node_id, sequence," in ev, (
            "event INSERT 列表显式含 tenant_id"
        )
        assert "(tenant_id, checkpoint_id, instance_id, completed_node_id, next_node_id," in cp, (
            "checkpoint INSERT 列表显式含 tenant_id"
        )
        for source in (wf, ev, cp):
            assert "tenant_id: Optional[str] = None" in source, (
                "方法签名含可选 tenant_id 参数（Q3 裁决）"
            )
            assert "_tenant_connection(" in source, "方法须经 _tenant_connection 注入"
