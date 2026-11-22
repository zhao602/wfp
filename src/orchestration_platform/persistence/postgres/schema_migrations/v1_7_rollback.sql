-- =============================================================================
-- 多租户强隔离 RLS 启用回滚脚本（v1_7_rollback.sql）
--
-- 职责：撤销 v1_7 的全部策略与 RLS 开关（DROP POLICY p_tenant_isolation +
--       NO FORCE + DISABLE，24 处），回到 v1_7 之前状态。
-- 注意：回滚后数据面仅剩仓储代码层 tenant_id 过滤（第一道保险），
--       数据库层不再强制隔离——仅用于迁移演练与紧急回退。
-- 设计意图：
--   1. 幂等（B0 纪律，E10）：表守卫 to_regclass（禁 '::regclass'）+ 策略守卫
--      pg_policy（策略不存在跳过 DROP）+ NO FORCE/DISABLE 幂等重执行；
--   2. 逆序执行：先撤策略与开关，后删版本记录；
--   3. 回滚顺序 v1_7 → v1_6（先本脚本，后 v1_6_rollback.sql）。
-- =============================================================================

DO $$
DECLARE
    target TEXT;
    targets TEXT[] := ARRAY[
        -- 核心 4（业务仓储读写，v1_2 补列）
        'workflow_definition',
        'execution_instance',
        'checkpoint',
        'execution_event',
        -- 治理活跃 9（六治理仓储读写）
        'governance_tenant',
        'governance_tenant_quota',
        'governance_org_node',
        'governance_user',
        'governance_role',
        'governance_permission',
        'governance_role_binding',
        'governance_access_token',
        'governance_token_usage',
        -- 孤儿 8（治理表，当前无仓储读写，直连仍是数据面）
        'pricing_rules',
        'cost_records',
        'budgets',
        'audit_log_entries',
        'mask_rules',
        'scheduler_policies',
        'resource_pools',
        'global_scheduler_queue',
        -- DEFAULT 分区 3（防直连分区绕过父表策略）
        'governance_token_usage_default',
        'cost_records_default',
        'audit_log_entries_default'
    ];
BEGIN
    FOREACH target IN ARRAY targets LOOP
        -- 表存在守卫（to_regclass，禁 ::regclass——缺表返回 NULL 而非抛错）
        IF to_regclass('public.' || target) IS NULL THEN
            CONTINUE;
        END IF;

        -- 策略存在守卫（幂等重跑零副作用）
        IF EXISTS (
            SELECT 1 FROM pg_policy
            WHERE polname = 'p_tenant_isolation'
              AND polrelid = to_regclass('public.' || target)
        ) THEN
            EXECUTE format('DROP POLICY p_tenant_isolation ON public.%I', target);
        END IF;

        EXECUTE format('ALTER TABLE public.%I NO FORCE ROW LEVEL SECURITY', target);
        EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', target);
    END LOOP;
END
$$;

-- 删除迁移版本记录
DELETE FROM schema_versions WHERE version = '1.7';
