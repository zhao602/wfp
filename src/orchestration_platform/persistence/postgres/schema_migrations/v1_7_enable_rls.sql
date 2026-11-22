-- =============================================================================
-- 多租户强隔离 RLS 启用迁移脚本（v1_7_enable_rls.sql）
--
-- 职责：为全部 21 张含租户表 + 3 个 DEFAULT 分区（共 24 处）启用行级安全策略
--       p_tenant_isolation（roadmap B1 验收：业务表策略覆盖 100%）。
-- 设计意图：
--   1. 双通道策略（FOR ALL，USING 与 WITH CHECK 同谓词）：
--      a) 运维通道 current_setting('app.bypass_rls', true) = 'on'——应用信任级
--         开关（app 角色可 SET 任意 GUC），供运维/演练及预租户上下文认证入口
--         （token get_by_hash 等）跨租户管理使用，安全边界由应用层控制；
--      b) 租户通道 tenant_id = current_setting('app.current_tenant', true)——
--         仓储基类事务级 set_config 注入（代码层过滤第一道保险，RLS 第二道）；
--   2. fail-closed：未设 app.current_tenant 时 current_setting(..., true) 返回
--      NULL，等值比较不成立 → 无行可见/可写（验收「无租户上下文查询返回 0 行」）；
--   3. FORCE ROW LEVEL SECURITY 必需：应用角色 = 表 owner，不加 FORCE 则 owner
--      绕过 RLS；
--   4. 分区子表（governance_token_usage_default / cost_records_default /
--      audit_log_entries_default）单独启用：防直连分区绕过父表策略
--      （直接查询分区仅适用分区自身策略）；
--   5. schema_versions 不入策略范围（无 tenant_id，迁移登记永远可用）；
--   6. 幂等（B0 纪律，E10）：表守卫 to_regclass（禁 '::regclass'——缺表返回
--      NULL 而非抛错，且 PL/pgSQL 对 AND 不保证短路求值）+ 策略守卫
--      pg_policy（重复执行零副作用）+ ENABLE/FORCE 幂等重执行。
-- 顺序：先 v1_6（租户列收紧，影子校验通过），后本脚本；回滚：v1_7_rollback.sql。
-- =============================================================================

-- 迁移版本记录
INSERT INTO schema_versions (version, description) VALUES ('1.7', '多租户强隔离：RLS 策略全量启用（21 表 + 3 分区）')
ON CONFLICT (version) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 24 处三件套：CREATE POLICY p_tenant_isolation + ENABLE + FORCE
-- -----------------------------------------------------------------------------
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
        IF NOT EXISTS (
            SELECT 1 FROM pg_policy
            WHERE polname = 'p_tenant_isolation'
              AND polrelid = to_regclass('public.' || target)
        ) THEN
            EXECUTE format(
                'CREATE POLICY p_tenant_isolation ON public.%I FOR ALL ' ||
                'USING (current_setting(''app.bypass_rls'', true) = ''on'' ' ||
                '       OR tenant_id = current_setting(''app.current_tenant'', true)) ' ||
                'WITH CHECK (current_setting(''app.bypass_rls'', true) = ''on'' ' ||
                '       OR tenant_id = current_setting(''app.current_tenant'', true))',
                target
            );
        END IF;

        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', target);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', target);
    END LOOP;
END
$$;
