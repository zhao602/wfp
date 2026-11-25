# SDK 契约冻结（docs/sdk-contract.md）

> 批次：roadmap B2 嵌入 SDK（roadmap §7-B2 / §10 前置「契约冻结」）
> 状态：冻结（2026-11-21）
> 适用：`wfp-sdk` Python 包 v0.1.x、`/api/v1` REST 面、错误码体系、YAML 校验契约、租户约定。
> 变更纪律：本文件冻结后，任一契约面（端点/错误码/租户约定）的破坏性变更须升
> `/api/v2` 或 SDK major 版本，并回写本文件与 roadmap。

## §1 端点契约（SDK 9 方法 → REST 映射）

前缀 `/api/v1`；请求/响应体均为 JSON（SSE 除外）；字段名 snake_case。

| # | SDK 方法 | HTTP | 路径 | 请求体 | 成功响应 | 失败响应 |
|---|---|---|---|---|---|---|
| 1 | `validate_definition(yaml_text)` | POST | `/workflows/validate` | `{yaml_text}` | 200 `{valid, issues[], definition_id}`（**恒 200**，非法定义不报错，看 `valid`） | 网络/5xx |
| 2 | `submit_definition(yaml_text)` | POST | `/workflows` | `{yaml_text}` | 200 `{definition_id, definition_version}` | 422（校验未过，§2） |
| 3 | `start_instance(definition_id, yaml_text, inputs=None, business_category=None)` | POST | `/instances` | `{definition_id, yaml_text, inputs?, definition_version?, trigger_type?}` | 200 `{instance_id, status: "running", started_at}` | 422（解析/编译失败）；500 |
| 4 | `get_instance(instance_id)` | GET | `/instances/{id}` | — | 200 `{instance_id, definition_id, status, started_at, finished_at?}` | 404（实例不存在） |
| 5 | `stream_events(instance_id)` | GET | `/instances/{id}/events/stream` | — | SSE 流：`data: {事件对象}\n\n` 帧 + 终态后 `data: {"event_type": "STREAM_END", ...}` 收尾帧 | 404 |
| 6 | `poll_events(instance_id, include_voided=False)` | GET | `/instances/{id}/events?include_voided=` | — | 200 `[事件对象]` | 404 |
| 7 | `cancel_instance(instance_id)` | POST | `/instances/{id}/cancel` | — | 200 `{instance_id, definition_id, status, started_at}`（取消已投递，终态由后台落定，以 #4 轮询为准） | 404；409 `INSTANCE_CANCEL_CONFLICT`（已终态或无可取消任务） |
| 8 | `get_results(instance_id)` | GET | `/instances/{id}/results` | — | 200 `{instance_id, status, outputs, finished_at?}`；非终态 `outputs: null`（轮询友好）；终态 `outputs` = 终态共享状态 `node_outputs`（仅 succeeded 有值，其余终态 `{}`） | 404 |
| 9 | `wait_for_completion(instance_id, timeout, poll_interval)` | 组合 | #4 轮询 | — | 终态 `InstanceInfo` | 超时 → SDK 本地 `WfpTimeoutError`（合成码 `CLIENT_TIMEOUT`，不发 HTTP） |

**映射约定：**

- `business_category` 参数经 `trigger_type` 请求字段传递（路由
  `_resolve_business_category` 一级回退链：`trigger_type` → TRIGGER 节点配置 →
  default；SDK 侧语义即「业务类别」，字段名沿用后端通道名以避免后端改动）。
