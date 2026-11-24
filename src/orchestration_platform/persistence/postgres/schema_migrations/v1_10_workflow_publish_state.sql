-- =============================================================================
-- 工作流定义发布态迁移脚本（v1_10_workflow_publish_state.sql）
--
-- 职责：workflow_definition 增发布状态机三列与状态索引（roadmap-v3 长期批次
--       B11 定义版本与发布态，design D1/D2/D6）。
-- 设计意图：
--   1. 状态机载体：status（draft/published，NOT NULL DEFAULT 'draft'——
--      既有行回填 draft 语义正确：从未发布过）+ draft_of（本版本基于的
--      已发布版本号，发布回滚时承载 to_version 溯源）+ published_at；
--   2. 发布态不可变守卫在仓储 save 写边界强制（PG 单语句
--      ON CONFLICT DO UPDATE ... WHERE status <> 'published' + RETURNING，
--      语句级原子无竞态窗口——见 postgres_workflow_repository.save）；
--   3. 幂等守卫（README 迁移原则 4）：ADD COLUMN IF NOT EXISTS +
--      CREATE INDEX IF NOT EXISTS + schema_versions ON CONFLICT DO NOTHING，
--      任意前态重复执行零副作用；
--   4. 不碰租户列（tenant_id 由 v1_2 租户化加列、v1_6 收紧、v1_7 RLS，
--      本迁移零租户面改动）；不启用新 RLS（workflow_definition 既有
--      策略三件套覆盖，v1_7）。
-- 前置依赖：应用本迁移后新 INSERT 才写 status 列（迁移先行，沿 v1_8/v1_9 纪律）。
-- 顺序：v1_9 之后执行；回滚：v1_10_rollback.sql。
-- =============================================================================

-- 迁移版本记录
INSERT INTO schema_versions (version, description) VALUES ('1.10', '工作流定义发布态：status/draft_of/published_at 列与状态索引（B11 定义版本与发布态，发布态不可变守卫）')
ON CONFLICT (version) DO NOTHING;

-- 发布状态机三列（既有行回填 DEFAULT 'draft'）
ALTER TABLE workflow_definition
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'draft';
ALTER TABLE workflow_definition
    ADD COLUMN IF NOT EXISTS draft_of INTEGER;
ALTER TABLE workflow_definition
    ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;

-- 状态查询索引：get_published（definition_id + status 取最新已发布版本）
CREATE INDEX IF NOT EXISTS idx_workflow_definition_status
    ON workflow_definition (definition_id, status);
