-- =============================================================================
-- 作业取消标志回滚脚本（v1_9_rollback.sql）
--
-- 职责：回退 v1_9_job_cancel_requested.sql——撤销版本登记、删除实例活跃
--       作业索引与取消标志列。
-- 幂等：DROP/DELETE 均带守卫，重复执行零副作用。
-- 警告：DROP COLUMN 丢弃在途取消标志——回滚瞬间已置位未消费的取消请求
--       将丢失（worker 不再感知取消，作业跑完为止）；仅用于迁移演练与
--       紧急回退（README 口径）。
-- =============================================================================

-- 撤销版本登记
DELETE FROM schema_versions WHERE version = '1.9';

-- 删除实例活跃作业索引
DROP INDEX IF EXISTS idx_execution_job_instance_active;

-- 删除取消标志列
ALTER TABLE execution_job DROP COLUMN IF EXISTS cancel_requested;
