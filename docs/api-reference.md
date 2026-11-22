# API 参考

> 装配实测口径：`create_app`（`src/orchestration_platform/api/application_factory.py`）共 **32 个 `include_router` 装配、133 个端点**（路由装饰器口径；B2 嵌入 SDK 批次 +2：实例取消/结果）；特性开关门控的族在关闭时**不注册路由（访问返回 404）**。

## 1. 总览

- **基址前缀**：全部业务路由挂载于 `/api/v1`。
- **鉴权**：RBAC 鉴权中间件（`api/middleware/rbac_authorization_middleware.py`）对全部 `/api/v1/**` 生效，要求 `Authorization: Bearer <token>`；豁免前缀（`_PUBLIC_PATHS`）：`/healthz`、`/metrics`、`/docs`、`/openapi.json`、`/redoc`、`/api/v1/auth/token`。
- **开关门控语义**：门控族的路由是否注册由 `settings.py` 对应布尔开关决定（默认值见 [config.md](./config.md)）；关闭时端点不存在（404），而非 403。
- **SSE**：执行事件流为 `EventSource` 文本流端点（见 §4）。

## 2. 端点清单（按路由族，全部省略 `/api/v1` 前缀）

| 路由族 | 开关门控（默认） | 端点（方法 路径） |
| --- | --- | --- |
| 工作流定义 `workflow_definition_router` | — | POST `/workflows/validate` · POST `/workflows` · GET `/workflows/{definition_id}` |
| 执行实例 `execution_instance_router` | — | POST `/instances` · GET `/instances/{instance_id}` · GET `/instances/{instance_id}/events` · GET `/instances/{instance_id}/events/stream`（SSE） · POST `/instances/{instance_id}/cancel`（B2）· GET `/instances/{instance_id}/results`（B2） · GET `/instances/{instance_id}/checkpoints` · POST `/instances/{instance_id}/recover` |
| 回滚重放 `rollback_replay_router` | — | POST `/instances/{instance_id}/rollback` · POST `/instances/{instance_id}/replay` · POST `/instances/{instance_id}/review` |
| 实时订阅 `realtime_subscription_router`（内部前缀 `/subscriptions`） | — | POST `/subscriptions` · GET `/subscriptions` · GET `/subscriptions/{subscription_id}` · POST `/subscriptions/{subscription_id}/pause` · POST `/subscriptions/{subscription_id}/resume` · DELETE `/subscriptions/{subscription_id}` · GET `/subscriptions/{subscription_id}/history` |
| 上下文查询 `context_query_router` | — | GET `/instances/{instance_id}/context` · GET `/instances/{instance_id}/context/{checkpoint_id}` |
| MCP 工具 `mcp_server_router` | — | GET `/mcp/server/tools` |
| 工具目录 `tool_catalog_router` | — | GET `/tools` |
| 模型目录 `model_catalog_router` | — | GET `/models/available` |
| 治理核心 `governance_router`（内部前缀 `/governance`） | — | POST `/governance/tenants` · GET `/governance/tenants` · POST `/governance/auth/token` · POST `/governance/auth/token/revoke` · GET `/governance/usage` · GET `/governance/costs` · GET `/governance/budgets/{budget_id}/status` · GET `/governance/audit-logs` · POST `/governance/audit-logs/export` · GET `/governance/scheduler/policy` · GET `/governance/resource-pools` |
| NL 编排 `nl_orchestration_router` | `nl_orchestration_enabled`（false） | POST `/nl_orchestration/create` · POST `/nl_orchestration/modify` |
| ABAC `abac_router` | `abac_enabled`（false） | POST `/governance/abac/policies` · GET `/governance/abac/policies` · DELETE `/governance/abac/policies/{policy_id}` |
| 墓碑 `tombstone_router` | `tombstone_enabled`（true） | POST `/governance/audit/tombstone/mark` · POST `/governance/audit/tombstone/forget-user` · GET `/governance/audit/tombstone` · DELETE `/governance/audit/logs/{entry_id}` |
| 缓存观测 `cache_router` | — | GET `/governance/cache/status` · GET `/governance/cache/health` |
| 事件总线观测 `event_bus_router` | — | GET `/governance/event-bus/status` · GET `/governance/event-bus/health` |
| 沙箱 `sandbox_router` | `sandbox_enabled`（false） | POST `/governance/sandbox/policies` · GET `/governance/sandbox/policies` · DELETE `/governance/sandbox/policies/{policy_id}` · GET `/governance/sandbox/violations` · GET `/governance/sandbox/status` |
| 插件节点 `plugin_node_router` | `plugin_sdk_enabled`（false） | GET `/plugin/nodes` · POST `/plugin/nodes/install` · DELETE `/plugin/nodes/{node_name}` |
| 节点贡献 `contribution_router` | `node_contribution_enabled`（false） | GET `/market/contributions` · POST `/market/contributions` · POST `/market/contributions/{contribution_id}/transition` · POST `/market/contributions/{contribution_id}/rescan` · GET `/market/contributions/{contribution_id}/history` |
| SSO `sso_router` | `sso_enabled`（false） | GET `/auth/sso/idps` · POST `/auth/sso/idps` · GET `/auth/sso/{idp_id}/initiate` · POST `/auth/sso/{idp_id}/callback` |
| 企业连接器 `enterprise_connector_router` | `enterprise_connector_enabled`（false） | GET `/connectors/enterprise` · POST `/connectors/enterprise` · PUT `/connectors/enterprise/{connector_id}/credentials` · POST `/connectors/enterprise/{connector_id}/test` · POST `/connectors/enterprise/{connector_id}/schema` |
| 数据源 `data_source_router` | — | POST `/data-source/test` |
| 评估 `evaluation_router` | `evaluation_enabled`（false） | POST `/governance/evaluation/scenario-sets` · GET `/governance/evaluation/scenario-sets` · GET `/governance/evaluation/scenario-sets/{set_id}` · DELETE `/governance/evaluation/scenario-sets/{set_id}` · POST `/governance/evaluation/tasks` · GET `/governance/evaluation/tasks` · GET `/governance/evaluation/tasks/{task_id}` · GET `/governance/evaluation/tasks/{task_id}/report` · POST `/governance/evaluation/baselines` · GET `/governance/evaluation/baselines` · GET `/governance/evaluation/status` |
| Agent 发现 `agent_discovery_router` | `agent_discovery_enabled`（false） | POST `/governance/agent-discovery/profiles` · GET `/governance/agent-discovery/profiles/{agent_id}` · DELETE `/governance/agent-discovery/profiles/{agent_id}` · GET `/governance/agent-discovery/profiles` · POST `/governance/agent-discovery/discover` |
| 触发器模板 `trigger_template_router` | `trigger_template_enabled`（false） | GET `/realtime-subscriptions/templates` |
| Agent 绩效 `agent_performance_router` | `agent_performance_enabled`（false） | GET `/governance/agent-performance` |
| 范例库 `example_library_router` | — | GET `/examples` · GET `/examples/{example_id}` |
| Agent 市场 `agent_market_router`（内部前缀 `/agent-market`） | `agent_market_enabled`（false） | GET `/agent-market/entries` · GET `/agent-market/entries/{entry_id}` · GET `/agent-market/categories` · POST `/agent-market/entries` · GET `/agent-market/templates` · GET `/agent-market/templates/{template_id}` |
| 市场开发流 `agent_market_dev_router`（内部前缀 `/agent-market`） | `agent_market_dev_enabled`（false） | GET `/agent-market/dev-templates` · GET `/agent-market/dev-templates/{scaffold_id}` · POST `/agent-market/dev-submissions` · GET `/agent-market/dev-submissions` · GET `/agent-market/dev-submissions/{submission_id}` · POST `/agent-market/dev-submissions/{submission_id}/transition` · POST `/agent-market/dev-submissions/{submission_id}/rescan` · GET `/agent-market/dev-submissions/{submission_id}/history` |
| Tool 市场 `tool_market_router`（内部前缀 `/tool-market`） | `tool_market_enabled`（false） | GET `/tool-market/tools` · GET `/tool-market/tools/{tool_identifier}` · GET `/tool-market/categories` · POST `/tool-market/tools` |
| 向量知识库管理 `vector_kb_management_router`（内部前缀 `/kb-management`） | `vector_kb_management_enabled`（false） | POST `/kb-management/knowledge-bases` · GET `/kb-management/knowledge-bases` · GET `/kb-management/knowledge-bases/{knowledge_base_id}` · PUT `/kb-management/knowledge-bases/{knowledge_base_id}` · DELETE `/kb-management/knowledge-bases/{knowledge_base_id}` · POST `/kb-management/knowledge-bases/{knowledge_base_id}/documents` · POST `/kb-management/knowledge-bases/{knowledge_base_id}/documents/{document_id}/confirm` · GET `/kb-management/knowledge-bases/{knowledge_base_id}/jobs/{job_id}` · GET `/kb-management/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunks` · POST `/kb-management/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunk-drafts` · POST `/kb-management/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunk-drafts/confirm` · GET `/kb-management/embedding-models` · GET `/kb-management/knowledge-bases/{knowledge_base_id}/diagnosis` |
| 文档标注 `vector_kb_annotation_router`（内部前缀 `/kb-management`） | `vector_kb_annotation_enabled`（false） | POST `/kb-management/knowledge-bases/{knowledge_base_id}/documents/{document_id}/annotate` · POST `.../lowercase` · POST `.../annotation-draft` · POST `.../annotation-draft/confirm` |
| JWT 签发 `auth_router` | `auth_token_issuance_enabled`（false） | POST `/auth/token`（**公开**：RBAC 中间件豁免；开关关闭时不注册） |
| 定时触发器管理 `trigger_schedule_router` | `trigger_schedule_api_enabled`（false） | GET `/trigger-schedules` · POST `/trigger-schedules/{schedule_id}:enable` · POST `/trigger-schedules/{schedule_id}:disable` · GET `/trigger-schedules/{schedule_id}/next-fire` |

