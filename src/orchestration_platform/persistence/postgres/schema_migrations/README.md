# PostgreSQL 表结构迁移说明（schema_migrations/）

## 迁移原则

1. **不得造成存量实例不可恢复**——迁移脚本必须保证已有数据可被新版本正确读取；
2. **向前兼容**——新增字段必须有默认值，删除字段必须先经过弃用周期；
3. **版本登记**——每个迁移向 `schema_versions` 表登记版本（`ON CONFLICT (version) DO NOTHING`），
   已应用版本以登记记录为准，勿凭记忆重跑；
4. **幂等守卫**——建表/加列/约束/更名均带存在性守卫（`IF NOT EXISTS` /
   `ADD COLUMN IF NOT EXISTS` / `to_regclass`），任意前态重复执行零副作用；
   **例外：v1_5 应用后禁止重跑 v1_2**（见「v1_5 说明与限制」）。

## 迁移脚本清单

| 顺序 | 脚本 | 版本 | 内容 | 配对回滚 |
| --- | --- | --- | --- | --- |
| 前置 | `../table_definitions.sql` | - | 基础 4 表（`workflow_definition` / `execution_instance` / `checkpoint` / `execution_event`） | - |
| 1 | `v1_1_phase2_enhancement.sql` | 1.1 | 第二阶段引擎增强：`schema_versions` 建表守卫、`context_checkpoints` / `mcp_tool_registrations` / `data_source_connections` 新表、checkpoint / execution_event / workflow_definition 加列 | `v1_1_rollback.sql` |
| 2 | `v1_2_phase3_governance.sql` | 1.2 | 第三阶段生产治理：16 张治理表（tenants / users / roles / token_usage_records 等）、基础 4 表租户化加列 | `v1_2_rollback.sql` |
| 3 | `v1_3_checkpoint_unique.sql` | 1.3 | 检查点同轮唯一约束 | `v1_3_rollback.sql` |
| 4 | `v1_4_vector_kb_records.sql` | 1.4 | 向量知识库 PG 数组存储 | —（无回滚脚本） |
| 5 | `v1_5_rename_governance_tables.sql` | 1.5 | 治理表更名与仓储契约对齐（`governance_*`） | `v1_5_rollback.sql` |
| 6 | `v1_6_tenant_hardening.sql` | 1.6 | 多租户强隔离第一段：8 张可空表 backfill + `SET NOT NULL`（roadmap B1） | `v1_6_rollback.sql` |
| 7 | `v1_7_enable_rls.sql` | 1.7 | 多租户强隔离第二段：21 表 + 3 分区 RLS 策略三件套（roadmap B1） | `v1_7_rollback.sql` |
| 8 | `v1_8_execution_queue.sql` | 1.8 | 执行队列表 `execution_job`（租约/心跳字段 + 幂等键部分唯一索引 + 认领/接管扫描部分索引，roadmap M1） | `v1_8_execution_queue_rollback.sql` |
| 9 | `v1_9_job_cancel_requested.sql` | 1.9 | 作业取消标志：`execution_job.cancel_requested` + 实例活跃作业索引（M3 实例级取消，跨进程传播载体） | `v1_9_rollback.sql` |
| 10 | `v1_10_workflow_publish_state.sql` | 1.10 | 工作流定义发布态：`status`/`draft_of`/`published_at` 三列 + 状态索引（B11 定义版本与发布态，发布态不可变守卫；既有行回填 `'draft'`） | `v1_10_rollback.sql` |

## 执行方式

所有迁移**必须**以 `-v ON_ERROR_STOP=1` 执行（出错即停，禁止半套迁移静默通过）：

```bash
# 新库初始化（空库全链，按顺序执行）
psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB \
  -v ON_ERROR_STOP=1 -f table_definitions.sql
psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB \
  -v ON_ERROR_STOP=1 -f schema_migrations/v1_1_phase2_enhancement.sql
# ……依次执行至 v1_7_enable_rls.sql

# 存量库（已应用 1.1 ~ 1.4）：仅执行 v1_5，再依次 v1_6 / v1_7
psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB \
  -v ON_ERROR_STOP=1 -f schema_migrations/v1_5_rename_governance_tables.sql

# 版本核对
psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT version, description, applied_at FROM schema_versions ORDER BY applied_at;"
```

容器场景（迁移演练）使用 `docker cp` 拷入脚本后 `docker exec ... psql -v ON_ERROR_STOP=1 -f ...`。

## v1_5 说明与限制（治理表更名与仓储契约对齐）

1. **八表守卫更名**：`tenants→governance_tenant`、`tenant_quotas→governance_tenant_quota`、
   `org_nodes→governance_org_node`、`users→governance_user`、`roles→governance_role`、
   `role_bindings→governance_role_binding`、`access_tokens→governance_access_token`、
   `token_usage_records→governance_token_usage`——六大 PostgreSQL 仓储静态 SQL 只引用
   `governance_*` 表名，本迁移消除「迁移建表 ↔ 仓储读写」断链；
2. **新建 `governance_permission`**：主键 `(tenant_id, permission_id)`，
   对齐 `postgres_role_repository.save_permission` 的 upsert 契约；
3. **四组唯一约束**：对齐 user / org_node / role / role_binding 仓储的
   `ON CONFLICT` 幂等 upsert 冲突键；
4. **三张 LIST 分区表补建 DEFAULT 分区**（`governance_token_usage` / `cost_records` /
   `audit_log_entries`）——无 DEFAULT 分区时 INSERT 报 no partition found；
