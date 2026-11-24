-- =============================================================================
-- v1_10 回滚脚本（v1_10_rollback.sql）
--
-- 职责：撤销工作流定义发布态三列与状态索引（B11）。
-- 数据警告：
--   1. DROP COLUMN 丢失 status/draft_of/published_at 全部数据——
--      已发布版本将退化为无发布态（回退到 v1_10 前口径），生产执行前
--      必须先备份（沿 v1_5 回滚警告惯例）；
--   2. 仅撤本迁移对象（列 + 索引）；schema_versions 版本登记保留
--      （历史事实不可篡改，与既有回滚脚本口径一致——回滚以对象为单位，
--      版本记录留痕）。
-- 幂等守卫：DROP ... IF EXISTS，任意前态重复执行零副作用。
-- 顺序：v1_10_rollback.sql 在 v1_10 之后执行（紧急回退）。
-- =============================================================================

DROP INDEX IF EXISTS idx_workflow_definition_status;
ALTER TABLE workflow_definition DROP COLUMN IF EXISTS status;
ALTER TABLE workflow_definition DROP COLUMN IF EXISTS draft_of;
ALTER TABLE workflow_definition DROP COLUMN IF EXISTS published_at;
