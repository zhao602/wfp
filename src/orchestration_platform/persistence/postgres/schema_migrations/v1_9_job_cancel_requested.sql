-- =============================================================================
-- 作业取消标志迁移脚本（v1_9_job_cancel_requested.sql）
--
-- 职责：execution_job 增取消标志列与实例活跃作业索引（roadmap-v3 中期窗口
--       M3，P5 实例级取消与超时，design D1）。
-- 设计意图：
--   1. 取消标志载体 = execution_job（非 execution_instance）：全仓无实例
--      持久化层，队列即跨进程协调基底；worker 心跳（默认 10s）已在轮询
--      该行，取消时延上界 = 心跳间隔（deploy.md 口径）；
--   2. 跨进程取消传播：API cancel 端点置位 → worker 心跳读标志 →
--      取消 runner task → run_and_record 既有 CancelledError 分支落
--      cancelled 终态（cancelled 终态不重投，M1 语义延续）；
--   3. 幂等守卫（README 迁移原则 4）：ADD COLUMN IF NOT EXISTS +
--      CREATE INDEX IF NOT EXISTS + schema_versions ON CONFLICT DO NOTHING，
--      任意前态重复执行零副作用；
--   4. 不启用 RLS：沿用 v1_8 口径（worker 系统级认领资源，操作走
--      app.bypass_rls='on' 通道）。
-- 顺序：v1_8 之后执行；回滚：v1_9_rollback.sql。
-- =============================================================================

-- 迁移版本记录
INSERT INTO schema_versions (version, description) VALUES ('1.9', '执行作业取消标志：execution_job.cancel_requested + 实例活跃作业索引（M3 实例级取消，跨进程传播载体）')
ON CONFLICT (version) DO NOTHING;

-- 取消标志列：API 置位 / worker 心跳轮询（design D1/D2）
ALTER TABLE execution_job
    ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE;

-- 实例活跃作业索引：cancel 端点按 instance_id 定位 pending/running 作业
CREATE INDEX IF NOT EXISTS idx_execution_job_instance_active
    ON execution_job (instance_id, created_at)
    WHERE status IN ('pending', 'running');