5. **v1_5 应用后禁止重跑 v1_2**：v1_2 以 `CREATE TABLE IF NOT EXISTS` 建复数治理表，
   更名后 `tenants` 等旧名已不存在，重跑会重建一批**无数据的复数幽灵表**；
6. **回滚数据丢失警告**（`v1_5_rollback.sql`）：DROP 三张 DEFAULT 分区
   将丢失 Token 用量 / 成本 / 审计已写入数据，DROP `governance_permission` 丢失权限项数据；
   仅用于迁移演练与紧急回退，生产执行前必须先备份。

## v1_6 / v1_7 说明（多租户强隔离，roadmap B1）

### v1_6_tenant_hardening.sql（版本 1.6，分级启用第一段）

1. **8 张可空表收紧**：核心 4 表（`workflow_definition` / `execution_instance` /
   `checkpoint` / `execution_event`，v1_2 补列时未加 NOT NULL）+ 4 张孤儿
   （`pricing_rules` / `mask_rules` / `scheduler_policies` / `resource_pools`）；
2. **backfill**：`tenant_id IS NULL` 的存量行统一写 `'tenant_default'`（与 v1_2
   列默认值、种子租户一致）——**不可逆数据变更**，回滚脚本仅撤 NOT NULL 约束；
3. **幂等守卫**：`to_regclass` 表存在 + `information_schema.columns`
   `is_nullable='YES'` 列可空，双重守卫后执行 backfill + `SET NOT NULL`；
   已收紧库重复执行零副作用；缺列前态（仅 table_definitions）安全跳过；
4. **影子校验**：v1_6 与 v1_7 之间应跑仓储冒烟确认数据无损（分级启用，
   roadmap 风险登记要求），演练见批次档案 drill-record.md。

### v1_7_enable_rls.sql（版本 1.7，分级启用第二段）

1. **24 处三件套**：21 张含租户表 + 3 个 DEFAULT 分区
   （`governance_token_usage_default` / `cost_records_default` /
   `audit_log_entries_default`）逐一 `CREATE POLICY p_tenant_isolation` +
   `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY`——
   验收口径「业务表策略覆盖 100%」；
2. **策略双通道**（FOR ALL，USING 与 WITH CHECK 同谓词）：
   - 租户通道：`tenant_id = current_setting('app.current_tenant', true)`——
     仓储基类 `TenantScopedPostgresRepository` 在事务内
     `SELECT set_config('app.current_tenant', $1, true)` 注入（第二道保险；
     仓储 SQL 的 WHERE tenant_id 过滤为第一道）；
   - 运维通道：`current_setting('app.bypass_rls', true) = 'on'`——应用信任级
     开关（app 角色可 SET 任意 GUC），供运维/演练及预租户上下文认证入口
     （token `get_by_hash` 等）跨租户管理使用，安全边界由应用层控制；
3. **fail-closed**：未设 `app.current_tenant` 时 `current_setting(..., true)`
   返回 NULL，等值比较不成立 → 无行可见/可写（无租户上下文查询返回 0 行）；
4. **FORCE 必需**：应用角色 = 表 owner，不加 FORCE 则 owner 绕过 RLS；
5. **分区直连防护**：直接查询分区仅适用分区自身策略，故 DEFAULT 分区
   单独启用三件套（防直连分区绕过父表策略）；
6. **schema_versions 不入策略范围**：无 tenant_id，迁移登记永远可用；
7. **回滚顺序 v1_7 → v1_6**（先 `v1_7_rollback.sql` 撤策略，再
   `v1_6_rollback.sql` 撤约束）；v1_6 回滚不恢复 backfill 前的历史 NULL。

### RLS 启用后的运维直连须知

RLS 生效后 psql / 运维工具直连查询含租户表**默认 0 行**（fail-closed），
需先在会话内注入上下文（均为事务级，随事务结束失效）：

```sql
-- 租户上下文（单租户视角）
SELECT set_config('app.current_tenant', 'tenant_default', true);
-- 或运维旁路（跨租户管理视角，应用信任级开关）
SELECT set_config('app.bypass_rls', 'on', true);
```

核对策略覆盖（应为 24）：

```sql
SELECT count(*) FROM pg_policy WHERE polname = 'p_tenant_isolation';
SELECT relname, relrowsecurity, relforcerowsecurity
  FROM pg_class
 WHERE relname IN ('governance_user', 'workflow_definition', /* … */)
 ORDER BY relname;
```

## 历史勘误（2026-11-21，roadmap B0/D1）

1. v1_1 / v1_2 初版曾以复数表名（`workflow_definitions` / `checkpoints` / `events` 等）
   引用 `table_definitions.sql` 的单数表（`workflow_definition` / `checkpoint` /
   `execution_event`），已原地勘误为单数——旧文档与既有演练记录中的复数表名作废；
2. v1_2 头部注释「15 张」与实际建表 16 张为既有口径差，原文保留，仅在此登记；
3. 初版 README 引用的 `v1_to_v2.sql` 从未存在，该引用已删除；
4. 初版 README 缺迁移清单与 ON_ERROR_STOP 要求，本版补齐；
5. 初版清单 v1_4 行配对回滚列写的 `v1_4_rollback.sql` 从未存在（与第 3 条同类
   死引用），已改为「—（无回滚脚本）」——v1_4 向量知识库迁移无回滚脚本为既定
   事实（建表 + `CREATE INDEX IF NOT EXISTS`，回滚=手工 DROP）。