> 说明：`governance_router` 族的 `/governance/auth/token` 不在中间件豁免清单内（需 Bearer）；豁免的是 `auth_router` 的 `/api/v1/auth/token`。

## 3. 错误响应口径

后端错误响应体实测共三种形态，前端 `ApiError`（`web/src/api/apiError.ts`）统一解析并按五层分层（transport / auth / tenant / business / validation）：

| 形态 | 出口 | 示例 |
| --- | --- | --- |
| 平台统一结构 `{code, message, details}` | 全局异常处理器（`DomainError` 族）：`WorkflowValidationError`→422、`InstanceStateConflictError`→409、`ResourceNotFoundError`→404、其余 `DomainError`→500；RBAC 中间件 401/403（如 `AUTH_TOKEN_MISSING`） | `{"code":"...", "message":"...", "details":{}}` |
| FastAPI 默认 `{detail}` | 请求校验失败（422）及个别 `HTTPException(detail=字符串)` | `{"detail":"..."}` |
| 市场族结构化 `{detail:{error_code, error_message}}` | 市场/治理族 `HTTPException(detail={"error_code":..., "error_message":...})`（节点贡献、沙箱、插件节点等） | `{"detail":{"error_code":"NODE_NOT_FOUND","error_message":"..."}}` |

状态码 → 前端分层映射：401/403→auth · 422→validation · 404/409/其余 4xx→business · 5xx→transport · `TENANT_` 前缀码→tenant；网络失败/超时→transport（`NETWORK_ERROR`/`TIMEOUT`）。

