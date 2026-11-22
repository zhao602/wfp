-- =============================================================================
-- 治理表更名迁移脚本（v1_5_rename_governance_tables.sql）
--
-- 职责：将 v1_2 创建的治理表更名/补齐为 governance_* 仓储契约（roadmap B0/D1）。
-- 设计意图：
--   1. 六大 PostgreSQL 仓储（tenant/user/token/role/token_usage/org_tree）静态 SQL
--      统一引用 governance_* 表名；v1_2 建表名（tenants/users/roles 等）从未被
--      仓储引用，属「迁移建表 ↔ 仓储读写」断链，本迁移消除断链；
--   2. 迁移幂等（沿用 v1_3/v1_4 范式，E10）：版本登记 ON CONFLICT DO NOTHING，
--      更名/索引/建表/约束/分区全部带存在性守卫，任意前态重复执行零副作用；
--   3. DO 块守卫（to_regclass）：旧名存在且新名不存在才更名——
--      空库执行、重复执行、已更名库再执行均为 no-op；
--   4. 四组 (tenant_id, 业务主键) 唯一约束对齐仓储 ON CONFLICT 幂等 upsert 契约
--      （user: (tenant_id, user_id) / org_node: (tenant_id, org_node_id) /
--       role: (tenant_id, role_id) / role_binding: (tenant_id, binding_id)）；
--   5. governance_permission 为新建表（v1_2 未建），主键 (tenant_id, permission_id)
--      对齐 postgres_role_repository.save_permission 的 upsert 契约；
--   6. 三张 LIST 分区表补建 DEFAULT 分区：v1_2 仅建分区父表未建任何分区，
--      无 DEFAULT 分区时所有 INSERT 报 no partition of relation found。
-- =============================================================================

-- 迁移版本记录
INSERT INTO schema_versions (version, description) VALUES ('1.5', '治理表更名与仓储契约对齐（governance_*）')
ON CONFLICT (version) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 1. 表更名（守卫：旧名存在 + 新名不存在）
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.tenants') IS NOT NULL
       AND to_regclass('public.governance_tenant') IS NULL THEN
        ALTER TABLE public.tenants RENAME TO governance_tenant;
    END IF;
    IF to_regclass('public.tenant_quotas') IS NOT NULL
       AND to_regclass('public.governance_tenant_quota') IS NULL THEN
        ALTER TABLE public.tenant_quotas RENAME TO governance_tenant_quota;
    END IF;
    IF to_regclass('public.org_nodes') IS NOT NULL
       AND to_regclass('public.governance_org_node') IS NULL THEN
        ALTER TABLE public.org_nodes RENAME TO governance_org_node;
    END IF;
    IF to_regclass('public.users') IS NOT NULL
       AND to_regclass('public.governance_user') IS NULL THEN
        ALTER TABLE public.users RENAME TO governance_user;
    END IF;
    IF to_regclass('public.roles') IS NOT NULL
       AND to_regclass('public.governance_role') IS NULL THEN
        ALTER TABLE public.roles RENAME TO governance_role;
    END IF;
    IF to_regclass('public.role_bindings') IS NOT NULL
       AND to_regclass('public.governance_role_binding') IS NULL THEN
        ALTER TABLE public.role_bindings RENAME TO governance_role_binding;
    END IF;
    IF to_regclass('public.access_tokens') IS NOT NULL
       AND to_regclass('public.governance_access_token') IS NULL THEN
        ALTER TABLE public.access_tokens RENAME TO governance_access_token;
    END IF;
    IF to_regclass('public.token_usage_records') IS NOT NULL
       AND to_regclass('public.governance_token_usage') IS NULL THEN
        ALTER TABLE public.token_usage_records RENAME TO governance_token_usage;
    END IF;
END
$$;

