# =============================================================================
# v1_5 治理表更名迁移静态断言用例（tests/unit/test_migration_v1_5_governance_rename.py）
#
# 覆盖验收（roadmap B0/D1，迁移建表 ↔ 仓储读写断链修复）：
#   1. v1_5 版本登记幂等（'1.5' + ON CONFLICT DO NOTHING）；
#   2. 八张治理表守卫更名（to_regclass 旧存在 + 新不存在）；
#   3. 六索引守卫更名 + governance_permission 建表与仓储 upsert 契约对齐；
#   4. 四组唯一约束与仓储 ON CONFLICT 列集一致；
#   5. 三张 LIST 分区表补建 DEFAULT 分区；
#   6. 回滚脚本逆操作对称（分区/约束/权限表/索引/表名/版本记录）；
#   7. 反 D1 断言：六仓储 governance_* 表名 ⊆ v1_5 交付表名集合，
#      v1_1/v1_2/回滚零复数表名 DDL 残留，token_usage upsert 对齐分区主键。
# =============================================================================

import re
from pathlib import Path

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "orchestration_platform"
    / "persistence"
    / "postgres"
    / "schema_migrations"
)
_REPO_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "orchestration_platform"
    / "governance"
    / "persistence"
    / "postgres"
)

# 八张治理表更名映射（v1_2 建表名 → 仓储契约表名）
_RENAMES = {
    "tenants": "governance_tenant",
    "tenant_quotas": "governance_tenant_quota",
    "org_nodes": "governance_org_node",
    "users": "governance_user",
    "roles": "governance_role",
    "role_bindings": "governance_role_binding",
    "access_tokens": "governance_access_token",
    "token_usage_records": "governance_token_usage",
}

_INDEX_RENAMES = {
    "idx_org_nodes_tenant": "idx_governance_org_node_tenant",
    "idx_org_nodes_parent": "idx_governance_org_node_parent",
    "idx_users_tenant": "idx_governance_user_tenant",
    "idx_role_bindings_user": "idx_governance_role_binding_user",
    "idx_access_tokens_hash": "idx_governance_access_token_hash",
    "idx_usage_tenant_time": "idx_governance_token_usage_tenant_time",
}

_UNIQUE_CONSTRAINTS = {
    "uq_governance_user_tenant_user": "UNIQUE (tenant_id, user_id)",
    "uq_governance_org_node_tenant_node": "UNIQUE (tenant_id, org_node_id)",
    "uq_governance_role_tenant_role": "UNIQUE (tenant_id, role_id)",
    "uq_governance_role_binding_tenant_binding": "UNIQUE (tenant_id, binding_id)",
}

_DEFAULT_PARTITIONS = (
    ("governance_token_usage_default", "governance_token_usage"),
    ("cost_records_default", "cost_records"),
    ("audit_log_entries_default", "audit_log_entries"),
)

# v1_5 交付的 governance_* 表名全集（八更名 + 一新建）
_GOV_TABLE_NAMES = frozenset(list(_RENAMES.values()) + ["governance_permission"])


def _read(name: str) -> str:
    path = _MIGRATIONS_DIR / name
    assert path.exists(), f"迁移文件缺失: {path}"
    return path.read_text(encoding="utf-8")


class TestV1_5VersionRegistration:
    """版本登记与版本删除幂等。"""

    def test_version_registered_idempotently(self) -> None:
        sql = _read("v1_5_rename_governance_tables.sql")
        assert "VALUES ('1.5'" in sql
        assert "ON CONFLICT (version) DO NOTHING" in sql

    def test_rollback_deletes_version(self) -> None:
        sql = _read("v1_5_rollback.sql")
        assert "DELETE FROM schema_versions WHERE version = '1.5'" in sql


class TestV1_5GuardedRenames:
    """守卫更名：八表 + 六索引 + 三 DEFAULT 分区。"""

    def test_all_table_renames_guarded(self) -> None:
        sql = _read("v1_5_rename_governance_tables.sql")
        for old, new in _RENAMES.items():
            assert f"to_regclass('public.{old}')" in sql, old
            assert f"to_regclass('public.{new}')" in sql, new
            assert f"ALTER TABLE public.{old} RENAME TO {new}" in sql

    def test_all_index_renames_guarded(self) -> None:
        sql = _read("v1_5_rename_governance_tables.sql")
        for old, new in _INDEX_RENAMES.items():
            assert f"to_regclass('public.{old}')" in sql, old
            assert f"to_regclass('public.{new}')" in sql, new
            assert f"ALTER INDEX public.{old} RENAME TO {new}" in sql

    def test_default_partitions_created(self) -> None:
        sql = _read("v1_5_rename_governance_tables.sql")
        for partition, parent in _DEFAULT_PARTITIONS:
            assert f"to_regclass('public.{partition}')" in sql, partition
            assert f"PARTITION OF public.{parent} DEFAULT" in sql

    def test_unique_constraints_match_repo_on_conflict(self) -> None:
        """四组唯一约束列集与六仓储 ON CONFLICT 列集一致。"""
        sql = _read("v1_5_rename_governance_tables.sql")
        for name, cols in _UNIQUE_CONSTRAINTS.items():
            assert f"ADD CONSTRAINT {name} {cols}" in sql
        repo_sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(_REPO_DIR.glob("postgres_*_repository.py"))
        }
        assert "ON CONFLICT (tenant_id, user_id) DO UPDATE" in repo_sources[
            "postgres_user_repository.py"
        ]
        assert "ON CONFLICT (tenant_id, org_node_id) DO UPDATE" in repo_sources[
            "postgres_org_tree_repository.py"
        ]
        assert "ON CONFLICT (tenant_id, role_id) DO UPDATE" in repo_sources[
            "postgres_role_repository.py"
        ]
        assert "ON CONFLICT (tenant_id, binding_id) DO UPDATE" in repo_sources[
            "postgres_role_repository.py"
        ]

    def test_permission_table_matches_repo_upsert(self) -> None:
        """governance_permission 建表主键与 save_permission upsert 契约对齐。"""
        sql = _read("v1_5_rename_governance_tables.sql")
        repo = (_REPO_DIR / "postgres_role_repository.py").read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS governance_permission" in sql
        assert "PRIMARY KEY (tenant_id, permission_id)" in sql
        for column in (
            "permission_id",
            "tenant_id",
            "resource_type",
            "operation",
            "scope",
            "description",
        ):
            assert column in sql, column
        assert "INSERT INTO governance_permission" in repo
        assert "ON CONFLICT (tenant_id, permission_id) DO UPDATE" in repo


