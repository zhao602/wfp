-- =============================================================================
-- 建表脚本（persistence/postgres/table_definitions.sql）
--
-- 职责：创建工作流编排平台的核心数据表。
-- 设计意图（对应 spec 4.5.3、4.2.4、4.2.5、design 2.3）：
--   1. 四张表均含 schema_version 字段，为第二阶段迁移预留；
--   2. 幂等键唯一约束保证重复提交返回既有实例；
--   3. 事件序号与检查点位点在同一事务内一致。
-- =============================================================================

-- 工作流定义表：版本化存储
CREATE TABLE IF NOT EXISTS workflow_definition (
    definition_id       VARCHAR(255)   NOT NULL,
    definition_version  INTEGER        NOT NULL DEFAULT 1,
    name                VARCHAR(100)   NOT NULL,
    spec_version        VARCHAR(50)    NOT NULL,
    definition_body     JSONB          NOT NULL,
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    schema_version      INTEGER        NOT NULL DEFAULT 1,
    PRIMARY KEY (definition_id, definition_version)
);

CREATE INDEX IF NOT EXISTS idx_workflow_definition_id
    ON workflow_definition (definition_id);

-- 执行实例表
CREATE TABLE IF NOT EXISTS execution_instance (
    instance_id         VARCHAR(255)   PRIMARY KEY,
    definition_id       VARCHAR(255)   NOT NULL,
    definition_version  INTEGER        NOT NULL,
    status              VARCHAR(50)    NOT NULL DEFAULT 'pending',
    inputs              JSONB          NOT NULL DEFAULT '{}',
    outputs             JSONB          NOT NULL DEFAULT '{}',
    idempotency_key     VARCHAR(255),
    trigger_type        VARCHAR(50)    NOT NULL DEFAULT 'manual',
    trace_id            VARCHAR(255)   NOT NULL,
    current_node_id     VARCHAR(255),
    error               JSONB,
    started_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    schema_version      INTEGER        NOT NULL DEFAULT 1
);

-- 幂等键唯一约束：同一幂等键只创建一个实例
CREATE UNIQUE INDEX IF NOT EXISTS idx_instance_idempotency
    ON execution_instance (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- 检查点表：追加写模型
CREATE TABLE IF NOT EXISTS checkpoint (
    checkpoint_id       VARCHAR(255)   PRIMARY KEY,
    instance_id         VARCHAR(255)   NOT NULL,
    completed_node_id   VARCHAR(255),
    next_node_id        VARCHAR(255),
    state_snapshot      JSONB          NOT NULL,
    round_index         INTEGER        NOT NULL,
    is_available        BOOLEAN        NOT NULL DEFAULT TRUE,
    unavailable_reason  TEXT,
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    schema_version      INTEGER        NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_checkpoint_instance
    ON checkpoint (instance_id, round_index DESC);

-- 执行事件表：不可变追加写
CREATE TABLE IF NOT EXISTS execution_event (
    event_id            VARCHAR(255)   PRIMARY KEY,
    instance_id         VARCHAR(255)   NOT NULL,
    event_type          VARCHAR(50)    NOT NULL,
    node_id             VARCHAR(255),
    sequence            INTEGER        NOT NULL,
    payload             JSONB          NOT NULL DEFAULT '{}',
    voided              BOOLEAN        NOT NULL DEFAULT FALSE,
    occurred_at         TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    schema_version      INTEGER        NOT NULL DEFAULT 1,
    UNIQUE (instance_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_event_instance_seq
    ON execution_event (instance_id, sequence);