-- -----------------------------------------------------------------------------
-- 2. 索引更名（守卫：旧名存在 + 新名不存在；名称对齐更名后的表）
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.idx_org_nodes_tenant') IS NOT NULL
       AND to_regclass('public.idx_governance_org_node_tenant') IS NULL THEN
        ALTER INDEX public.idx_org_nodes_tenant RENAME TO idx_governance_org_node_tenant;
    END IF;
    IF to_regclass('public.idx_org_nodes_parent') IS NOT NULL
       AND to_regclass('public.idx_governance_org_node_parent') IS NULL THEN
        ALTER INDEX public.idx_org_nodes_parent RENAME TO idx_governance_org_node_parent;
    END IF;
    IF to_regclass('public.idx_users_tenant') IS NOT NULL
       AND to_regclass('public.idx_governance_user_tenant') IS NULL THEN
        ALTER INDEX public.idx_users_tenant RENAME TO idx_governance_user_tenant;
    END IF;
    IF to_regclass('public.idx_role_bindings_user') IS NOT NULL
       AND to_regclass('public.idx_governance_role_binding_user') IS NULL THEN
        ALTER INDEX public.idx_role_bindings_user RENAME TO idx_governance_role_binding_user;
    END IF;
    IF to_regclass('public.idx_access_tokens_hash') IS NOT NULL
       AND to_regclass('public.idx_governance_access_token_hash') IS NULL THEN
        ALTER INDEX public.idx_access_tokens_hash RENAME TO idx_governance_access_token_hash;
    END IF;
    IF to_regclass('public.idx_usage_tenant_time') IS NOT NULL
       AND to_regclass('public.idx_governance_token_usage_tenant_time') IS NULL THEN
        ALTER INDEX public.idx_usage_tenant_time RENAME TO idx_governance_token_usage_tenant_time;
    END IF;
END
$$;

-- -----------------------------------------------------------------------------
-- 3. 新建权限项表（v1_2 未建；对齐 save_permission 的 (tenant_id, permission_id) upsert）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS governance_permission (
    permission_id VARCHAR(64)  NOT NULL,
    tenant_id     VARCHAR(64)  NOT NULL,
    resource_type VARCHAR(64)  NOT NULL,
    operation     VARCHAR(32)  NOT NULL,
    scope         VARCHAR(32)  NOT NULL DEFAULT 'tenant',
    description   TEXT,
    PRIMARY KEY (tenant_id, permission_id)
);

-- -----------------------------------------------------------------------------
-- 4. 仓储 upsert 契约唯一约束（守卫：表存在 + 约束不存在）
--    注意：约束存在性守卫必须用 to_regclass（缺表前态返回 NULL），
--    禁用 '...'::regclass——缺表时该转换抛错，且 PL/pgSQL 对 AND 不保证
--    短路求值，外层表存在守卫拦不住（演练暴露，B0 就地修正）。
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.governance_user') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_constraint
           WHERE conname = 'uq_governance_user_tenant_user'
              AND conrelid = to_regclass('public.governance_user')
       ) THEN
        ALTER TABLE public.governance_user
            ADD CONSTRAINT uq_governance_user_tenant_user UNIQUE (tenant_id, user_id);
    END IF;
    IF to_regclass('public.governance_org_node') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_constraint
           WHERE conname = 'uq_governance_org_node_tenant_node'
              AND conrelid = to_regclass('public.governance_org_node')
       ) THEN
        ALTER TABLE public.governance_org_node
            ADD CONSTRAINT uq_governance_org_node_tenant_node UNIQUE (tenant_id, org_node_id);
    END IF;
    IF to_regclass('public.governance_role') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_constraint
           WHERE conname = 'uq_governance_role_tenant_role'
              AND conrelid = to_regclass('public.governance_role')
       ) THEN
        ALTER TABLE public.governance_role
            ADD CONSTRAINT uq_governance_role_tenant_role UNIQUE (tenant_id, role_id);
    END IF;
    IF to_regclass('public.governance_role_binding') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_constraint
           WHERE conname = 'uq_governance_role_binding_tenant_binding'
              AND conrelid = to_regclass('public.governance_role_binding')
       ) THEN
        ALTER TABLE public.governance_role_binding
            ADD CONSTRAINT uq_governance_role_binding_tenant_binding UNIQUE (tenant_id, binding_id);
    END IF;
END
$$;

-- -----------------------------------------------------------------------------
-- 5. 分区表补建 DEFAULT 分区（守卫：父表存在 + 分区不存在）
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.governance_token_usage') IS NOT NULL
       AND to_regclass('public.governance_token_usage_default') IS NULL THEN
        CREATE TABLE public.governance_token_usage_default
            PARTITION OF public.governance_token_usage DEFAULT;
    END IF;
    IF to_regclass('public.cost_records') IS NOT NULL
       AND to_regclass('public.cost_records_default') IS NULL THEN
        CREATE TABLE public.cost_records_default
            PARTITION OF public.cost_records DEFAULT;
    END IF;
    IF to_regclass('public.audit_log_entries') IS NOT NULL
       AND to_regclass('public.audit_log_entries_default') IS NULL THEN
        CREATE TABLE public.audit_log_entries_default
            PARTITION OF public.audit_log_entries DEFAULT;
    END IF;
END
$$;
