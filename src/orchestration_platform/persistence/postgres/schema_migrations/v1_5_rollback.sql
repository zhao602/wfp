-- =============================================================================
-- 治理表更名回滚脚本（v1_5_rollback.sql）
--
-- 职责：撤销 v1_5 的更名/索引更名/建表/约束/分区，回到 v1_5 之前的表名。
-- 注意（数据丢失警告）：
--   1. DROP 三张 DEFAULT 分区将丢失对应分区表的全部已写入数据
--      （Token 用量 / 成本记录 / 审计日志）；
--   2. DROP governance_permission 将丢失权限项数据；
--   3. 仅用于迁移演练与紧急回退，生产执行前必须先备份。
-- 设计意图：
--   1. 回滚幂等（守卫同 v1_5：新名存在 + 旧名不存在才逆操作），
--      空库/重复执行/已回滚库再执行均为 no-op；
--   2. 逆序执行：DEFAULT 分区 → 唯一约束 → 权限表 → 索引名 → 表名 → 版本记录。
-- =============================================================================

-- 删除 DEFAULT 分区（数据丢失警告见文件头）
DROP TABLE IF EXISTS governance_token_usage_default;
DROP TABLE IF EXISTS cost_records_default;
DROP TABLE IF EXISTS audit_log_entries_default;

-- 删除仓储 upsert 契约唯一约束（守卫：表存在；DROP CONSTRAINT IF EXISTS 幂等）
DO $$
BEGIN
    IF to_regclass('public.governance_user') IS NOT NULL THEN
        ALTER TABLE public.governance_user
            DROP CONSTRAINT IF EXISTS uq_governance_user_tenant_user;
    END IF;
    IF to_regclass('public.governance_org_node') IS NOT NULL THEN
        ALTER TABLE public.governance_org_node
            DROP CONSTRAINT IF EXISTS uq_governance_org_node_tenant_node;
    END IF;
    IF to_regclass('public.governance_role') IS NOT NULL THEN
        ALTER TABLE public.governance_role
            DROP CONSTRAINT IF EXISTS uq_governance_role_tenant_role;
    END IF;
    IF to_regclass('public.governance_role_binding') IS NOT NULL THEN
        ALTER TABLE public.governance_role_binding
            DROP CONSTRAINT IF EXISTS uq_governance_role_binding_tenant_binding;
    END IF;
END
$$;

-- 删除权限项表
DROP TABLE IF EXISTS governance_permission;

-- 索引名回退（守卫：新名存在 + 旧名不存在）
DO $$
BEGIN
    IF to_regclass('public.idx_governance_org_node_tenant') IS NOT NULL
       AND to_regclass('public.idx_org_nodes_tenant') IS NULL THEN
        ALTER INDEX public.idx_governance_org_node_tenant RENAME TO idx_org_nodes_tenant;
    END IF;
    IF to_regclass('public.idx_governance_org_node_parent') IS NOT NULL
       AND to_regclass('public.idx_org_nodes_parent') IS NULL THEN
        ALTER INDEX public.idx_governance_org_node_parent RENAME TO idx_org_nodes_parent;
    END IF;
    IF to_regclass('public.idx_governance_user_tenant') IS NOT NULL
       AND to_regclass('public.idx_users_tenant') IS NULL THEN
        ALTER INDEX public.idx_governance_user_tenant RENAME TO idx_users_tenant;
    END IF;
    IF to_regclass('public.idx_governance_role_binding_user') IS NOT NULL
       AND to_regclass('public.idx_role_bindings_user') IS NULL THEN
        ALTER INDEX public.idx_governance_role_binding_user RENAME TO idx_role_bindings_user;
    END IF;
    IF to_regclass('public.idx_governance_access_token_hash') IS NOT NULL
       AND to_regclass('public.idx_access_tokens_hash') IS NULL THEN
        ALTER INDEX public.idx_governance_access_token_hash RENAME TO idx_access_tokens_hash;
    END IF;
    IF to_regclass('public.idx_governance_token_usage_tenant_time') IS NOT NULL
       AND to_regclass('public.idx_usage_tenant_time') IS NULL THEN
        ALTER INDEX public.idx_governance_token_usage_tenant_time RENAME TO idx_usage_tenant_time;
    END IF;
END
$$;

-- 表名回退（守卫：新名存在 + 旧名不存在）
DO $$
BEGIN
    IF to_regclass('public.governance_tenant') IS NOT NULL
       AND to_regclass('public.tenants') IS NULL THEN
        ALTER TABLE public.governance_tenant RENAME TO tenants;
    END IF;
    IF to_regclass('public.governance_tenant_quota') IS NOT NULL
       AND to_regclass('public.tenant_quotas') IS NULL THEN
        ALTER TABLE public.governance_tenant_quota RENAME TO tenant_quotas;
    END IF;
    IF to_regclass('public.governance_org_node') IS NOT NULL
       AND to_regclass('public.org_nodes') IS NULL THEN
        ALTER TABLE public.governance_org_node RENAME TO org_nodes;
    END IF;
    IF to_regclass('public.governance_user') IS NOT NULL
       AND to_regclass('public.users') IS NULL THEN
        ALTER TABLE public.governance_user RENAME TO users;
    END IF;
    IF to_regclass('public.governance_role') IS NOT NULL
       AND to_regclass('public.roles') IS NULL THEN
        ALTER TABLE public.governance_role RENAME TO roles;
    END IF;
    IF to_regclass('public.governance_role_binding') IS NOT NULL
       AND to_regclass('public.role_bindings') IS NULL THEN
        ALTER TABLE public.governance_role_binding RENAME TO role_bindings;
    END IF;
    IF to_regclass('public.governance_access_token') IS NOT NULL
       AND to_regclass('public.access_tokens') IS NULL THEN
        ALTER TABLE public.governance_access_token RENAME TO access_tokens;
    END IF;
    IF to_regclass('public.governance_token_usage') IS NOT NULL
       AND to_regclass('public.token_usage_records') IS NULL THEN
        ALTER TABLE public.governance_token_usage RENAME TO token_usage_records;
    END IF;
END
$$;

-- 删除迁移版本记录
DELETE FROM schema_versions WHERE version = '1.5';
