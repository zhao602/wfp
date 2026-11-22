-- =============================================================================
-- 多租户强隔离租户列收紧回滚脚本（v1_6_rollback.sql）
--
-- 职责：撤销 v1_6 的 SET NOT NULL 约束与版本记录，回到 v1_6 之前状态。
-- 注意（数据不可逆警告）：
--   1. v1_6 的 backfill（tenant_id NULL → 'tenant_default'）是不可逆的数据变更，
--      本脚本仅撤销 NOT NULL 约束，不恢复历史 NULL 值——该数据变更登记为既成事实；
--   2. 列 DEFAULT 'tenant_default'（核心 4 表）为 v1_2 原有，v1_6 未改动，
--      本脚本同样不改。
-- 设计意图：
--   1. 幂等（B0 纪律）：表守卫 to_regclass + 列守卫 information_schema.columns
--      （is_nullable='NO' 才执行——已回滚的表重复执行为零副作用）；
--   2. 逆序执行：先撤约束，后删版本记录。
-- 顺序：必须在 v1_7_rollback.sql 之后执行（回滚顺序 v1_7 → v1_6）。
-- =============================================================================

-- 撤销 8 张表的 tenant_id NOT NULL 约束（守卫：表存在 + 列存在且当前 NOT NULL）
DO $$
BEGIN
    IF to_regclass('public.workflow_definition') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'workflow_definition'
             AND column_name = 'tenant_id'
             AND is_nullable = 'NO'
       ) THEN
        ALTER TABLE public.workflow_definition ALTER COLUMN tenant_id DROP NOT NULL;
    END IF;
    IF to_regclass('public.execution_instance') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'execution_instance'
             AND column_name = 'tenant_id'
             AND is_nullable = 'NO'
       ) THEN
        ALTER TABLE public.execution_instance ALTER COLUMN tenant_id DROP NOT NULL;
    END IF;
    IF to_regclass('public.checkpoint') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'checkpoint'
             AND column_name = 'tenant_id'
             AND is_nullable = 'NO'
       ) THEN
        ALTER TABLE public.checkpoint ALTER COLUMN tenant_id DROP NOT NULL;
    END IF;
    IF to_regclass('public.execution_event') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'execution_event'
             AND column_name = 'tenant_id'
             AND is_nullable = 'NO'
       ) THEN
        ALTER TABLE public.execution_event ALTER COLUMN tenant_id DROP NOT NULL;
    END IF;
    IF to_regclass('public.pricing_rules') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'pricing_rules'
             AND column_name = 'tenant_id'
             AND is_nullable = 'NO'
       ) THEN
        ALTER TABLE public.pricing_rules ALTER COLUMN tenant_id DROP NOT NULL;
    END IF;
    IF to_regclass('public.mask_rules') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'mask_rules'
             AND column_name = 'tenant_id'
             AND is_nullable = 'NO'
       ) THEN
        ALTER TABLE public.mask_rules ALTER COLUMN tenant_id DROP NOT NULL;
    END IF;
    IF to_regclass('public.scheduler_policies') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'scheduler_policies'
             AND column_name = 'tenant_id'
             AND is_nullable = 'NO'
       ) THEN
        ALTER TABLE public.scheduler_policies ALTER COLUMN tenant_id DROP NOT NULL;
    END IF;
    IF to_regclass('public.resource_pools') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'resource_pools'
             AND column_name = 'tenant_id'
             AND is_nullable = 'NO'
       ) THEN
        ALTER TABLE public.resource_pools ALTER COLUMN tenant_id DROP NOT NULL;
    END IF;
END
$$;

-- 删除迁移版本记录
DELETE FROM schema_versions WHERE version = '1.6';
