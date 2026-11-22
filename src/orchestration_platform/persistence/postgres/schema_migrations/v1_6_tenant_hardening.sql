-- =============================================================================
-- 多租户强隔离租户列收紧迁移脚本（v1_6_tenant_hardening.sql）
--
-- 职责：为 8 张 tenant_id 可空表执行 backfill（NULL → 'tenant_default'）并
--       ALTER COLUMN SET NOT NULL（roadmap B1 第一段：先补 tenant_id 收紧，
--       影子校验通过后再执行 v1_7 启用 RLS——风险登记分级启用要求）。
-- 设计意图：
--   1. 8 张可空表 = 核心 4（workflow_definition / execution_instance /
--      checkpoint / execution_event，v1_2:279/282/287/289 补列时未加 NOT NULL）
--      + 4 张孤儿（pricing_rules / mask_rules / scheduler_policies /
--      resource_pools，v1_2:142/219/236/251 同样可空）；
--   2. backfill 目标值 'tenant_default' 与 v1_2 列默认值、种子租户一致
--      （backfill 不可逆，回滚脚本头部登记该数据事实）；
--   3. 保留列 DEFAULT 'tenant_default'（核心 4 表，兼容兜底），不改默认值；
--   4. 幂等（B0 纪律，E10）：表守卫 to_regclass（禁 '::regclass'——缺表返回
--      NULL 而非抛错，且 PL/pgSQL 对 AND 不保证短路求值）+ 列守卫
--      information_schema.columns（is_nullable='YES' 才执行——已收紧的表
--      重复执行为零副作用；缺列前态如仅 table_definitions 同样安全跳过）；
--   5. 其余 13 张含租户表建表时即 NOT NULL（含 v1_5 新建 governance_permission），
--      不在本迁移范围（精确 8 张 = 可空集合）。
-- 顺序：v1_5 之后、v1_7 之前；回滚：v1_6_rollback.sql（仅撤约束，数据不可逆）。
-- =============================================================================

-- 迁移版本记录
INSERT INTO schema_versions (version, description) VALUES ('1.6', '多租户强隔离：租户列收紧（backfill + NOT NULL）')
ON CONFLICT (version) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 8 张可空表：backfill + SET NOT NULL（守卫：表存在 + tenant_id 列存在且可空）
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.workflow_definition') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'workflow_definition'
             AND column_name = 'tenant_id'
             AND is_nullable = 'YES'
       ) THEN
        UPDATE public.workflow_definition SET tenant_id = 'tenant_default' WHERE tenant_id IS NULL;
        ALTER TABLE public.workflow_definition ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
    IF to_regclass('public.execution_instance') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'execution_instance'
             AND column_name = 'tenant_id'
             AND is_nullable = 'YES'
       ) THEN
        UPDATE public.execution_instance SET tenant_id = 'tenant_default' WHERE tenant_id IS NULL;
        ALTER TABLE public.execution_instance ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
    IF to_regclass('public.checkpoint') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'checkpoint'
             AND column_name = 'tenant_id'
             AND is_nullable = 'YES'
       ) THEN
        UPDATE public.checkpoint SET tenant_id = 'tenant_default' WHERE tenant_id IS NULL;
        ALTER TABLE public.checkpoint ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
    IF to_regclass('public.execution_event') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'execution_event'
             AND column_name = 'tenant_id'
             AND is_nullable = 'YES'
       ) THEN
        UPDATE public.execution_event SET tenant_id = 'tenant_default' WHERE tenant_id IS NULL;
        ALTER TABLE public.execution_event ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
    IF to_regclass('public.pricing_rules') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'pricing_rules'
             AND column_name = 'tenant_id'
             AND is_nullable = 'YES'
       ) THEN
        UPDATE public.pricing_rules SET tenant_id = 'tenant_default' WHERE tenant_id IS NULL;
        ALTER TABLE public.pricing_rules ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
    IF to_regclass('public.mask_rules') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'mask_rules'
             AND column_name = 'tenant_id'
             AND is_nullable = 'YES'
       ) THEN
        UPDATE public.mask_rules SET tenant_id = 'tenant_default' WHERE tenant_id IS NULL;
        ALTER TABLE public.mask_rules ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
    IF to_regclass('public.scheduler_policies') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'scheduler_policies'
             AND column_name = 'tenant_id'
             AND is_nullable = 'YES'
       ) THEN
        UPDATE public.scheduler_policies SET tenant_id = 'tenant_default' WHERE tenant_id IS NULL;
        ALTER TABLE public.scheduler_policies ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
    IF to_regclass('public.resource_pools') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'resource_pools'
             AND column_name = 'tenant_id'
             AND is_nullable = 'YES'
       ) THEN
        UPDATE public.resource_pools SET tenant_id = 'tenant_default' WHERE tenant_id IS NULL;
        ALTER TABLE public.resource_pools ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
END
$$;
