# =============================================================================
# v1_6 / v1_7 多租户强隔离迁移静态断言
# （tests/unit/test_migration_v1_6_v1_7_tenant_rls.py）
#
# 职责：以 B0 静态断言口径核对 v1_6（租户列收紧）与 v1_7（RLS 策略）迁移及
#       回滚脚本的幂等守卫、覆盖矩阵与策略语义（roadmap B1 验收：业务表策略
#       覆盖 100%；无租户上下文查询返回 0 行；跨租户用例 ≥20 由 drill_rls.py
#       实库演练承担，实库行为 N 移交 drill-record.md）。
# =============================================================================

import re
from pathlib import Path

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src" / "orchestration_platform" / "persistence" / "postgres" / "schema_migrations"
)

# 8 张 tenant_id 可空表（v1_6 收紧目标：核心 4 + 4 张孤儿）
_NULLABLE_8 = [
    "workflow_definition",
    "execution_instance",
    "checkpoint",
    "execution_event",
    "pricing_rules",
    "mask_rules",
    "scheduler_policies",
    "resource_pools",
]

# 24 处 RLS 目标 = 21 张含租户表 + 3 个 DEFAULT 分区（roadmap B1 验收口径）
_EXPECTED_TARGETS = [
    # 核心 4（业务仓储读写，v1_2 补列）
    "workflow_definition",
    "execution_instance",
    "checkpoint",
    "execution_event",
    # 治理活跃 9（六治理仓储读写）
    "governance_tenant",
    "governance_tenant_quota",
    "governance_org_node",
    "governance_user",
    "governance_role",
    "governance_permission",
    "governance_role_binding",
    "governance_access_token",
    "governance_token_usage",
    # 孤儿 8（治理表，当前无仓储读写，直连仍是数据面）
    "pricing_rules",
    "cost_records",
    "budgets",
    "audit_log_entries",
    "mask_rules",
    "scheduler_policies",
    "resource_pools",
    "global_scheduler_queue",
    # DEFAULT 分区 3（防直连分区绕过父表策略）
    "governance_token_usage_default",
    "cost_records_default",
    "audit_log_entries_default",
]

_TARGETS_BLOCK_RE = re.compile(r"targets TEXT\[\] := ARRAY\[(.*?)\];", re.S)
_TARGET_NAME_RE = re.compile(r"'([a-z_0-9]+)'")


def _read(name: str) -> str:
    return (_MIGRATIONS_DIR / name).read_text(encoding="utf-8")


def _strip_sql_comments(sql: str) -> str:
    """剥离 SQL 注释（整行 -- 与行内 --）：B0 教训——头部「禁 ::regclass」纪律说明
    本身包含该字面量，断言必须只针对可执行代码行。"""
    return "\n".join(
        line.split("--", 1)[0] if "--" in line else line
        for line in sql.splitlines()
    )


def _parse_targets(sql: str):
    """解析 FOREACH targets 数组的全部目标表名（按出现顺序）。"""
    m = _TARGETS_BLOCK_RE.search(sql)
    assert m is not None, "targets 数组块必须存在"
    return _TARGET_NAME_RE.findall(m.group(1))


# =============================================================================
# A. v1_6_tenant_hardening.sql（版本 1.6，租户列收紧）
# =============================================================================


