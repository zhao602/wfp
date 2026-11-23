-- =============================================================================
-- 执行队列表迁移脚本（v1_8_execution_queue.sql）
--
-- 职责：创建执行队列作业表 execution_job（roadmap-v3 中期窗口 M1，
--       F1 执行队列与提交路径统一，plan D2）。
-- 设计意图：
--   1. 两副本不重复执行（plan D3）：认领原子性由应用层
--      UPDATE ... FOR UPDATE SKIP LOCKED 单赢（postgres_execution_queue.py）；
--   2. 提交幂等：idempotency_key 部分唯一索引（先例 execution_instance
--      幂等键 table_definitions.sql:46-48）——同键重复提交返回既有作业；
--   3. 租约列：lease_owner / lease_expires_at / attempts——认领即持租约
--      （默认 30s），心跳续期，过期可接管（at-least-once 诚实口径）；
--   4. 不启用 RLS：execution_job 为 worker 系统级认领资源（跨租户扫描），
--      操作走 app.bypass_rls='on' 通道；tenant_id 列仅供作业归属统计
--      （基础设施表口径，对齐 v1_7 的 schema_versions 例外）；
--   5. 幂等守卫（README 迁移原则 4）：CREATE TABLE IF NOT EXISTS +
--      CREATE [UNIQUE] INDEX IF NOT EXISTS + schema_versions
--      ON CONFLICT DO NOTHING，任意前态重复执行零副作用。
-- 顺序：v1_7 之后执行；回滚：v1_8_execution_queue_rollback.sql。
-- =============================================================================

-- 迁移版本记录
INSERT INTO schema_versions (version, description) VALUES ('1.8', '执行队列表：execution_job（F1 执行队列与提交路径统一，租约 + 幂等键 + 认领索引）')
ON CONFLICT (version) DO NOTHING;

-- 执行队列表：worker 认领执行的数据载体
CREATE TABLE IF NOT EXISTS execution_job (
    job_id               VARCHAR(255)   PRIMARY KEY,
    instance_id          VARCHAR(255)   NOT NULL,
    tenant_id            VARCHAR(255)   NOT NULL DEFAULT 'tenant_default',
    definition_id        VARCHAR(255)   NOT NULL,
    definition_version   INTEGER,
    yaml_text            TEXT,
    initial_inputs       JSONB          NOT NULL DEFAULT '{}',
    trigger_source       VARCHAR(50)    NOT NULL DEFAULT 'manual',
    trace_id             VARCHAR(255)   NOT NULL DEFAULT '',
    resume_checkpoint_id VARCHAR(255),
    idempotency_key      VARCHAR(255),
    status               VARCHAR(50)    NOT NULL DEFAULT 'pending',
    lease_owner          VARCHAR(255),
    lease_expires_at     TIMESTAMPTZ,
    attempts             INTEGER        NOT NULL DEFAULT 0,
    last_error           TEXT,
    created_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

-- 幂等键唯一约束：同一幂等键只入队一个作业（非终态重复提交返回既有）
CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_job_idempotency
    ON execution_job (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- 认领扫描索引：pending 作业按入队时刻先进先出
CREATE INDEX IF NOT EXISTS idx_execution_job_claim
    ON execution_job (created_at, job_id)
    WHERE status = 'pending';

-- 接管扫描索引：租约过期的 running 作业按到期时刻接管
CREATE INDEX IF NOT EXISTS idx_execution_job_takeover
    ON execution_job (lease_expires_at, job_id)
    WHERE status = 'running';
