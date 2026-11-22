# 计量配额闭环指南（roadmap B4）

> 版本：2026-11-22（roadmap_b4_metering_quota；同日 B7 收割批更新：§2.1 节点级记账 / §4 对账口径 / §5 边界刷新）
> 范围：执行级用量记账、租户并发实例配额（429 契约）、用量对账脚本。
> 相关：`docs/config.md`（metering_* / tenant_* 配置键）、`docs/governance_metering_billing.md`（计量计费组件全景）。

## 1. 计量配额闭环总览

B4 接通「计量 → 配额 → 对账」闭环三环节：

| 环节 | 组件 | 位置 |
| --- | --- | --- |
| 执行级记账 | `ExecutionUsageRecorder` | `governance/metering/usage_recorder.py` |
| 并发配额闸门 | `TenantQuotaGate` | `governance/tenant/quota_gate.py` |
| 用量对账 | `reconcile.py` + `scripts/reconcile_usage.py` | `governance/metering/` + `scripts/` |
| 429 契约 | `TenantQuotaExceededError` | `domain/errors.py` + `api/application_factory.py` |

## 2. 执行级记账（用量行数 = 执行次数）

**时点**：每次 `run_and_record` 调用（任意触发源：手动启动 / 恢复 / 回滚重放 /
审批续跑 / cron 定时）在执行启动处记 1 条用量记录——与运行日志 `begin_run`
同一 invocation 构造性配对。超配额拒绝路径不进入执行，不记账。

**记录口径**（`TokenUsageRecord`）：

| 字段 | 值 | 说明 |
| --- | --- | --- |
| `node_id` | `"execution"` | 执行级固定标识（区别于节点级计量） |
| `model_identifier` | `"workflow_engine"` | 执行级固定模型标识 |
| `prompt/completion/total_tokens` | `0` | 引擎不产出 token 用量，占位口径 |
| `record_id` | `usage_{uuid4 前 16 位}` | 唯一标识 |
| `tenant_id` | 实际租户或 `log_tenant_default` | 缺失兜底（cron fire 无租户场景） |
| `instance_id` / `workflow_definition_id` | 实例标识 / 定义标识 | 对账关联键 |

**写入链路**：`ExecutionUsageRecorder.record_execution_start` →
`UsageBufferQueue.enqueue`（默认每 100 条或 5 秒批量刷新）→
`_flush_to_repository`（**按租户分组**逐租户 `append_batch`）→ 双后端用量仓储
（`storage_backend=local_file`：`<local_storage_root>/governance/token_usage/`；
`postgres`：`governance_token_usage` 表，RLS 隔离 + `ON CONFLICT DO NOTHING` 幂等）。

**装配开关**：`metering_enabled=True`（默认）时 application_factory 装配
recorder + 缓冲队列（两段构造），startup/shutdown 挂接定时刷新。
关闭时无用量记录（对账必差异，见 §4 前提声明）。

### 2.1 节点级记账（B7 W1b，真实 token 数）

**时点**：节点执行成功路径（`scheduler._run_node_lifecycle` 成功分支，
end_node 后、命名输出写前）——执行器透传 provider usage 时记账，
无 usage / metering 未装配静默跳过（不影响执行主流程）。

**usage 来源**：`HttpLlmClient.infer`（非流式，生产路径）提取 OpenAI
兼容响应体顶层 `usage`（`prompt_tokens` / `completion_tokens` /
`total_tokens`）→ `InferenceResult.usage` → llm/agent 执行器写
`metadata["token_usage"]`（agent 取产出最终输出那次推理，重试成功
即重试结果）。

**记录口径**（与执行级同行 `TokenUsageRecord`，区别字段）：

| 字段 | 值 | 说明 |
| --- | --- | --- |
| `node_id` | 真实节点 ID | 区别于执行级固定 `"execution"` |
| `agent_node_id` | agent 节点 ID 或 `null` | 仅 `NodeType.AGENT` 节点填 |
| `model_identifier` | provider 模型标识 | 执行级为 `workflow_engine` |
| `prompt/completion/total_tokens` | provider 真实 token 数 | 异形条目归一化（数字字符串取整 / 缺失与 bool 计 0） |
| `workflow_definition_id` | `compiled.definition_id` | 执行级同字段 |

**写入链路**：`ExecutionUsageRecorder.record_node_usage` → 同一
`UsageBufferQueue` → 按租户分组 `append_batch` → 双后端用量仓储
（与执行级同队列、同落盘，无新装配开关）。

**已知缺口**（移交池）：`infer_stream` 流式帧内 usage 提取未做
（既有帧解析不动）；agent ReAct 循环中间推理 usage 不累加
（只记产出最终输出那次）。

## 3. 429 超配额契约（拒绝未执行）

**并发口径（D1）**：租户「并发活跃实例数」= 内存注册表中该租户非终态
（pending/running/waiting_review 等，终态 = succeeded/failed/rolled_back/cancelled）
的实例计数。终态释放配额槽位；进程重启后内存注册表清零即并发归零。

