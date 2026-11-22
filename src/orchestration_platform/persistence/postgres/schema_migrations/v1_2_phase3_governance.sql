-- =============================================================================
-- 第三阶段治理迁移脚本（v1_2_phase3_governance.sql）
--
-- 职责：从第二阶段 schema 迁移至第三阶段治理 schema。
-- 设计意图（对应 spec 4.5.5、design 2.9、任务 T49）：
--   1. 新增 15 张治理表；
--   2. 为既有表追加可选列（不修改既有列）；
--   3. 初始化默认租户与默认组织；
--   4. 迁移无损且幂等。
--
-- 历史勘误（2026-11-21，roadmap B0/D1，就地修正）：
--   第 5 节既有表扩展语句原误用复数表名（workflow_definitions/execution_instances/
--   checkpoints/events），实际表定义为单数（见 table_definitions.sql）。
--   注：本脚本创建的治理表（tenants/users/roles 等）在 v1_5 中更名为 governance_* 前缀。
-- =============================================================================

-- 迁移版本记录
INSERT INTO schema_versions (version, description) VALUES ('1.2', '第三阶段生产治理')
ON CONFLICT (version) DO NOTHING;

-- =============================================================================
-- 1. RBAC + 多租户表
-- =============================================================================

-- 租户表
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id      VARCHAR(64)   PRIMARY KEY,
    tenant_name    VARCHAR(256)  NOT NULL,
    status         VARCHAR(32)   DEFAULT 'active',
    is_default     BOOLEAN       DEFAULT FALSE,
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version VARCHAR(8)    DEFAULT '1.2'
);

-- 租户配额表
CREATE TABLE IF NOT EXISTS tenant_quotas (
    tenant_id          VARCHAR(64)   PRIMARY KEY REFERENCES tenants(tenant_id),
    max_instances      INTEGER       DEFAULT 100,
    max_workflows      INTEGER       DEFAULT 1000,
    max_tokens_per_day BIGINT,
    max_storage_gb     INTEGER,
    updated_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version     VARCHAR(8)    DEFAULT '1.2'
);

-- 组织节点表
CREATE TABLE IF NOT EXISTS org_nodes (
    org_node_id    VARCHAR(64)   PRIMARY KEY,
    tenant_id      VARCHAR(64)   NOT NULL REFERENCES tenants(tenant_id),
    node_name      VARCHAR(256)  NOT NULL,
    parent_node_id VARCHAR(64)   REFERENCES org_nodes(org_node_id),
    node_path      TEXT          NOT NULL,
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version VARCHAR(8)    DEFAULT '1.2'
);

CREATE INDEX IF NOT EXISTS idx_org_nodes_tenant ON org_nodes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_org_nodes_parent ON org_nodes(parent_node_id);

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    user_id        VARCHAR(128)  PRIMARY KEY,
    tenant_id      VARCHAR(64)   NOT NULL REFERENCES tenants(tenant_id),
    username       VARCHAR(128)  NOT NULL,
    display_name   VARCHAR(256),
    email          VARCHAR(256),
    org_node_id    VARCHAR(64)   REFERENCES org_nodes(org_node_id),
    status         VARCHAR(32)   DEFAULT 'active',
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version VARCHAR(8)    DEFAULT '1.2',
    UNIQUE(tenant_id, username)
);

-- 角色表
CREATE TABLE IF NOT EXISTS roles (
    role_id        VARCHAR(64)   PRIMARY KEY,
    role_name      VARCHAR(256)  NOT NULL,
    tenant_id      VARCHAR(64)   NOT NULL REFERENCES tenants(tenant_id),
    permissions    JSONB         DEFAULT '[]',
    parent_role_id VARCHAR(64)   REFERENCES roles(role_id),
    is_system_role BOOLEAN       DEFAULT FALSE,
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version VARCHAR(8)    DEFAULT '1.2'
);