- 事件对象（#5/#6 共用）：`{event_id, instance_id, event_type, node_id, timestamp,
  payload}`；`event_type` 词汇：`NODE_STARTED` / `NODE_COMPLETED` / `NODE_FAILED` /
  `INSTANCE_STARTED` / `INSTANCE_COMPLETED` / `INSTANCE_FAILED` /
  `INSTANCE_SUSPENDED` / `INSTANCE_CANCELLED`（B2 新增）/ `STREAM_END`（仅 SSE 收尾帧）。
- 实例状态词汇：`pending` / `running` / `succeeded` / `failed` / `waiting_review` /
  `rolled_back` / `cancelled`（B2 新增）；**终态集合** =
  `{"succeeded", "failed", "rolled_back", "cancelled"}`（#8/#9 与 SSE 收尾共用口径）。
- 竞态容忍（#7）：校验通过后任务恰先完成时 `task.cancel()` 为 no-op，响应返回
  请求时刻快照；客户端以 #4 轮询到的终态为准（取消成功 → `cancelled`；
  恰好完成 → 实际终态）。

## §2 错误契约

### 2.1 统一错误体（目标形态）

```json
{"code": "<SCREAMING_SNAKE_CASE>", "message": "<人类可读>", "details": {}}
```

### 2.2 wire 形态（三种并存，SDK 必须全兼容解析）

| 形态 | 来源 | 示例 |
|---|---|---|
| W1 裸 detail 字符串 | FastAPI `HTTPException(detail=str)` | `{"detail": "实例 xxx 不存在"}` |
| W2 detail 内嵌对象 | FastAPI `HTTPException(detail=dict)` / DomainError `.to_dict()` 经 detail | `{"detail": {"code": "WF_VALIDATION_FAILED", "message": "...", "details": {...}}}` |
| W3 顶层对象 | 全局异常处理器（application_factory 422/409/404/500） | `{"code": "...", "message": "...", "details": {...}}` |

SDK 解析顺序：顶层 `code` → `detail.code` → `detail`（字符串）→ 兜底合成码。

### 2.3 HTTP 状态 → SDK 异常映射

| HTTP | SDK 异常 | 典型码 |
|---|---|---|
| 422 | `WfpValidationError` | `WF_VALIDATION_FAILED`（details.issues 携同构问题清单） |
| 409 | `WfpStateConflictError` | `INSTANCE_CANCEL_CONFLICT`（B2 新增）、`REPLAY_ALREADY_IN_PROGRESS` |
| 404 | `WfpNotFoundError` | `WF_NOT_FOUND`、`INSTANCE_NOT_FOUND`（W1 形态时码缺失，SDK 兜底 `INSTANCE_NOT_FOUND`） |
| 401 / 403 | `WfpAuthError` | `AUTH_TOKEN_MISSING`、`AUTH_TOKEN_INVALID`、`AUTH_TOKEN_EXPIRED`、`AUTH_DENIED` |
| 其余 4xx/5xx | `WfpError` | 原码透传 |
| 网络异常/超时 | `WfpTransportError` | 合成码 `TRANSPORT_ERROR`（不发 HTTP） |
| wait 超时 | `WfpTimeoutError` | 合成码 `CLIENT_TIMEOUT`（不发 HTTP） |

### 2.4 码表纪律

- 平台错误码常量名即值（`errors.py`，SCREAMING_SNAKE_CASE）；
- SDK vendored 子集与平台逐字一致；未知响应码 → `WfpError(code=原样字符串)`
  透传（isomorphic passthrough，不丢失信息）；
- 合成码仅两条（`TRANSPORT_ERROR` / `CLIENT_TIMEOUT`），仅由 SDK 本地产生，
  永不与平台码冲突（平台无 `TRANSPORT_*`/`CLIENT_*` 前缀）。

### 2.5 B2 新增码

| 码 | 场景 | HTTP |
|---|---|---|
| `INSTANCE_CANCEL_CONFLICT` | 取消请求被拒：实例已终态，或无可取消的运行任务（含 waiting_review 挂起态） | 409 |

## §3 租户约定（§10 冻结项）

1. **租户身份随 Bearer 令牌**：`Authorization: Bearer <token>` → RBAC 拦截器链
   解析 → `request.state.tenant_id`（rbac_authorization_middleware.py:150）。
2. **无租户请求头**：不设 `X-Tenant-ID` 类通道；SDK 不提供租户参数。
3. **降级模式**（治理链未启用，即 local profile）：中间件放行，路由层
   `tenant_id=None` → 仓储回退 `settings.default_tenant_id`（`tenant_default`）。
4. 令牌签发：`POST /api/v1/auth/token`（`auth_token_issuance_enabled=true` 时；
   否则调用方持预签发令牌）。SDK 构造参数 `token=` 接受预签发字符串。

## §4 YAML 契约

- `spec_version`：`1.0` / `1.1`；
- 校验问题形态（`ValidationIssue`，domain/models/common.py:61）：
  `{code, severity, message, node_id?, edge_id?, location?}`，
  `severity ∈ {error, warning, info}`，`location = {line, column}`（1 起算）；
- 校验报告形态（`ValidationReport`，common.py:75）：`{valid, issues[], definition_id?}`；
- CLI 校验退出码（`wfp validate`）：0=通过 / 1=校验失败 / 2=IO·解析错误
  （SDK `validate_definition` 恒返报告对象，不映射退出码）。

## §5 版本策略

- `/api/v1` 前缀冻结；新增端点进 v1（非破坏性），字段只增不删不改名；
- SDK 语义化版本（0.1.0 起）；破坏性契约变更 → `/api/v2` 或 SDK major；
- SSE 帧格式（`data: <json>\n\n` + `STREAM_END` 收尾）冻结。

### 5.1 契约 snapshot 门禁（B14 接入，2026-11-25）

上述纪律的机器执行：契约面以字节级金档冻结，本地 CI 与 pytest 门禁逐字节对照。

- **金档**（`tests/fixtures/contracts/`）：
  - `openapi_v1.json`——`create_app()` 全量 OpenAPI 3.1 schema（126 路径）；
  - `contract_surfaces.json`——运行时提取的三个契约面：SDK 端点面
    （`wfp_sdk.client`，8 端点）、web 客户端方法面（23 方法/端点）、
    `apiTypes.ts` 导出面（25 声明 + 31 re-export）。
- **门禁脚本** `scripts/check_contract_snapshot.py`：
  - 钉扎 21 路由旗标全 ON + RBAC/TENANT off + `STORAGE_BACKEND=local_file` +
    LLM 占位，消除环境漂移；差异即 exit 1；
  - `--regen` 重生成金档——**仅**在走完破坏性变更流程后使用（见下）。
- **接线**：`run_local_ci.py` 步 2.5（引擎隔离后、pytest 前）；pytest 侧
  `tests/unit/test_contract_snapshot_gate.py` 薄包装。
- **破坏性变更流程**：升 `/api/v2` 或 SDK major → 回写本文件 §1/§2 →
  金档 `--regen` 并随变更提交 → roadmap 回写；跳步即门禁红。

## §6 非目标记录（当前版本明确不做）

1. 异步客户端（`AsyncWorkflowClient`）——出口移交；
2. SDK 的 review(HITL) / rollback / replay 方法——B3 相邻域；
3. 实例持久化（`InstanceRegistry` 内存态，server 重启即失；SDK 使用契约：
   实例生命周期须在 server 存续期内完成）；
4. token 签发 helper / 刷新逻辑（SDK 接受预签发 token）。
