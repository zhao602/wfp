-- =============================================================================
-- 第二阶段回滚脚本（v1_1_rollback.sql）
--
-- 职责：从第二阶段 schema 回退至第一阶段 schema。
-- 注意：回滚将丢失第二阶段增强字段数据，仅用于紧急回退。
--
-- 历史勘误（2026-11-21，roadmap B0/D1，就地修正）：
--   既有表扩展列回退语句原误用复数表名（checkpoints/events），
--   实际表定义为单数（checkpoint/execution_event，见 table_definitions.sql）。
-- =============================================================================

-- 删除第二阶段新增表
DROP TABLE IF EXISTS data_source_connections;
DROP TABLE IF EXISTS mcp_tool_registrations;
DROP TABLE IF EXISTS context_checkpoints;

-- 删除既有表的第二阶段扩展列
ALTER TABLE checkpoint DROP COLUMN IF EXISTS compression_meta;
ALTER TABLE checkpoint DROP COLUMN IF EXISTS branch_id;

ALTER TABLE execution_event DROP COLUMN IF EXISTS subworkflow_id;
ALTER TABLE execution_event DROP COLUMN IF EXISTS nesting_depth;
ALTER TABLE execution_event DROP COLUMN IF EXISTS injection_seq;
ALTER TABLE execution_event DROP COLUMN IF EXISTS strategy_type;
ALTER TABLE execution_event DROP COLUMN IF EXISTS tool_id;

-- 注意：spec_version 列保留（含 '1.0' 默认值），不影响第一阶段

-- 删除迁移版本记录
DELETE FROM schema_versions WHERE version = '1.1';