-- 角色绑定表
CREATE TABLE IF NOT EXISTS role_bindings (
    binding_id    VARCHAR(64)   PRIMARY KEY,
    role_id       VARCHAR(64)   NOT NULL REFERENCES roles(role_id),
    user_id       VARCHAR(128)  NOT NULL REFERENCES users(user_id),
    org_node_id   VARCHAR(64)   REFERENCES org_nodes(org_node_id),
    tenant_id     VARCHAR(64)   NOT NULL REFERENCES tenants(tenant_id),
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version VARCHAR(8)   DEFAULT '1.2'
);

-- 访问令牌表
CREATE TABLE IF NOT EXISTS access_tokens (
    token_id      VARCHAR(64)   PRIMARY KEY,
    user_id       VARCHAR(128)  NOT NULL REFERENCES users(user_id),
    tenant_id     VARCHAR(64)   NOT NULL REFERENCES tenants(tenant_id),
    token_hash    VARCHAR(256)  NOT NULL,
    status        VARCHAR(32)   DEFAULT 'active',
    issued_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at    TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at    TIMESTAMP WITH TIME ZONE,
    last_used_at  TIMESTAMP WITH TIME ZONE,
    schema_version VARCHAR(8)   DEFAULT '1.2'
);

-- =============================================================================
-- 2. Token 计量与计费表
-- =============================================================================

-- Token 用量记录表（按租户分区）
CREATE TABLE IF NOT EXISTS token_usage_records (
    record_id            VARCHAR(64)   NOT NULL,
    tenant_id            VARCHAR(64)   NOT NULL,
    instance_id          VARCHAR(64)   NOT NULL,
    node_id              VARCHAR(64)   NOT NULL,
    agent_node_id        VARCHAR(64),
    model_identifier     VARCHAR(128)  NOT NULL,
    prompt_tokens        INTEGER       DEFAULT 0,
    completion_tokens    INTEGER       DEFAULT 0,
    total_tokens         INTEGER       DEFAULT 0,
    workflow_definition_id VARCHAR(128),
    recorded_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version       VARCHAR(8)    DEFAULT '1.2',
    PRIMARY KEY (tenant_id, record_id)
) PARTITION BY LIST (tenant_id);

-- 定价规则表
CREATE TABLE IF NOT EXISTS pricing_rules (
    rule_id              VARCHAR(64)   PRIMARY KEY,
    model_identifier     VARCHAR(128)  NOT NULL,
    prompt_token_price   DECIMAL(18,6) DEFAULT 0,
    completion_token_price DECIMAL(18,6) DEFAULT 0,
    effective_from       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    effective_to         TIMESTAMP WITH TIME ZONE,
    tenant_id            VARCHAR(64)   REFERENCES tenants(tenant_id),
    schema_version       VARCHAR(8)    DEFAULT '1.2'
);

-- 成本记录表（按租户分区）
CREATE TABLE IF NOT EXISTS cost_records (
    cost_id        VARCHAR(64)   NOT NULL,
    tenant_id      VARCHAR(64)   NOT NULL,
    usage_record_id VARCHAR(64) NOT NULL,
    instance_id    VARCHAR(64)   NOT NULL,
    cost_amount    DECIMAL(18,6) NOT NULL,
    pricing_rule_id VARCHAR(64) NOT NULL,
    calculated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version VARCHAR(8)    DEFAULT '1.2',
    PRIMARY KEY (tenant_id, cost_id)
) PARTITION BY LIST (tenant_id);

-- 预算表
CREATE TABLE IF NOT EXISTS budgets (
    budget_id              VARCHAR(64)   PRIMARY KEY,
    tenant_id              VARCHAR(64)   NOT NULL REFERENCES tenants(tenant_id),
    scope                  VARCHAR(32)   DEFAULT 'tenant',
    target_id              VARCHAR(128)  NOT NULL,
    period                 VARCHAR(32)   DEFAULT 'monthly',
    budget_limit           DECIMAL(18,6) NOT NULL,
    alert_threshold        DECIMAL(5,4)  DEFAULT 0.8,
    circuit_break_strategy VARCHAR(32)   DEFAULT 'soft_alert',
    current_usage          DECIMAL(18,6) DEFAULT 0,
    is_circuit_broken      BOOLEAN       DEFAULT FALSE,
    period_start           TIMESTAMP WITH TIME ZONE NOT NULL,
    period_end             TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at             TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version         VARCHAR(8)    DEFAULT '1.2'
);

