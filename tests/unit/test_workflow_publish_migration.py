# =============================================================================
# 工作流定义发布态迁移与 PG 守卫断言
# （tests/unit/test_workflow_publish_migration.py）
#
# 职责：B11 定义版本与发布态批——v1_10 迁移脚本静态断言（幂等守卫/
#       列回填/索引/回滚配对）、table_definitions.sql 全量脚本同步、
#       PG 仓储发布态守卫 SQL（ON CONFLICT WHERE + RETURNING 单语句）
#       与发布态方法 SQL 静态断言、PG save 守卫行为（FakeAsyncConn 语义
#       mock，零真实库依赖——沿 test_tenant_scoped_postgres_base.py 惯例）。
# 设计意图（B11 design D2/D6）：
#   1. N 类禁试：不连库——迁移与 SQL 以文件内容断言锁定（静态 SQL 断言惯例）；
#   2. 发布态不可变守卫（写边界强制）：PG 单语句
#      ON CONFLICT DO UPDATE ... WHERE status <> 'published' RETURNING——
#      冲突且发布态 → 0 行 → fetchrow None → WF_STATE_CONFLICT；
#   3. 迁移 README 清单完整性：v1_9（M3 遗留勘误补录）+ v1_10 两行。
# =============================================================================

from pathlib import Path
from typing import Any, Optional

import pytest