class TestV1_6TenantHardening:
    def test_version_registration(self):
        """版本登记 1.6 + ON CONFLICT 幂等（E10 范式）。"""
        sql = _read("v1_6_tenant_hardening.sql")
        assert "VALUES ('1.6'" in sql, "版本登记 1.6"
        assert "ON CONFLICT (version) DO NOTHING" in sql, "版本登记幂等（E10 范式）"

    def test_backfill_covers_exactly_8_nullable_tables(self):
        """backfill 恰 8 张可空表（NULL → 'tenant_default'）。"""
        sql = _read("v1_6_tenant_hardening.sql")
        assert (
            sql.count("SET tenant_id = 'tenant_default' WHERE tenant_id IS NULL") == 8
        ), "backfill UPDATE 恰 8 处"
        for table in _NULLABLE_8:
            assert f"to_regclass('public.{table}')" in sql, f"{table} 表守卫存在"

    def test_set_not_null_exactly_8(self):
        """SET NOT NULL 恰 8 处（精确 8 张=可空集合，其余 13 张已 NOT NULL 不动）。"""
        sql = _read("v1_6_tenant_hardening.sql")
        assert sql.count("ALTER COLUMN tenant_id SET NOT NULL") == 8, "SET NOT NULL 恰 8 处"
        assert sql.count("ALTER COLUMN tenant_id DROP NOT NULL") == 0, "正向迁移不含回滚语句"

    def test_idempotent_guards(self):
        """幂等守卫：to_regclass + information_schema 列守卫；禁 ::regclass（B0 纪律）。"""
        sql = _read("v1_6_tenant_hardening.sql")
        code = _strip_sql_comments(sql)
        assert code.count("information_schema.columns") == 8, "列存在守卫 8 处"
        assert sql.count("is_nullable = 'YES'") == 8, "仅可空列执行收紧"
        assert "::regclass" not in code, (
            "可执行行禁用 ::regclass（B0 演练教训：PL/pgSQL AND 非短路）"
        )

    def test_does_not_touch_defaults(self):
        """不改列默认值（核心 4 表保留 v1_2 的 DEFAULT 'tenant_default'）。"""
        sql = _read("v1_6_tenant_hardening.sql")
        assert "DROP DEFAULT" not in sql, "不删除默认值"
        assert "SET DEFAULT" not in sql, "不新增/改写默认值"


# =============================================================================
# B. v1_6_rollback.sql（回滚对称）
# =============================================================================


class TestV1_6Rollback:
    def test_rollback_symmetry(self):
        """回滚对称：8 表 DROP NOT NULL + 删版本登记。"""
        sql = _read("v1_6_rollback.sql")
        assert sql.count("ALTER COLUMN tenant_id DROP NOT NULL") == 8, "DROP NOT NULL 恰 8 处"
        assert "DELETE FROM schema_versions WHERE version = '1.6'" in sql, "删除版本记录"

    def test_rollback_guards_and_irreversibility_note(self):
        """回滚守卫（is_nullable='NO' 才执行）+ backfill 不可逆登记。"""
        sql = _read("v1_6_rollback.sql")
        assert sql.count("is_nullable = 'NO'") == 8, "仅已收紧列执行回退"
        assert "不可逆" in sql, "backfill 数据变更不可逆警告必须登记"
        assert "::regclass" not in _strip_sql_comments(sql), "可执行行禁用 ::regclass"


# =============================================================================
# C. v1_7_enable_rls.sql（版本 1.7，RLS 策略全量启用）
# =============================================================================


class TestV1_7EnableRls:
    def test_version_registration(self):
        """版本登记 1.7 + ON CONFLICT 幂等（E10 范式）。"""
        sql = _read("v1_7_enable_rls.sql")
        assert "VALUES ('1.7'" in sql, "版本登记 1.7"
        assert "ON CONFLICT (version) DO NOTHING" in sql, "版本登记幂等（E10 范式）"

    def test_targets_exactly_21_tables_plus_3_partitions(self):
        """策略覆盖 24 处 = 21 张含租户表 + 3 个 DEFAULT 分区（验收：覆盖 100%）。"""
        sql = _read("v1_7_enable_rls.sql")
        targets = _parse_targets(sql)
        assert targets == _EXPECTED_TARGETS, (
            "24 目标逐一核对（顺序保持）：21 表 + 3 分区，业务表策略覆盖 100%"
        )

    def test_policy_semantics(self):
        """策略语义：FOR ALL + USING/WITH CHECK 双谓词 + 双通道。"""
        sql = _read("v1_7_enable_rls.sql")
        assert "CREATE POLICY p_tenant_isolation ON public.%I FOR ALL" in sql, (
            "p_tenant_isolation 策略 FOR ALL"
        )
        assert "WITH CHECK" in sql, "写入侧 WITH CHECK 存在"
        # fail-closed：missing_ok 形式 current_setting(..., true)，未设变量返回 NULL → 无行
        assert (
            sql.count("current_setting(''app.current_tenant'', true)") == 2
        ), "租户通道 USING + WITH CHECK 各一（PL/pgSQL 内单引号双写）"
        assert (
            sql.count("current_setting(''app.bypass_rls'', true)") == 2
        ), "运维旁路通道 USING + WITH CHECK 各一"
        assert "tenant_id = current_setting(''app.current_tenant'', true)" in sql, (
            "租户等值谓词"
        )

    def test_enable_and_force_statements(self):
        """ENABLE + FORCE 三件套（FORCE 必需：应用角色=表 owner）。"""
        sql = _read("v1_7_enable_rls.sql")
        assert "ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY" in sql, "ENABLE 语句"
        assert "ALTER TABLE public.%I FORCE ROW LEVEL SECURITY" in sql, "FORCE 语句"

    def test_idempotent_guards(self):
        """幂等守卫：表守卫 to_regclass + 策略守卫 pg_policy；禁 ::regclass。"""
        sql = _read("v1_7_enable_rls.sql")
        assert "to_regclass('public.' || target) IS NULL" in sql, "表存在守卫（CONTINUE 跳过）"
        assert "FROM pg_policy" in sql, "策略存在守卫（幂等重跑）"
        assert "::regclass" not in _strip_sql_comments(sql), (
            "可执行行禁用 ::regclass（B0 演练教训）"
        )
        assert "CONTINUE" in sql, "缺表前态安全跳过"