-- =============================================================================
-- 3. 审计合规表
-- =============================================================================

-- 审计日志表（追加写入，禁止 UPDATE/DELETE）
CREATE TABLE IF NOT EXISTS audit_log_entries (
    entry_id         VARCHAR(64)   NOT NULL,
    tenant_id        VARCHAR(64)   NOT NULL,
    actor_id         VARCHAR(128)  NOT NULL,
    actor_type       VARCHAR(32)   DEFAULT 'user',
    resource_type    VARCHAR(32)   NOT NULL,
    resource_id      VARCHAR(128)  NOT NULL,
    operation        VARCHAR(32)   NOT NULL,
    operation_result VARCHAR(32)   DEFAULT 'success',
    request_payload  JSONB,
    response_summary TEXT,
    trace_id         VARCHAR(64)   NOT NULL,
    ip_address       VARCHAR(64),
    occurred_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    previous_hash    VARCHAR(64)   DEFAULT '',
    entry_hash       VARCHAR(64)   DEFAULT '',
    schema_version   VARCHAR(8)    DEFAULT '1.2',
    PRIMARY KEY (tenant_id, entry_id)
) PARTITION BY LIST (tenant_id);

-- 禁止审计日志 UPDATE/DELETE 的触发器
CREATE OR REPLACE FUNCTION prevent_audit_modify() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '审计日志只允许追加，禁止修改或删除';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_audit_update ON audit_log_entries;
DROP TRIGGER IF EXISTS trg_prevent_audit_delete ON audit_log_entries;
CREATE TRIGGER trg_prevent_audit_update BEFORE UPDATE ON audit_log_entries
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modify();
CREATE TRIGGER trg_prevent_audit_delete BEFORE DELETE ON audit_log_entries
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modify();

-- 脱敏规则表
CREATE TABLE IF NOT EXISTS mask_rules (
    rule_id      VARCHAR(64)   PRIMARY KEY,
    tenant_id    VARCHAR(64)   REFERENCES tenants(tenant_id),
    field_path   TEXT          NOT NULL,
    mask_method  VARCHAR(32)   DEFAULT 'full_mask',
    replacement  TEXT,
    keep_prefix  INTEGER       DEFAULT 0,
    keep_suffix  INTEGER       DEFAULT 0,
    is_active    BOOLEAN       DEFAULT TRUE,
    schema_version VARCHAR(8)  DEFAULT '1.2'
);

-- =============================================================================
-- 4. 全局调度与资源治理表
-- =============================================================================

-- 调度策略表
CREATE TABLE IF NOT EXISTS scheduler_policies (
    policy_id            VARCHAR(64)   PRIMARY KEY,
    tenant_id            VARCHAR(64)   REFERENCES tenants(tenant_id),
    policy_type          VARCHAR(32)   DEFAULT 'strict_priority',
    default_priority     VARCHAR(16)   DEFAULT 'normal',
    fair_weight          INTEGER       DEFAULT 1,
    max_queue_size       INTEGER       DEFAULT 10000,
    preemption_enabled   BOOLEAN       DEFAULT TRUE,
    preemption_max_count INTEGER       DEFAULT 3,
    decision_timeout_ms  INTEGER       DEFAULT 100,
    updated_at           TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version       VARCHAR(8)    DEFAULT '1.2'
);

-- 资源池表
CREATE TABLE IF NOT EXISTS resource_pools (
    pool_id                VARCHAR(64)   PRIMARY KEY,
    tenant_id              VARCHAR(64)   REFERENCES tenants(tenant_id),
    pool_type              VARCHAR(32)   NOT NULL,
    total_capacity         INTEGER       NOT NULL,
    allocated              INTEGER       DEFAULT 0,
    available              INTEGER       NOT NULL,
    oversubscription_ratio DECIMAL(5,2)  DEFAULT 1.0,
    max_capacity           INTEGER       NOT NULL,
    updated_at             TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    schema_version         VARCHAR(8)    DEFAULT '1.2'
);

