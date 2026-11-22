-- =============================================================================
-- 第二阶段存储迁移脚本（v1_1_phase2_enhancement.sql）
--
-- 职责：从第一阶段 schema 迁移至第二阶段 schema。
-- 设计意图（对应 spec 4.5.5、design 2.9.4、任务 T42）：
--   1. 新增 context_checkpoints、mcp_tool_registrations、data_source_connections 三张表；
--   2. 为既有表追加可选列（不修改既有列）；
--   3. 迁移无损且幂等（重复执行不产生副作用）；
--   4. 按 schema_version 增量执行。
--
-- 历史勘误（2026-11-21，roadmap B0/D1，就地修正）：
--   1. 既有表扩展与索引语句原误用复数表名（checkpoints/events/workflow_definitions），
--      实际表定义为单数（checkpoint/execution_event/workflow_definition，见 table_definitions.sql）；
--   2. 版本记录 INSERT 前补 schema_versions 幂等建表
--      （table_definitions.sql 未建该表，空库直接执行本脚本原先会失败）；
--   3. idx_mcp_tool_category 表达式索引原缺括号（GIN (descriptor->'tags') 为语法错误，
--      PG 要求表达式索引元素整体加括号），Docker/本地演练暴露的第 3 类死语句，就地修正。
-- =============================================================================

-- 迁移版本登记表（幂等建表：全链路版本 INSERT 的前置依赖）
CREATE TABLE IF NOT EXISTS schema_versions (
    version     VARCHAR(32)               NOT NULL,
    description TEXT,
    applied_at  TIMESTAMP WITH TIME ZONE  DEFAULT NOW(),
    PRIMARY KEY (version)
);

-- 迁移版本记录
INSERT INTO schema_versions (version, description) VALUES ('1.1', '第二阶段引擎增强')
ON CONFLICT (version) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 1. 新增表：上下文检查点
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS context_checkpoints (
    checkpoint_id      VARCHAR(64)   PRIMARY KEY,
    instance_id        VARCHAR(64)   NOT NULL,
    node_id            VARCHAR(64)   NOT NULL,
    agent_context      JSONB         NOT NULL,
    window_strategy    VARCHAR(32),
    token_count        INTEGER,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version     VARCHAR(8)    DEFAULT '1.1'
);

CREATE INDEX IF NOT EXISTS idx_context_cp_instance ON context_checkpoints(instance_id);

-- -----------------------------------------------------------------------------
-- 2. 新增表：MCP 工具注册
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mcp_tool_registrations (
    tool_identifier    TEXT          NOT NULL,
    version            TEXT          NOT NULL,
    descriptor         JSONB         NOT NULL,
    registered_by      VARCHAR(64)   NOT NULL,
    registered_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_active          BOOLEAN       DEFAULT TRUE,
    previous_version   TEXT,
    schema_version     VARCHAR(8)    DEFAULT '1.1',
    PRIMARY KEY (tool_identifier, version)
);

CREATE INDEX IF NOT EXISTS idx_mcp_tool_category ON mcp_tool_registrations USING GIN ((descriptor->'tags'));

-- -----------------------------------------------------------------------------
-- 3. 新增表：数据源连接
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_source_connections (
    connection_id      VARCHAR(64)   PRIMARY KEY,
    source_type        VARCHAR(32)   NOT NULL,
    connection_config  JSONB         NOT NULL,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version     VARCHAR(8)    DEFAULT '1.1'
);

-- -----------------------------------------------------------------------------
-- 4. 既有表可选列扩展（幂等：IF NOT EXISTS）
-- -----------------------------------------------------------------------------

-- checkpoint 表追加压缩元数据与分支标识
ALTER TABLE checkpoint ADD COLUMN IF NOT EXISTS compression_meta JSONB;
ALTER TABLE checkpoint ADD COLUMN IF NOT EXISTS branch_id VARCHAR(64);

-- execution_event 表追加第二阶段扩展字段
ALTER TABLE execution_event ADD COLUMN IF NOT EXISTS subworkflow_id VARCHAR(64);
ALTER TABLE execution_event ADD COLUMN IF NOT EXISTS nesting_depth INTEGER;
ALTER TABLE execution_event ADD COLUMN IF NOT EXISTS injection_seq INTEGER;
ALTER TABLE execution_event ADD COLUMN IF NOT EXISTS strategy_type VARCHAR(32);
ALTER TABLE execution_event ADD COLUMN IF NOT EXISTS tool_id VARCHAR(64);

-- workflow_definition 表追加规范版本号（若第一阶段未建；与既有列 IF NOT EXISTS 幂等共存）
ALTER TABLE workflow_definition ADD COLUMN IF NOT EXISTS spec_version VARCHAR(8) DEFAULT '1.0';

-- -----------------------------------------------------------------------------
-- 5. 索引扩展
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_checkpoints_branch ON checkpoint(branch_id) WHERE branch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_events_subworkflow ON execution_event(subworkflow_id) WHERE subworkflow_id IS NOT NULL;