from orchestration_platform.domain.errors import (
    WF_STATE_CONFLICT,
    InstanceStateConflictError,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import (
    WorkflowDefinition,
)
from orchestration_platform.persistence.abstractions.repositories import (
    IWorkflowRepository,
)
from orchestration_platform.persistence.local_file.local_file_workflow_repository import (
    LocalFileWorkflowRepository,
)
from orchestration_platform.persistence.postgres.postgres_workflow_repository import (
    PostgresWorkflowRepository,
)

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "orchestration_platform"
_PG_DIR = _SRC_ROOT / "persistence" / "postgres"
_MIGRATIONS_DIR = _PG_DIR / "schema_migrations"


def _make_definition(version: int = 1) -> WorkflowDefinition:
    """构造最小可保存工作流定义（单触发器节点）。"""
    return WorkflowDefinition(
        definition_id="wf-migration-assert",
        name="迁移断言测试",
        spec_version="1.0",
        definition_version=version,
        nodes=[
            NodeDefinition(
                node_id="trig",
                node_type=NodeType.TRIGGER,
                configuration={"trigger_type": "manual"},
            )
        ],
    )


# =============================================================================
# v1_10 迁移脚本静态断言（design D6）
# =============================================================================

class TestV10MigrationScript:
    def test_migration_file_exists_and_registers_version(self):
        sql = (
            _MIGRATIONS_DIR / "v1_10_workflow_publish_state.sql"
        ).read_text(encoding="utf-8")
        assert "INSERT INTO schema_versions (version, description)" in sql
        assert "'1.10'" in sql
        assert "ON CONFLICT (version) DO NOTHING" in sql

    def test_migration_idempotent_column_guards_with_draft_backfill(self):
        """三列幂等守卫 + status NOT NULL DEFAULT 'draft'（既有行回填口径）。"""
        sql = (
            _MIGRATIONS_DIR / "v1_10_workflow_publish_state.sql"
        ).read_text(encoding="utf-8")
        assert (
            "ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'draft'"
            in sql
        )
        assert "ADD COLUMN IF NOT EXISTS draft_of INTEGER" in sql
        assert "ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ" in sql
        assert (
            "CREATE INDEX IF NOT EXISTS idx_workflow_definition_status" in sql
        )
        assert "ON workflow_definition (definition_id, status)" in sql

    def test_migration_rollback_paired_and_idempotent(self):
        rollback = (
            _MIGRATIONS_DIR / "v1_10_rollback.sql"
        ).read_text(encoding="utf-8")
        assert "DROP INDEX IF EXISTS idx_workflow_definition_status" in rollback
        for column in ("status", "draft_of", "published_at"):
            assert (
                f"ALTER TABLE workflow_definition DROP COLUMN IF EXISTS {column}"
                in rollback
            )

    def test_table_definitions_synced_with_migration(self):
        """全量建表脚本与 v1_10 同步（新库直建全量；列序无关，列集一致）。"""
        sql = (_PG_DIR / "table_definitions.sql").read_text(encoding="utf-8")
        assert "status              VARCHAR(20)    NOT NULL DEFAULT 'draft'" in sql
        assert "draft_of            INTEGER" in sql
        assert "published_at        TIMESTAMPTZ" in sql
        assert "idx_workflow_definition_status" in sql

    def test_migrations_readme_lists_v1_9_and_v1_10(self):
        """README 清单补录 v1_9（M3 遗留）与 v1_10（本批）两行。"""
        readme = (_MIGRATIONS_DIR / "README.md").read_text(encoding="utf-8")
        assert "`v1_9_job_cancel_requested.sql`" in readme
        assert "`v1_10_workflow_publish_state.sql`" in readme


# =============================================================================
# PG 仓储发布态 SQL 静态断言（design D2/D4）
# =============================================================================

class TestPostgresPublishStateSql:
    def test_save_guard_single_statement_where_and_returning(self):
        """发布态守卫：ON CONFLICT WHERE status <> 'published' + RETURNING。"""
        source = (
            _PG_DIR / "postgres_workflow_repository.py"
        ).read_text(encoding="utf-8")
        assert "WHERE workflow_definition.status <> 'published'" in source
        assert "RETURNING status" in source
        assert "WF_STATE_CONFLICT" in source

    def test_save_insert_carries_publish_columns(self):
        source = (
            _PG_DIR / "postgres_workflow_repository.py"
        ).read_text(encoding="utf-8")
        assert (
            "definition_body, status, draft_of, published_at, created_at, updated_at)"
            in source
        )

    def test_get_published_status_predicate(self):
        source = (
            _PG_DIR / "postgres_workflow_repository.py"
        ).read_text(encoding="utf-8")
        assert "status='published'" in source
        assert "ORDER BY definition_version DESC LIMIT 1" in source

    def test_get_latest_version_max_aggregate(self):
        source = (
            _PG_DIR / "postgres_workflow_repository.py"
        ).read_text(encoding="utf-8")
        assert "MAX(definition_version)" in source

    def test_publish_double_writes_body_and_columns(self):
        """publish：definition_body 与状态列同事务双写（body 为 get() 权威）。"""
        source = (
            _PG_DIR / "postgres_workflow_repository.py"
        ).read_text(encoding="utf-8")
        assert (
            "SET definition_body=$4, status=$5, published_at=$6, updated_at=NOW()"
            in source
        )

    def test_list_versions_selects_publish_columns(self):
        source = (
            _PG_DIR / "postgres_workflow_repository.py"
        ).read_text(encoding="utf-8")
        assert (
            "SELECT definition_id, definition_version, status, published_at "
            in source
        )

    def test_both_backends_satisfy_extended_protocol(self):
        """双后端均满足扩展协议（runtime_checkable 方法存在性）。"""
        assert isinstance(LocalFileWorkflowRepository(), IWorkflowRepository)
        assert isinstance(PostgresWorkflowRepository(), IWorkflowRepository)


# =============================================================================
# PG save 发布态守卫行为（FakeAsyncConn 语义 mock，零真实库）
# =============================================================================

class _FakeConn:
    """最小 asyncpg 连接替身：fetchrow 可编程返回。"""

    def __init__(self, fetchrow_result: Optional[Any] = None) -> None:
        self._fetchrow_result = fetchrow_result
        self.executed: list = []

    async def fetchrow(self, *args, **kwargs):
        return self._fetchrow_result

    async def execute(self, *args, **kwargs):
        self.executed.append(args)


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeTenantConnection:
    """_tenant_connection 替身：async with 产出连接（绕开真实 asyncpg 事务）。"""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _pg_repo_with_conn(conn: _FakeConn) -> PostgresWorkflowRepository:
    repo = PostgresWorkflowRepository(pool=_FakePool(conn))
    repo._tenant_connection = lambda tenant_id=None: _FakeTenantConnection(conn)  # type: ignore[method-assign]
    return repo


class TestPostgresSaveGuardBehavior:
    async def test_conflict_on_published_returns_none_rows_then_raises(self):
        """ON CONFLICT WHERE 不满足 → fetchrow 返回 None → WF_STATE_CONFLICT。"""
        repo = _pg_repo_with_conn(_FakeConn(fetchrow_result=None))
        with pytest.raises(InstanceStateConflictError) as exc_info:
            await repo.save(_make_definition(version=1))
        assert exc_info.value.code == WF_STATE_CONFLICT

    async def test_fresh_insert_returns_row_and_ref(self):
        """无冲突（新版本号）→ RETURNING 行 → 正常返回富化 ref。"""
        repo = _pg_repo_with_conn(_FakeConn(fetchrow_result={"status": "draft"}))
        ref = await repo.save(_make_definition(version=2))
        assert ref.definition_version == 2
        assert ref.status == "draft"


# =============================================================================
# README 迁移原则符合性（原则 1/2/4：向前兼容默认值 + 幂等守卫）
# =============================================================================

class TestMigrationPrinciples:
    def test_new_columns_all_have_defaults_or_nullable(self):
        """向前兼容：新列要么 NOT NULL DEFAULT，要么可空（迁移原则 2）。"""
        sql = (
            _MIGRATIONS_DIR / "v1_10_workflow_publish_state.sql"
        ).read_text(encoding="utf-8")
        assert "NOT NULL DEFAULT 'draft'" in sql
        assert "ADD COLUMN IF NOT EXISTS draft_of INTEGER" in sql
        assert "ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ" in sql
        assert "DROP COLUMN" not in sql  # 非破坏性迁移（原则 1）