-- 全局调度队列表
CREATE TABLE IF NOT EXISTS global_scheduler_queue (
    queue_entry_id  VARCHAR(64)   PRIMARY KEY,
    tenant_id       VARCHAR(64)   NOT NULL,
    instance_id     VARCHAR(64)   NOT NULL,
    priority        VARCHAR(16)   DEFAULT 'normal',
    policy_id       VARCHAR(64)   REFERENCES scheduler_policies(policy_id),
    status          VARCHAR(32)   DEFAULT 'queued',
    enqueued_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    dequeued_at     TIMESTAMP WITH TIME ZONE,
    schema_version  VARCHAR(8)    DEFAULT '1.2'
);

-- =============================================================================
-- 5. 既有表追加可选列（幂等：IF NOT EXISTS）
-- =============================================================================

ALTER TABLE workflow_definition ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) DEFAULT 'tenant_default';
ALTER TABLE workflow_definition ADD COLUMN IF NOT EXISTS created_by VARCHAR(128) DEFAULT 'system';

ALTER TABLE execution_instance ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) DEFAULT 'tenant_default';
ALTER TABLE execution_instance ADD COLUMN IF NOT EXISTS created_by VARCHAR(128) DEFAULT 'system';
ALTER TABLE execution_instance ADD COLUMN IF NOT EXISTS priority VARCHAR(16) DEFAULT 'normal';
ALTER TABLE execution_instance ADD COLUMN IF NOT EXISTS preemption_count INTEGER DEFAULT 0;

ALTER TABLE checkpoint ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) DEFAULT 'tenant_default';

ALTER TABLE execution_event ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) DEFAULT 'tenant_default';
ALTER TABLE execution_event ADD COLUMN IF NOT EXISTS budget_id VARCHAR(128);
ALTER TABLE execution_event ADD COLUMN IF NOT EXISTS resource_pool_id VARCHAR(128);
ALTER TABLE execution_event ADD COLUMN IF NOT EXISTS preempted_instance_id VARCHAR(128);

-- =============================================================================
-- 6. 默认租户与组织初始化
-- =============================================================================

INSERT INTO tenants (tenant_id, tenant_name, status, is_default)
VALUES ('tenant_default', '默认租户', 'active', TRUE)
ON CONFLICT (tenant_id) DO NOTHING;

INSERT INTO tenant_quotas (tenant_id, max_instances, max_workflows)
VALUES ('tenant_default', 100, 1000)
ON CONFLICT (tenant_id) DO NOTHING;

INSERT INTO org_nodes (org_node_id, tenant_id, node_name, parent_node_id, node_path)
VALUES ('org_default_root', 'tenant_default', '默认组织', NULL, '/root')
ON CONFLICT (org_node_id) DO NOTHING;

-- 默认角色：租户管理员与编排工程师
INSERT INTO roles (role_id, role_name, tenant_id, permissions, is_system_role)
VALUES (
    'role_tenant_admin', '租户管理员', 'tenant_default',
    '[{"permission_id":"admin_all","resource_type":"governance_policy","operation":"execute","scope":"tenant"}]'::jsonb,
    TRUE
)
ON CONFLICT (role_id) DO NOTHING;

INSERT INTO roles (role_id, role_name, tenant_id, permissions, is_system_role)
VALUES (
    'role_orchestrator', '编排工程师', 'tenant_default',
    '[{"permission_id":"wf_manage","resource_type":"workflow_definition","operation":"create","scope":"self"}]'::jsonb,
    TRUE
)
ON CONFLICT (role_id) DO NOTHING;

-- =============================================================================
-- 7. 索引扩展
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_role_bindings_user ON role_bindings(user_id);
CREATE INDEX IF NOT EXISTS idx_access_tokens_hash ON access_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_time ON audit_log_entries(tenant_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_usage_tenant_time ON token_usage_records(tenant_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_budget_tenant ON budgets(tenant_id);
CREATE INDEX IF NOT EXISTS idx_scheduler_queue_status ON global_scheduler_queue(status, priority);