# =============================================================================
# D. v1_7_rollback.sql（回滚对称）
# =============================================================================


class TestV1_7Rollback:
    def test_rollback_symmetry(self):
        """回滚对称：守卫 DROP POLICY + NO FORCE + DISABLE + 同 24 目标 + 删版本。"""
        sql = _read("v1_7_rollback.sql")
        assert _parse_targets(sql) == _EXPECTED_TARGETS, "回滚覆盖同一 24 目标集合"
        assert "DROP POLICY p_tenant_isolation ON public.%I" in sql, "守卫 DROP POLICY"
        assert "FROM pg_policy" in sql, "策略存在守卫（幂等重跑）"
        assert "ALTER TABLE public.%I NO FORCE ROW LEVEL SECURITY" in sql, "撤销 FORCE"
        assert "ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY" in sql, "撤销 ENABLE"
        assert "CREATE POLICY" not in sql.replace(
            "DROP POLICY p_tenant_isolation ON public.%I", ""
        ), "回滚不含创建语句"
        assert "DELETE FROM schema_versions WHERE version = '1.7'" in sql, "删除版本记录"
        assert "::regclass" not in _strip_sql_comments(sql), "可执行行禁用 ::regclass"


# =============================================================================
# E. schema_migrations/README.md 清单与 RLS 说明
# =============================================================================


class TestReadmeIntegration:
    def test_readme_lists_v1_6_and_v1_7_with_paired_rollback(self):
        """README 清单含 v1_6/v1_7 两行且配对回滚正确。"""
        readme = _read("README.md")
        assert "v1_6_tenant_hardening.sql" in readme, "清单含 v1_6"
        assert "v1_6_rollback.sql" in readme, "v1_6 配对回滚"
        assert "v1_7_enable_rls.sql" in readme, "清单含 v1_7"
        assert "v1_7_rollback.sql" in readme, "v1_7 配对回滚"

    def test_readme_rls_ops_guidance(self):
        """README 含 RLS 运维直连须知（set_config 事务级注入 + 回滚顺序）。"""
        readme = _read("README.md")
        assert "p_tenant_isolation" in readme, "策略名说明"
        assert "set_config('app.current_tenant'" in readme, "租户上下文注入示例"
        assert "set_config('app.bypass_rls'" in readme, "运维旁路说明"
        assert "v1_7 → v1_6" in readme, "回滚顺序说明"

    def test_readme_v1_4_rollback_dead_reference_fixed(self):
        """v1_4 行勘误：清单行死引用 v1_4_rollback.sql 改为「无回滚脚本」（历史勘误第 5 条）。"""
        readme = _read("README.md")
        v1_4_row = next(
            (line for line in readme.splitlines() if "v1_4_vector_kb_records.sql" in line),
            None,
        )
        assert v1_4_row is not None, "v1_4 清单行存在"
        assert "无回滚脚本" in v1_4_row, "v1_4 无回滚脚本为既定事实"
        assert "v1_4_rollback" not in v1_4_row, "清单行死引用必须清除"