## 4. SSE 执行事件流

- 端点：`GET /api/v1/instances/{instance_id}/events/stream`（`text/event-stream`）。
- 前端消费：`web/src/execution-visualization/ExecutionEventStreamClient.tsx`——`EventSource` 订阅；断线自动重连（指数退避 1s~30s，`maxRetries` 默认 10，耗尽触发 `onRetryExhausted`）；重连后经 `GET /api/v1/instances/{instance_id}/events` 全量补偿刷新（仅非空令牌时携带 `Authorization`）。

## 5. 实例取消与结果（B2 嵌入 SDK 新增）

- `POST /api/v1/instances/{instance_id}/cancel`：请求取消执行中实例。404（实例不存在）；409 `INSTANCE_CANCEL_CONFLICT`（实例已终态，或无可取消任务——含 `waiting_review` 挂起态）；200 返回请求时刻快照 `{instance_id, definition_id, status, started_at}`。取消为竞态容忍语义：投递成功不代表终态为 `cancelled`，终态以 `GET /instances/{instance_id}` 轮询为准。
- `GET /api/v1/instances/{instance_id}/results`：查询实例执行结果。404（实例不存在）；非终态 200 `{instance_id, status, outputs: null}`（轮询友好，无 `finished_at` 键）；终态 200 `{instance_id, status, outputs, finished_at}`，`outputs` 为终态 `node_outputs` 快照（仅 `succeeded` 实例有值，其余终态为 `{}`）。
- 完整契约（SDK 9 方法映射、错误码表、SSE 帧格式、租户约定）：[sdk-contract.md](./sdk-contract.md)。

## 6. 治理用量查询（B7 W1a 真实化）

- `GET /governance/usage?tenant_id=<必填>&instance_id=<可选>`（`governance_router` 内部前缀 `/governance`）：聚合用量仓储返回真实统计，非 stub。
- 正常 200 形状（`available=true`）：

```json
{
  "tenant_id": "tenant-1",
  "instance_id": null,
  "available": true,
  "record_count": 12,
  "executions": 3,
  "node_records": 9,
  "total_prompt_tokens": 1250,
  "total_completion_tokens": 430,
  "total_tokens": 1680,
  "by_model": {"workflow_engine": 3, "qwen3.8-27b": 9}
}
```

- 口径：`executions`=执行级行数（`node_id="execution"`，行数=执行次数）；`node_records`=节点级行数（B7 W1b 节点级真实 token 计量行）；`by_model` 按 `model_identifier` 分组行数；`instance_id` 给定时按实例过滤。
- 降级 200 形状：用量仓储未注入（如独立工具进程）时 `{"tenant_id": ..., "instance_id": ..., "available": false}`，不报错。
- 详见 [metering-quota-guide.md](./metering-quota-guide.md)（执行级/节点级口径 + 对账）。
