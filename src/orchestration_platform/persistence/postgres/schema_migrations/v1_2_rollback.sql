-- =============================================================================
-- 第三阶段回滚脚本（v1_2_rollback.sql）
--
-- 职责：从第三阶段 schema 回退至第二阶段 schema。
-- 注意：回滚将丢失全部治理数据与可选列数据，仅用于紧急回退。
--
-- 历史勘误（2026-11-21，roadmap B0/D1，就地修正）：
--   既有表扩展列回退语句原误用复数表名（workflow_definitions/execution_instances/
--   checkpoints/events），实际表定义为单数（见 table_definitions.sql）。
-- =============================================================================

-- 删除触发器与函数
DROP TRIGGER IF EXISTS trg_prevent_audit_update ON audit_log_entries;
DROP TRIGGER IF EXISTS trg_prevent_audit_delete ON audit_log_entries;
DROP FUNCTION IF EXISTS prevent_audit_modify();

-- 删除第三阶段新增表
DROP TABLE IF EXISTS global_scheduler_queue;
DROP TABLE IF EXISTS resource_pools;
DROP TABLE IF EXISTS scheduler_policies;
DROP TABLE IF EXISTS mask_rules;
DROP TABLE IF EXISTS audit_log_entries;
DROP TABLE IF EXISTS budgets;
DROP TABLE IF EXISTS cost_records;
DROP TABLE IF EXISTS pricing_rules;
DROP TABLE IF EXISTS token_usage_records;
DROP TABLE IF EXISTS access_tokens;
DROP TABLE IF EXISTS role_bindings;
DROP TABLE IF EXISTS roles;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS org_nodes;
DROP TABLE IF EXISTS tenant_quotas;
DROP TABLE IF EXISTS tenants;

-- 删除既有表的第三阶段扩展列
ALTER TABLE workflow_definition DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE workflow_definition DROP COLUMN IF EXISTS created_by;

ALTER TABLE execution_instance DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE execution_instance DROP COLUMN IF EXISTS created_by;
ALTER TABLE execution_instance DROP COLUMN IF EXISTS priority;
ALTER TABLE execution_instance DROP COLUMN IF EXISTS preemption_count;

ALTER TABLE checkpoint DROP COLUMN IF EXISTS tenant_id;

ALTER TABLE execution_event DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE execution_event DROP COLUMN IF EXISTS budget_id;
ALTER TABLE execution_event DROP COLUMN IF EXISTS resource_pool_id;
ALTER TABLE execution_event DROP COLUMN IF EXISTS preempted_instance_id;

-- 删除迁移版本记录
DELETE FROM schema_versions WHERE version = '1.2';