class TestV1_5RollbackSymmetry:
    """回滚脚本逆操作对称。"""

    def test_rollback_reverses_table_renames(self) -> None:
        sql = _read("v1_5_rollback.sql")
        for old, new in _RENAMES.items():
            assert f"ALTER TABLE public.{new} RENAME TO {old}" in sql

    def test_rollback_reverses_index_renames(self) -> None:
        sql = _read("v1_5_rollback.sql")
        for old, new in _INDEX_RENAMES.items():
            assert f"ALTER INDEX public.{new} RENAME TO {old}" in sql

    def test_rollback_drops_partitions_and_permission_table(self) -> None:
        sql = _read("v1_5_rollback.sql")
        for partition, _parent in _DEFAULT_PARTITIONS:
            assert f"DROP TABLE IF EXISTS {partition}" in sql
        assert "DROP TABLE IF EXISTS governance_permission" in sql

    def test_rollback_drops_unique_constraints(self) -> None:
        sql = _read("v1_5_rollback.sql")
        for name in _UNIQUE_CONSTRAINTS:
            assert f"DROP CONSTRAINT IF EXISTS {name}" in sql


class TestAntiD1TableNameAlignment:
    """反 D1：仓储表名集合与迁移交付表名集合一致，零复数断链残留。"""

    def test_repo_table_names_covered_by_v1_5(self) -> None:
        """六仓储引用的 governance_* 表名 ⊆ v1_5 交付表名集合。"""
        sql = _read("v1_5_rename_governance_tables.sql")
        repo_files = sorted(_REPO_DIR.glob("postgres_*_repository.py"))
        assert len(repo_files) >= 6
        for repo_file in repo_files:
            repo_sql = repo_file.read_text(encoding="utf-8")
            used = set(re.findall(r"\b(governance_[a-z_]+)\b", repo_sql))
            for table in used:
                assert table in _GOV_TABLE_NAMES, (
                    f"{repo_file.name} 引用未登记表 {table}"
                )
                assert table in sql, f"v1_5 未覆盖仓储引用表 {table}（{repo_file.name}）"

    def test_no_plural_table_ddl_in_phase_migrations_and_rollbacks(self) -> None:
        """v1_1/v1_2 及其回滚零复数表名 DDL 残留（B0/D1 勘误后）。"""
        for name in (
            "v1_1_phase2_enhancement.sql",
            "v1_2_phase3_governance.sql",
            "v1_1_rollback.sql",
            "v1_2_rollback.sql",
        ):
            sql = _read(name)
            for plural in (
                "workflow_definitions",
                "execution_instances",
                "checkpoints",
                "events",
            ):
                pattern = rf"TABLE (IF EXISTS )?(public\.)?{plural}\b"
                assert not re.search(pattern, sql), (
                    f"{name} 残留复数表名 DDL: {plural}"
                )

    def test_token_usage_upsert_matches_partition_primary_key(self) -> None:
        """token_usage 仓储 upsert 冲突键对齐分区表主键 (tenant_id, record_id)。"""
        repo = (_REPO_DIR / "postgres_token_usage_repository.py").read_text(
            encoding="utf-8"
        )
        v1_2 = _read("v1_2_phase3_governance.sql")
        assert "PRIMARY KEY (tenant_id, record_id)" in v1_2
        assert "ON CONFLICT (tenant_id, record_id) DO NOTHING" in repo
        assert "ON CONFLICT (record_id)" not in repo

    def test_v1_1_registers_schema_versions_guard(self) -> None:
        """v1_1 版本 INSERT 前置 schema_versions 幂等建表守卫在位。"""
        v1_1 = _read("v1_1_phase2_enhancement.sql")
        assert "CREATE TABLE IF NOT EXISTS schema_versions" in v1_1
        insert_pos = v1_1.index("INSERT INTO schema_versions")
        create_pos = v1_1.index("CREATE TABLE IF NOT EXISTS schema_versions")
        assert create_pos < insert_pos

    def test_v1_1_gin_expression_index_parenthesized(self) -> None:
        """v1_1 GIN 表达式索引须整体加括号（演练暴露的第 3 类死语句回归）。"""
        v1_1 = _read("v1_1_phase2_enhancement.sql")
        assert "USING GIN ((descriptor->'tags'))" in v1_1
        assert "USING GIN (descriptor->'tags')" not in v1_1

    def test_v1_5_constraint_guards_use_to_regclass(self) -> None:
        """v1_5 执行语句禁用 ::regclass（缺表前态抛错且 AND 非短路，演练暴露）。"""
        sql = _read("v1_5_rename_governance_tables.sql")
        executable = "\n".join(
            line for line in sql.splitlines() if not line.lstrip().startswith("--")
        )
        assert "::regclass" not in executable
