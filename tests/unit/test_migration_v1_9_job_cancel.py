# =============================================================================
# v1_9 作业取消标志迁移静态断言
# （tests/unit/test_migration_v1_9_job_cancel.py）
#
# 职责：以 B0 静态断言口径核对 v1_9 前向迁移与回滚脚本的幂等守卫、
#       列约束与索引语义（roadmap-v3 中期窗口 M3，design D1：取消标志
#       载体 = execution_job，跨进程取消传播基底）。实库演练由 compose
#       环境承担（本测试环境无活 PG）。
# =============================================================================

from pathlib import Path

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src" / "orchestration_platform" / "persistence" / "postgres" / "schema_migrations"
)


def _read(name: str) -> str:
    return (_MIGRATIONS_DIR / name).read_text(encoding="utf-8")


def _strip_sql_comments(sql: str) -> str:
    """剥离 SQL 注释：B0 教训——纪律说明文本可能含被断言字面量，
    断言必须只针对可执行代码行。"""
    return "\n".join(
        line.split("--", 1)[0] if "--" in line else line
        for line in sql.splitlines()
    )


# =============================================================================
# A. v1_9_job_cancel_requested.sql（版本 1.9，取消标志）
# =============================================================================


class TestV1_9JobCancelRequested:

    def test_file_exists(self):
        assert (_MIGRATIONS_DIR / "v1_9_job_cancel_requested.sql").exists()

    def test_version_registration_idempotent(self):
        """版本登记 1.9 + ON CONFLICT 幂等（E10 范式）。"""
        sql = _strip_sql_comments(_read("v1_9_job_cancel_requested.sql"))
        assert "VALUES ('1.9'" in sql, "版本登记 1.9"
        assert "ON CONFLICT (version) DO NOTHING" in sql, "版本登记幂等（E10 范式）"

    def test_add_column_guarded_not_null_default_false(self):
        """列增补三守卫：IF NOT EXISTS 幂等 + NOT NULL + DEFAULT FALSE。"""
        sql = _strip_sql_comments(_read("v1_9_job_cancel_requested.sql"))
        assert "ADD COLUMN IF NOT EXISTS cancel_requested" in sql
        assert "BOOLEAN NOT NULL DEFAULT FALSE" in sql
        assert "ALTER TABLE execution_job" in sql

    def test_active_instance_index_guarded_partial(self):
        """实例活跃作业索引：IF NOT EXISTS 幂等 + 部分索引仅 pending/running。"""
        sql = _strip_sql_comments(_read("v1_9_job_cancel_requested.sql"))
        assert "CREATE INDEX IF NOT EXISTS idx_execution_job_instance_active" in sql
        assert "ON execution_job (instance_id, created_at)" in sql
        assert "WHERE status IN ('pending', 'running')" in sql

    def test_no_rls_changes(self):
        """v1_8 口径延续：不新增 ENABLE/NO ROW LEVEL SECURITY（worker 走
        bypass 通道）。"""
        sql = _strip_sql_comments(_read("v1_9_job_cancel_requested.sql"))
        assert "ENABLE ROW LEVEL SECURITY" not in sql
        assert "NO ROW LEVEL SECURITY" not in sql
        assert "POLICY" not in sql


# =============================================================================
# B. v1_9_rollback.sql（回滚）
# =============================================================================


class TestV1_9Rollback:

    def test_file_exists(self):
        assert (_MIGRATIONS_DIR / "v1_9_rollback.sql").exists()

    def test_version_deregistration(self):
        """回滚撤销 1.9 版本登记（唯一 DELETE 目标）。"""
        sql = _strip_sql_comments(_read("v1_9_rollback.sql"))
        assert "DELETE FROM schema_versions WHERE version = '1.9'" in sql

    def test_drop_index_guarded(self):
        sql = _strip_sql_comments(_read("v1_9_rollback.sql"))
        assert "DROP INDEX IF EXISTS idx_execution_job_instance_active" in sql

    def test_drop_column_guarded(self):
        sql = _strip_sql_comments(_read("v1_9_rollback.sql"))
        assert (
            "ALTER TABLE execution_job DROP COLUMN IF EXISTS cancel_requested" in sql
        )

    def test_rollback_only_touches_v1_9_artifacts(self):
        """回滚零越界：不触碰其他版本登记或无关对象。"""
        sql = _strip_sql_comments(_read("v1_9_rollback.sql"))
        assert "DELETE FROM" not in sql.replace(
            "DELETE FROM schema_versions WHERE version = '1.9'", ""
        )
        assert "DROP" not in sql.replace(
            "DROP INDEX IF EXISTS idx_execution_job_instance_active", ""
        ).replace("ALTER TABLE execution_job DROP COLUMN IF EXISTS cancel_requested", "")