**闸门时机**：`POST /api/v1/instances` 在租户快照后、实例注册前调用
`TenantQuotaGate.ensure_capacity`。并发数达到 `max_instances` 上限时抛
`TenantQuotaExceededError`，映射 **HTTP 429**：

```json
{
  "code": "TENANT_QUOTA_EXCEEDED",
  "message": "租户并发实例配额超限: 100/100",
  "details": {
    "tenant_id": "tenant_default",
    "active_instances": 100,
    "max_instances": 100
  }
}
```

**拒绝零副作用**：实例不注册、不解析、不编译、不执行（闸门先于全部副作用步骤）。

**原子性**：配额查询为唯一 await 点，计数→校验→拒绝→注册为同步块，
事件循环单线程内无并发穿插——超配额 100% 拒绝保证。

**配额源三级回退**：租户仓储 `get_quota`（`governance_tenant_quota` 落库值）
→ settings 默认键（`tenant_default_quota_max_instances=100` /
`tenant_default_quota_max_workflows=1000`）→ 内置兜底 100/1000。

**两命名空间说明**：错误码 `TENANT_QUOTA_EXCEEDED`（`domain/errors.py`，
429 契约）与事件类型 `ExecutionEventType.TENANT_QUOTA_EXCEEDED`
（`domain/models/enums.py`）分属错误码/事件类型命名空间，语义关联互不冲突。

**豁免边界**：`tenant_id` 缺失（治理降级 / cron 无租户上下文）不闸门；
存量实例的恢复/重放/审批续跑不重复闸门（并发计数按内存注册表现值）。

## 4. 用量对账（对账零差异）

**双源独立持久**：

| 侧 | 数据源 | 采集口径 |
| --- | --- | --- |
| 用量侧 | 用量仓储 + 未 flush 缓冲（`metering_buffer_persist_path/buffer.json`） | `node_id="execution"` 的 `(tenant_id, instance_id)` 全集（B7 W1b 起节点级行显式过滤，不计执行次数） |
| runlog 侧 | `<log_dir>/**/*.jsonl` | `record_type=workflow_run` 且 `status=running` 的 begin 记录 `(tenant_id, run_id)` |

两侧同 invocation 构造（记账与 begin_run 同点），构造性零差异。

**用法**：

```powershell
# 缺省后端/路径取 settings（storage_backend / local_storage_root / log_dir）
python scripts/reconcile_usage.py

# 显式指定后端与路径
python scripts/reconcile_usage.py --storage-backend local_file --storage-root .data --log-dir logs

# 机器可读输出
python scripts/reconcile_usage.py --json
```

**退出码**：`0` 全租户零差异；`1` 存在差异（输出 `missing_in_usage` /
`missing_in_runlog` 清单）；`2` 环境异常（仓储不可达等）。

**前提声明**：`metering_enabled=False` 时用量侧为空，对账必差异——对账前
确认计量开启。postgres 模式走仓储 `_admin_connection` 旁路
（`app.bypass_rls='on'` 运维通道）跨租户管理读。

## 5. 已知边界与非目标

- **cron 无租户**：cron fire 路径无租户上下文——闸门跳过、记账落
  `log_tenant_default` 兜底（与 run-log 兜底同源，对账自洽）；
- **兜底口径分离（B7 W2b 注记）**：计量域兜底 `log_tenant_default`
  （默认 `default`，独立配置键）与治理域默认租户 `DEFAULT_TENANT_ID`
  （B7 W2b 起统一为 `tenant_default`，domain_event.py 单源）分属两域
  口径——计量/run-log 对账域保持 `log_tenant_default`，治理域
  （租户创建/领域事件/调度器回退）统一 `tenant_default`；
- **节点级 token 计量**：B7 W1b 已接线（见 §2.1，真实 token 数 +
  调度器成功路径钩子）；流式帧 usage 提取与 ReAct 中间推理累加
  仍为缺口（移交池）；
- **其他配额维度**：`max_workflows` / `max_tokens_per_day` / `max_storage_gb`
  拦截非本批范围（本批仅并发实例维度）；
- **治理中间件层计量拦截器**：HTTP 请求层无执行上下文，维持现状（生产
  no-op），本批零改动；
- **用量查询端点**：B7 W1a 已真实化——`GET /governance/usage` 按
  `tenant_id`（必填查询参数）聚合用量仓储：执行次数
  （`node_id="execution"` 行数）、节点级记录数、token 总量与
  `by_model` 分组；仓储未注入时降级 `available=false`；
- **双队列共享 persist 路径**：治理链内既有 `UsageBufferQueue`（无生产者，
  生产 no-op）与本批 recorder 队列共享 `metering_buffer_persist_path`——
  链内队列永不入队无冲突；persist 文件恢复幂等（`model_validate` 容错）。

## 6. 运维手册速查

| 场景 | 操作 |
| --- | --- |
| 调整租户并发配额 | 更新 `governance_tenant_quota` 落库值（或 local_file 配额文件） |
| 巡检用量一致性 | `python scripts/reconcile_usage.py`（exit 0 即零差异） |
| 排查 429 | 响应 `details.active_instances/max_instances` 即当前并发/上限 |
| 关闭计量 | `metering_enabled=false`（注意：对账必差异，用量不再记录） |
