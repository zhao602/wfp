# 平台配置参考

> 全量配置实测口径：由 `src/orchestration_platform/configuration/settings.py` 机械生成（字段声明序），共 219 项（布尔开关 51 + 非开关 168）。

## 1. 配置机制

- 配置类：`orchestration_platform.configuration.settings.Settings`（pydantic-settings），经 `get_settings()` 以 `lru_cache` 提供进程内唯一实例。
- 覆盖方式：任一字段可经**同名环境变量（大写、大小写不敏感）**或 `.env` 文件覆盖（`env_file=".env"`，UTF-8，忽略未声明变量）。
- 生产基线：`ENVIRONMENT=production` 时弱密钥 fail-fast 拒绝启动；示例值均为占位符（如 `change-me-please`），部署前必须替换。
- 快速模板：复制 `.env.example` 为 `.env` 后按需覆盖；布尔开关接受 `true/false`（大小写不敏感）。

## 2. 布尔开关矩阵（51 项）

### MCP 工具服务（仅客户端侧）

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `mcp_server_enabled` | `MCP_SERVER_ENABLED` | `True` | MCP 服务端是否启用 |

### Agent 市场与 Tool 市场（agent_tool_marketplace T11.1，仅追加）

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `agent_market_enabled` | `AGENT_MARKET_ENABLED` | `False` | 是否启用 Agent 市场路由（关闭时不注册，访问返回 404） |
| `tool_market_enabled` | `TOOL_MARKET_ENABLED` | `False` | 是否启用 Tool 市场路由（关闭时不注册，访问返回 404） |

### Agent 市场用户自主开发（agent_market_user_development T4.1，仅追加）

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `agent_market_dev_enabled` | `AGENT_MARKET_DEV_ENABLED` | `False` | 是否启用 Agent 市场用户自主开发路由（关闭时不注册，访问返回 404） |

### 可观测性

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `log_rotation_enabled` | `LOG_ROTATION_ENABLED` | `True` | 是否启用运行日志轮转 |
| `log_redaction_enabled` | `LOG_REDACTION_ENABLED` | `True` | 运行日志脱敏开关（关闭时仍保留字段名黑名单兜底，红线不可关） |
| `log_async_enabled` | `LOG_ASYNC_ENABLED` | `True` | 运行日志异步队列开关 |

### 第二阶段新增配置

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `scheduler_batch_mode` | `SCHEDULER_BATCH_MODE` | `True` | 是否启用调度器批处理 |
| `checkpoint_compression_enabled` | `CHECKPOINT_COMPRESSION_ENABLED` | `True` | 是否启用检查点压缩 |

### 第三阶段治理配置

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `rbac_enabled` | `RBAC_ENABLED` | `True` | 是否启用 RBAC 鉴权 |
| `tenant_isolation_enabled` | `TENANT_ISOLATION_ENABLED` | `True` | 是否启用租户隔离 |
| `default_tenant_id` | `DEFAULT_TENANT_ID` | `tenant_default` | 默认租户标识（业务仓储 tenant_id 缺省兜底，与 v1_2 列默认值/种子租户一致，roadmap B1） |
| `metering_enabled` | `METERING_ENABLED` | `True` | 是否启用 Token 计量 |
| `budget_fail_closed` | `BUDGET_FAIL_CLOSED` | `True` | 预算超限时是否拒绝（True）或仅告警（False） |
| `audit_enabled` | `AUDIT_ENABLED` | `True` | 是否启用审计日志 |
| `audit_content_hash_enabled` | `AUDIT_CONTENT_HASH_ENABLED` | `False` | 是否启用审计条目字段级内容哈希（input_hash/output_hash） |
| `structural_lossless_pruning_enabled` | `STRUCTURAL_LOSSLESS_PRUNING_ENABLED` | `False` | 是否启用结构化无损裁剪策略（关闭时走既有滑动窗口兜底） |
| `supervisor_orchestration_enabled` | `SUPERVISOR_ORCHESTRATION_ENABLED` | `False` | 是否启用监督者编排器（关闭时 SUPERVISOR 节点类型按未知拦截） |
| `trigger_template_enabled` | `TRIGGER_TEMPLATE_ENABLED` | `False` | 是否启用触发器模板（关闭时模板清单路由不注册、主循环不评估模板） |
| `agent_performance_enabled` | `AGENT_PERFORMANCE_ENABLED` | `False` | 是否启用 Agent 绩效记录与查询（关闭时记录器不挂载、路由不注册） |
| `global_scheduler_enabled` | `GLOBAL_SCHEDULER_ENABLED` | `True` | 是否启用全局调度器 |
| `scheduler_preemption_enabled` | `SCHEDULER_PREEMPTION_ENABLED` | `True` | 是否启用抢占调度 |

### 第三阶段偏差修正配置

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `nl_orchestration_enabled` | `NL_ORCHESTRATION_ENABLED` | `False` | 是否启用自然语言编排入口 |
| `abac_enabled` | `ABAC_ENABLED` | `False` | 是否启用 ABAC 策略引擎 |
| `tombstone_enabled` | `TOMBSTONE_ENABLED` | `True` | 是否启用墓碑删除（关闭则审计删除走物理删除） |
| `redis_enabled` | `REDIS_ENABLED` | `False` | 是否启用 Redis 缓存 |
| `kafka_enabled` | `KAFKA_ENABLED` | `False` | 是否启用 Kafka 事件总线 |
| `event_bus_publish_enabled` | `EVENT_BUS_PUBLISH_ENABLED` | `False` | 是否启用事件总线领域事件发布 |
| `audit_event_subscription_enabled` | `AUDIT_EVENT_SUBSCRIPTION_ENABLED` | `False` | 是否启用审计事件订阅落盘 |
| `schedule_scanner_enabled` | `SCHEDULE_SCANNER_ENABLED` | `False` | 是否启用 Cron 定时触发扫描器 |
| `trigger_schedule_api_enabled` | `TRIGGER_SCHEDULE_API_ENABLED` | `False` | 是否注册定时触发器管理路由 |
| `event_export_enabled` | `EVENT_EXPORT_ENABLED` | `False` | 是否启用事件出口 Webhook 投递装配（roadmap B3；关闭时零装配，基线零变化） |
| `event_export_api_enabled` | `EVENT_EXPORT_API_ENABLED` | `False` | 是否注册事件出口管理路由（订阅/死信/SSE；关闭时访问 404） |
| `sandbox_enabled` | `SANDBOX_ENABLED` | `False` | 是否启用 Agent 执行沙箱 |
| `sandbox_network_default_deny` | `SANDBOX_NETWORK_DEFAULT_DENY` | `True` | 网络默认拒绝 |
| `sandbox_subprocess_mode` | `SANDBOX_SUBPROCESS_MODE` | `False` | 是否启用子进程隔离模式 |
| `llm_node_enabled` | `LLM_NODE_ENABLED` | `False` | 是否启用大模型节点（默认 False：关闭时返回 LLM_NODE_DISABLED） |
| `code_node_enabled` | `CODE_NODE_ENABLED` | `False` | 是否启用代码节点（默认 False：关闭时返回 CODE_NODE_DISABLED） |
| `plugin_sdk_enabled` | `PLUGIN_SDK_ENABLED` | `False` | 是否启用插件节点 SDK |
| `node_contribution_enabled` | `NODE_CONTRIBUTION_ENABLED` | `False` | 是否启用节点市场第三方贡献 |
| `sso_enabled` | `SSO_ENABLED` | `False` | 是否启用 SSO 企业身份集成 |
| `enterprise_connector_enabled` | `ENTERPRISE_CONNECTOR_ENABLED` | `False` | 是否启用企业连接器 |
| `evaluation_enabled` | `EVALUATION_ENABLED` | `False` | 是否启用评估框架 |
| `evaluation_llm_scoring_enabled` | `EVALUATION_LLM_SCORING_ENABLED` | `False` | 是否启用 LLM 评分（默认纯规则） |
| `agent_discovery_enabled` | `AGENT_DISCOVERY_ENABLED` | `False` | 是否启用语义索引 Agent 发现 |
| `local_vector_database_enabled` | `LOCAL_VECTOR_DATABASE_ENABLED` | `False` | 是否启用本地向量数据库（默认 False：关闭时既有向量知识库节点行为完全不变） |
| `vector_real_embedding_enabled` | `VECTOR_REAL_EMBEDDING_ENABLED` | `False` | 是否强制真实 embedding 写入（默认 False：关闭时写入行为与现状逐字节等价；开启时仅 hash_fallback 可用的新写入以可辨识错误拒绝，不伪造语义） |
| `vector_kb_management_enabled` | `VECTOR_KB_MANAGEMENT_ENABLED` | `False` | 是否启用向量知识库管理（默认 False：关闭时 /api/v1/kb-management/** 返回 404，既有行为完全不变） |
| `vector_kb_annotation_enabled` | `VECTOR_KB_ANNOTATION_ENABLED` | `False` | 是否启用向量知识库文档标注能力（默认 False：关闭时标注端点 404、既有行为零变化） |
| `data_transform_custom_code_enabled` | `DATA_TRANSFORM_CUSTOM_CODE_ENABLED` | `False` | 是否启用数据转换自定义脚本能力（仅登记与静态校验，运行期不执行；默认 False） |

### 运行环境与安全强化（arch_review_v2_tasks G1/G2/G3，仅追加）

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `auth_token_issuance_enabled` | `AUTH_TOKEN_ISSUANCE_ENABLED` | `False` | 是否启用 JWT 签发端点（默认 False：关闭时不注册，端点 404） |
| `rbac_bootstrap_enabled` | `RBAC_BOOTSTRAP_ENABLED` | `False` | 是否启用 RBAC 启动期幂等初始化（默认租户/内置角色/初始管理员绑定；默认 False 不执行） |

### G6 实例级步数护栏（arch_review_v2 3.6，CHG-01/04/08；卡 A2；DC-10）

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `workflow_step_guard_enabled` | `WORKFLOW_STEP_GUARD_ENABLED` | `False` | 是否启用实例级步数护栏（默认 False：关闭时行为与基线完全一致，HC-07） |

## 3. 非开关配置项默认值（167 项）

### API 服务

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `api_host` | `API_HOST` | `0.0.0.0` | API 监听地址 |
| `api_port` | `API_PORT` | `8000` | API 监听端口（可配置） |
| `api_access_token` | `API_ACCESS_TOKEN` | `change-me-please` | 接口访问令牌（Bearer） |

### 存储后端

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `storage_backend` | `STORAGE_BACKEND` | `local_file` | 存储后端实现选择 |
| `local_storage_root` | `LOCAL_STORAGE_ROOT` | `./.data` | 本地文件存储根目录 |
| `postgres_host` | `POSTGRES_HOST` | `localhost` | PostgreSQL 主机 |
| `postgres_port` | `POSTGRES_PORT` | `5432` | PostgreSQL 端口 |
| `postgres_db` | `POSTGRES_DB` | `orchestration_platform` | PostgreSQL 数据库名 |
| `postgres_user` | `POSTGRES_USER` | `postgres` | PostgreSQL 用户名 |
| `postgres_password` | `POSTGRES_PASSWORD` | `change-me-please` | PostgreSQL 密码 |

### 工作流定义约束

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `workflow_max_nodes` | `WORKFLOW_MAX_NODES` | `50` | 单定义节点数量上限（第一阶段 50） |

### 引擎调度约束

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `node_default_timeout_seconds` | `NODE_DEFAULT_TIMEOUT_SECONDS` | `60` | 节点默认执行超时（秒） |
| `loop_max_iterations` | `LOOP_MAX_ITERATIONS` | `10` | 循环最大迭代次数（防死循环） |
| `checkpoint_interval_seconds` | `CHECKPOINT_INTERVAL_SECONDS` | `30` | 长耗时节点中间检查点间隔（秒） |
| `review_timeout_seconds` | `REVIEW_TIMEOUT_SECONDS` | `3600` | 人工审核超时（秒） |
| `instance_timeout_seconds` | `INSTANCE_TIMEOUT_SECONDS` | `1800` | 实例级超时（秒） |
| `instance_max_concurrency` | `INSTANCE_MAX_CONCURRENCY` | `20` | 实例最大并发数 |

### LangGraph 适配层

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `langgraph_checkpointer_mode` | `LANGGRAPH_CHECKPOINTER_MODE` | `custom` | 检查点模式：custom 由平台仓储持久化 |
| `langgraph_runtime_enabled` | `LANGGRAPH_RUNTIME_ENABLED` | `False` | LangGraph 第二运行时开关（默认关闭走内置引擎；仅作用于容器 langgraph_engine_factory 与等价矩阵 harness，生产 start_instance 链路零改；开启且依赖缺失时降级内置引擎并告警） |

### MCP 工具服务（仅客户端侧）

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `mcp_server_transport` | `MCP_SERVER_TRANSPORT` | `http` | MCP 传输方式 |
| `mcp_server_url` | `MCP_SERVER_URL` | `http://localhost:9000/mcp` | MCP HTTP 端点 |
| `mcp_server_command` | `MCP_SERVER_COMMAND` | `` | MCP stdio 启动命令 |
| `mcp_server_timeout_seconds` | `MCP_SERVER_TIMEOUT_SECONDS` | `30` | MCP 调用默认超时（秒） |
| `mcp_connection_pool_max` | `MCP_CONNECTION_POOL_MAX` | `10` | MCP 连接池最大连接数 |
| `mcp_connection_retry_max` | `MCP_CONNECTION_RETRY_MAX` | `3` | MCP 连接失败最大重试次数 |
| `mcp_tool_cache_ttl_seconds` | `MCP_TOOL_CACHE_TTL_SECONDS` | `300` | MCP 工具发现缓存 TTL（秒） |

### LLM 主模型

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `llm_primary_base_url` | `LLM_PRIMARY_BASE_URL` | `` | 主模型服务地址 |
| `llm_primary_api_key` | `LLM_PRIMARY_API_KEY` | `` | 主模型密钥 |
| `llm_primary_model` | `LLM_PRIMARY_MODEL` | `` | 主模型标识 |
| `llm_primary_timeout_seconds` | `LLM_PRIMARY_TIMEOUT_SECONDS` | `60` | 主模型超时（秒） |

### LLM 备用模型

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `llm_fallback_base_url` | `LLM_FALLBACK_BASE_URL` | `` | 备用模型服务地址 |
| `llm_fallback_api_key` | `LLM_FALLBACK_API_KEY` | `` | 备用模型密钥 |
| `llm_fallback_model` | `LLM_FALLBACK_MODEL` | `` | 备用模型标识 |
| `llm_fallback_timeout_seconds` | `LLM_FALLBACK_TIMEOUT_SECONDS` | `60` | 备用模型超时（秒） |

### LLM 推理重试（B2-C4）

开关默认关闭（`False`）：关闭时推理行为与现状逐字节等价；开启后仅对超时/连接失败等幂等失败自动重试（指数退避 + 抖动），非 200 响应与其余异常不重试，重试耗尽按既有错误语义返回。

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `llm_retry_enabled` | `LLM_RETRY_ENABLED` | `False` | LLM 推理幂等失败自动重试开关（仅超时/连接失败重试） |
| `llm_retry_max_attempts` | `LLM_RETRY_MAX_ATTEMPTS` | `3` | LLM 推理重试总尝试次数上限（含首次） |
| `llm_retry_backoff_base_seconds` | `LLM_RETRY_BACKOFF_BASE_SECONDS` | `0.5` | LLM 推理重试指数退避基数（秒，退避=base*2^n+jitter） |

### Agent 节点约束

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `agent_max_tool_rounds` | `AGENT_MAX_TOOL_ROUNDS` | `5` | 单次执行内工具调用最大轮次 |
| `agent_output_max_retries` | `AGENT_OUTPUT_MAX_RETRIES` | `2` | 输出结构不合规时最大重生成次数 |

### Agent 市场与 Tool 市场（agent_tool_marketplace T11.1，仅追加）

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `agent_market_storage_root` | `AGENT_MARKET_STORAGE_ROOT` | `./.data/agent_market` | Agent 市场条目本地文件存储根目录 |
| `tool_market_storage_root` | `TOOL_MARKET_STORAGE_ROOT` | `./.data/tool_market` | Tool 市场条目本地文件存储根目录 |

### Agent 市场用户自主开发（agent_market_user_development T4.1，仅追加）

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `agent_market_dev_storage_root` | `AGENT_MARKET_DEV_STORAGE_ROOT` | `./.data/agent_market_dev` | 用户开发提交本地文件存储根目录 |
| `agent_market_dev_max_package_mb` | `AGENT_MARKET_DEV_MAX_PACKAGE_MB` | `10` | 单个提交包大小上限（MB） |
| `agent_market_dev_max_files` | `AGENT_MARKET_DEV_MAX_FILES` | `200` | 单个提交包文件数上限 |
| `agent_market_dev_max_file_kb` | `AGENT_MARKET_DEV_MAX_FILE_KB` | `2048` | 提交包内单文件大小上限（KB） |
| `agent_market_dev_max_manifest_kb` | `AGENT_MARKET_DEV_MAX_MANIFEST_KB` | `64` | manifest.yaml 大小上限（KB） |
| `agent_market_dev_max_unpacked_mb` | `AGENT_MARKET_DEV_MAX_UNPACKED_MB` | `40` | 解压后总大小上限（MB） |

### 数据源

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `data_source_connection_string` | `DATA_SOURCE_CONNECTION_STRING` | `` | 数据源只读连接串 |

### 可观测性

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `log_level` | `LOG_LEVEL` | `INFO` | 日志级别 |
| `log_dir` | `LOG_DIR` | `./logs` | 运行日志根目录 |
| `log_rotation_when` | `LOG_ROTATION_WHEN` | `midnight` | 运行日志按时间轮转的触发时点 |
| `log_rotation_max_bytes` | `LOG_ROTATION_MAX_BYTES` | `52428800` | 运行日志单文件大小上限（字节，≥1MB） |
| `log_retention_days` | `LOG_RETENTION_DAYS` | `14` | 运行日志保留期（天，0=不清理） |
| `log_max_payload_bytes` | `LOG_MAX_PAYLOAD_BYTES` | `65536` | 运行日志单字段规模上限（字节） |
| `log_queue_max_size` | `LOG_QUEUE_MAX_SIZE` | `10000` | 运行日志异步队列上限 |
| `log_tenant_default` | `LOG_TENANT_DEFAULT` | `default` | 运行日志/计量租户缺失兜底目录名（口径分离注记 B7 W2b：本键为计量对账域兜底，独立于治理域默认租户 `DEFAULT_TENANT_ID`=`tenant_default`，两域口径互不复用） |

### 第二阶段新增配置

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `parallel_branch_max` | `PARALLEL_BRANCH_MAX` | `10` | 并行分支最大并行度 |
| `parallel_default_strategy` | `PARALLEL_DEFAULT_STRATEGY` | `fast_fail` | 并行默认失败策略 |
| `subworkflow_max_depth` | `SUBWORKFLOW_MAX_DEPTH` | `5` | 子工作流最大嵌套深度 |
| `dynamic_injection_max_count` | `DYNAMIC_INJECTION_MAX_COUNT` | `100` | 动态注入最大数量 |
| `scheduler_batch_max_nodes` | `SCHEDULER_BATCH_MAX_NODES` | `20` | 批处理单批最大节点数 |
| `checkpoint_compression_target_ratio` | `CHECKPOINT_COMPRESSION_TARGET_RATIO` | `0.6` | 检查点压缩目标比率 |
| `extra_placeholder` | `EXTRA_PLACEHOLDER` | `None` | 预留扩展占位字段 |

### 第三阶段治理配置

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `rbac_permission_cache_ttl_seconds` | `RBAC_PERMISSION_CACHE_TTL_SECONDS` | `300` | 权限缓存 TTL（秒） |
| `tenant_default_quota_max_instances` | `TENANT_DEFAULT_QUOTA_MAX_INSTANCES` | `100` | 默认租户最大实例数 |
| `tenant_default_quota_max_workflows` | `TENANT_DEFAULT_QUOTA_MAX_WORKFLOWS` | `1000` | 默认租户最大工作流数 |
| `token_default_ttl_seconds` | `TOKEN_DEFAULT_TTL_SECONDS` | `3600` | 令牌默认有效期（秒） |
| `token_signing_secret` | `TOKEN_SIGNING_SECRET` | `change-me-token-secretF` | 令牌签名密钥 |
| `metering_buffer_queue_max_size` | `METERING_BUFFER_QUEUE_MAX_SIZE` | `10000` | 计量缓冲队列最大长度 |
| `metering_buffer_persist_path` | `METERING_BUFFER_PERSIST_PATH` | `./.data/usage_buffer` | 计量缓冲持久化路径 |
| `billing_cost_precision` | `BILLING_COST_PRECISION` | `6` | 成本金额精度（小数位数） |
| `budget_circuit_break_latency_seconds` | `BUDGET_CIRCUIT_BREAK_LATENCY_SECONDS` | `5` | 预算熔断检测延迟（秒） |
| `alert_channel_type` | `ALERT_CHANNEL_TYPE` | `webhook` | 告警通道类型：webhook/email/log |
| `alert_webhook_url` | `ALERT_WEBHOOK_URL` | `` | 告警 Webhook URL |
| `audit_write_fail_strategy` | `AUDIT_WRITE_FAIL_STRATEGY` | `rollback` | 审计写入失败策略 |
| `audit_hash_algorithm` | `AUDIT_HASH_ALGORITHM` | `sha256` | 审计哈希链算法 |
| `audit_export_max_records` | `AUDIT_EXPORT_MAX_RECORDS` | `100000` | 审计导出最大记录数 |
| `audit_export_format` | `AUDIT_EXPORT_FORMAT` | `json` | 审计导出格式 |
| `audit_mask_default_strategy` | `AUDIT_MASK_DEFAULT_STRATEGY` | `full_mask` | 默认脱敏策略 |
| `supervisor_orchestration_state_backend` | `SUPERVISOR_ORCHESTRATION_STATE_BACKEND` | `memory` | 编排状态存储后端 |
| `supervisor_orchestration_max_sub_orchestrators` | `SUPERVISOR_ORCHESTRATION_MAX_SUB_ORCHESTRATORS` | `20` | 单编排子编排器数量上限 |
| `supervisor_sub_task_retry_max` | `SUPERVISOR_SUB_TASK_RETRY_MAX` | `2` | 子任务失败最大重试次数 |
| `agent_performance_memory_window_size` | `AGENT_PERFORMANCE_MEMORY_WINDOW_SIZE` | `10000` | 内存绩效仓储环形窗口容量（先进先出淘汰） |
| `global_scheduler_decision_timeout_ms` | `GLOBAL_SCHEDULER_DECISION_TIMEOUT_MS` | `100` | 调度决策超时（毫秒） |
| `scheduler_queue_max_size` | `SCHEDULER_QUEUE_MAX_SIZE` | `10000` | 调度队列最大长度 |
| `scheduler_fair_weight_default` | `SCHEDULER_FAIR_WEIGHT_DEFAULT` | `1` | 公平调度默认权重 |
| `scheduler_preemption_max_count` | `SCHEDULER_PREEMPTION_MAX_COUNT` | `3` | 单实例最大被抢占次数 |
| `resource_pool_concurrency_slot_quota` | `RESOURCE_POOL_CONCURRENCY_SLOT_QUOTA` | `100` | 并发槽位配额 |
| `resource_pool_oversubscription_ratio` | `RESOURCE_POOL_OVERSUBSCRIPTION_RATIO` | `1.0` | 资源超售比 |
| `resource_estimate_default_slots` | `RESOURCE_ESTIMATE_DEFAULT_SLOTS` | `1` | 默认资源需求槽位 |

### 第三阶段偏差修正配置

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `nl_orchestration_max_input_length` | `NL_ORCHESTRATION_MAX_INPUT_LENGTH` | `4000` | NL 指令最大输入长度（字符） |
| `nl_orchestration_timeout_seconds` | `NL_ORCHESTRATION_TIMEOUT_SECONDS` | `30` | NL 解析超时（秒） |
| `nl_orchestration_model` | `NL_ORCHESTRATION_MODEL` | `` | NL 编排使用的 LLM 模型标识 |
| `nl_orchestration_max_correction_rounds` | `NL_ORCHESTRATION_MAX_CORRECTION_ROUNDS` | `2` | 拓扑校验失败后 LLM 自纠错最大轮次 |
| `abac_decision_timeout_ms` | `ABAC_DECISION_TIMEOUT_MS` | `50` | ABAC 单次决策超时（毫秒） |
| `abac_default_fail_mode` | `ABAC_DEFAULT_FAIL_MODE` | `deny` | 属性求值失败默认模式 |
| `tombstone_batch_size` | `TOMBSTONE_BATCH_SIZE` | `1000` | 被遗忘权批量标记每批条数 |
| `redis_url` | `REDIS_URL` | `` | Redis 连接地址（空则降级内存缓存） |
| `redis_permission_cache_ttl` | `REDIS_PERMISSION_CACHE_TTL` | `300` | 权限缓存 TTL（秒） |
| `redis_session_cache_ttl` | `REDIS_SESSION_CACHE_TTL` | `1800` | 会话缓存 TTL（秒） |
| `redis_lock_default_ttl` | `REDIS_LOCK_DEFAULT_TTL` | `30` | 分布式锁默认过期时间（秒） |
| `redis_rate_limit_window` | `REDIS_RATE_LIMIT_WINDOW` | `60` | 限流计数窗口（秒） |
| `kafka_bootstrap_servers` | `KAFKA_BOOTSTRAP_SERVERS` | `` | Kafka 引导服务器地址（空则降级内存总线） |
| `kafka_workflow_topic` | `KAFKA_WORKFLOW_TOPIC` | `workflow_events` | 工作流执行事件 topic |
| `kafka_session_topic` | `KAFKA_SESSION_TOPIC` | `session_events` | 会话状态变更事件 topic |
| `kafka_consumer_group` | `KAFKA_CONSUMER_GROUP` | `orchestration_platform` | Kafka 消费者组标识 |
| `kafka_replay_max_events` | `KAFKA_REPLAY_MAX_EVENTS` | `10000` | 单次重放最大事件数 |
| `event_bus_memory_history_max` | `EVENT_BUS_MEMORY_HISTORY_MAX` | `10000` | 内存事件总线保留历史条数上限 |
| `event_export_webhook_max_attempts` | `EVENT_EXPORT_WEBHOOK_MAX_ATTEMPTS` | `5` | 单事件 Webhook 最大投递尝试次数 |
| `event_export_webhook_backoff_base_seconds` | `EVENT_EXPORT_WEBHOOK_BACKOFF_BASE_SECONDS` | `1.0` | 投递指数退避基数（秒） |
| `event_export_webhook_backoff_cap_seconds` | `EVENT_EXPORT_WEBHOOK_BACKOFF_CAP_SECONDS` | `60.0` | 投递退避上限（秒） |
| `event_export_webhook_timeout_seconds` | `EVENT_EXPORT_WEBHOOK_TIMEOUT_SECONDS` | `5.0` | 单次 Webhook 请求超时（秒） |
| `event_export_delivery_history_max` | `EVENT_EXPORT_DELIVERY_HISTORY_MAX` | `1000` | 每订阅投递记录环形上限 |
| `event_export_dlq_max_entries` | `EVENT_EXPORT_DLQ_MAX_ENTRIES` | `1000` | 死信总量上限（超限丢最旧） |
| `event_export_subscriptions_persist_path` | `EVENT_EXPORT_SUBSCRIPTIONS_PERSIST_PATH` | `./.data/event_export/subscriptions.json` | 事件出口订阅持久化路径（B7 W3：变更后原子落盘、启动期恢复） |
| `schedule_scanner_interval_seconds` | `SCHEDULE_SCANNER_INTERVAL_SECONDS` | `30` | 定时触发扫描间隔（秒） |
| `sandbox_default_isolation` | `SANDBOX_DEFAULT_ISOLATION` | `logical` | 默认隔离模式：logical/physical |
| `sandbox_execution_timeout_seconds` | `SANDBOX_EXECUTION_TIMEOUT_SECONDS` | `60` | 沙箱默认执行超时（秒） |
| `sandbox_default_cpu_seconds` | `SANDBOX_DEFAULT_CPU_SECONDS` | `10.0` | 默认 CPU 时间上限（秒） |
| `sandbox_default_memory_mb` | `SANDBOX_DEFAULT_MEMORY_MB` | `512` | 默认内存上限（MB） |
| `sandbox_default_output_bytes` | `SANDBOX_DEFAULT_OUTPUT_BYTES` | `1048576` | 默认输出尺寸上限（字节） |
| `sandbox_workdir_root` | `SANDBOX_WORKDIR_ROOT` | `./.data/sandbox/workdirs` | 沙箱工作目录根 |
| `sandbox_container_endpoint` | `SANDBOX_CONTAINER_ENDPOINT` | `` | 容器运行时端点（空则降级逻辑隔离） |
| `sandbox_container_timeout_seconds` | `SANDBOX_CONTAINER_TIMEOUT_SECONDS` | `120` | 容器执行超时（秒） |
| `sandbox_violation_topic` | `SANDBOX_VIOLATION_TOPIC` | `sandbox_violations` | 违规事件 topic |
| `code_execution_timeout_seconds` | `CODE_EXECUTION_TIMEOUT_SECONDS` | `10` | 代码节点受限执行超时（秒；与沙箱策略超时取较小值） |
| `code_output_max_bytes` | `CODE_OUTPUT_MAX_BYTES` | `1048576` | 代码节点输出序列化字节数上限（与沙箱策略输出上限取较小值） |
| `code_execution_channel` | `CODE_EXECUTION_CHANNEL` | `inprocess` | 代码节点执行通道（inprocess=受限进程内执行；subprocess=子进程；非法值由执行器降级 inprocess 并告警） |
| `plugin_sdk_max_nodes` | `PLUGIN_SDK_MAX_NODES` | `200` | 平台最大插件节点数 |
| `plugin_sdk_setup_timeout_seconds` | `PLUGIN_SDK_SETUP_TIMEOUT_SECONDS` | `30` | setup 阶段超时（秒） |
| `plugin_sdk_packages_root` | `PLUGIN_SDK_PACKAGES_ROOT` | `./.data/plugin_packages` | 插件节点包安装根目录 |
| `node_contribution_signature_algorithm` | `NODE_CONTRIBUTION_SIGNATURE_ALGORITHM` | `hmac-sha256` | 签名算法（hmac-sha256 或 rsa-sha256） |
| `node_contribution_signature_secret` | `NODE_CONTRIBUTION_SIGNATURE_SECRET` | `` | 签名密钥（环境变量注入，空则拒绝提交） |
| `node_contribution_scan_timeout_seconds` | `NODE_CONTRIBUTION_SCAN_TIMEOUT_SECONDS` | `120` | 静态安全扫描超时（秒） |
| `node_contribution_storage_root` | `NODE_CONTRIBUTION_STORAGE_ROOT` | `./.data/contributions` | 贡献存储根目录 |
| `sso_session_ttl_seconds` | `SSO_SESSION_TTL_SECONDS` | `3600` | SSO 会话 TTL（秒） |
| `sso_assertion_replay_cache_ttl` | `SSO_ASSERTION_REPLAY_CACHE_TTL` | `300` | 断言防重放缓存 TTL（秒） |
| `sso_default_role_on_missing_attr` | `SSO_DEFAULT_ROLE_ON_MISSING_ATTR` | `` | 属性缺失时缺省角色（空则拒绝开通） |
| `credential_encryption_key` | `CREDENTIAL_ENCRYPTION_KEY` | `` | 凭据加密密钥（空则拒绝持久化凭据） |
| `enterprise_connector_default_timeout_seconds` | `ENTERPRISE_CONNECTOR_DEFAULT_TIMEOUT_SECONDS` | `30` | 连接器默认超时（秒） |
| `enterprise_connector_test_timeout_seconds` | `ENTERPRISE_CONNECTOR_TEST_TIMEOUT_SECONDS` | `8` | 连接测试探活超时（秒） |
| `evaluation_random_seed` | `EVALUATION_RANDOM_SEED` | `42` | 固定随机种子（可复现） |
| `evaluation_llm_temperature` | `EVALUATION_LLM_TEMPERATURE` | `0.0` | LLM 评分温度 |
| `evaluation_regression_degrade_threshold` | `EVALUATION_REGRESSION_DEGRADE_THRESHOLD` | `0.05` | 回归退化告警阈值 |
| `evaluation_max_scenarios` | `EVALUATION_MAX_SCENARIOS` | `500` | 场景集条目上限 |
| `agent_discovery_vector_backend` | `AGENT_DISCOVERY_VECTOR_BACKEND` | `` | 向量索引后端（空则纯关键词） |
| `agent_discovery_vector_endpoint` | `AGENT_DISCOVERY_VECTOR_ENDPOINT` | `` | 向量索引端点 |
| `agent_discovery_vector_timeout_ms` | `AGENT_DISCOVERY_VECTOR_TIMEOUT_MS` | `300` | 向量索引超时（毫秒） |
| `agent_discovery_relevance_threshold` | `AGENT_DISCOVERY_RELEVANCE_THRESHOLD` | `0.3` | 相关度阈值 |
| `agent_discovery_result_limit` | `AGENT_DISCOVERY_RESULT_LIMIT` | `10` | 结果数量上限 |
| `agent_discovery_min_query_length` | `AGENT_DISCOVERY_MIN_QUERY_LENGTH` | `4` | 最短查询长度 |
| `agent_discovery_desc_max_length` | `AGENT_DISCOVERY_DESC_MAX_LENGTH` | `2000` | 描述最大长度 |
| `vector_kb_search_base_url` | `VECTOR_KB_SEARCH_BASE_URL` | `` | 向量检索服务地址（api 模式 embedding/search/rerank 共用；空则 API 检索不可用） |
| `vector_kb_search_timeout_seconds` | `VECTOR_KB_SEARCH_TIMEOUT_SECONDS` | `60` | 向量检索服务超时（秒） |
| `vector_kb_search_api_key` | `VECTOR_KB_SEARCH_API_KEY` | `` | 向量检索服务访问密钥（Bearer；仅 .env 注入，禁止写入工作流定义或日志） |
| `local_vector_database_storage_root` | `LOCAL_VECTOR_DATABASE_STORAGE_ROOT` | `./.data/vector_kb` | 本地向量数据库存储根目录（一集合一文件 sqlite） |
| `local_vector_database_default_dimension` | `LOCAL_VECTOR_DATABASE_DEFAULT_DIMENSION` | `128` | 默认向量维度 |
| `local_vector_database_default_metric` | `LOCAL_VECTOR_DATABASE_DEFAULT_METRIC` | `cosine` | 默认相似度度量（cosine / dot / euclidean） |
| `local_vector_database_backend` | `LOCAL_VECTOR_DATABASE_BACKEND` | `` | 增强检索后端标识（预留；空则暴力精确检索） |
| `local_vector_database_query_timeout_ms` | `LOCAL_VECTOR_DATABASE_QUERY_TIMEOUT_MS` | `1000` | 查询超时（毫秒） |
| `local_vector_database_max_records_per_collection` | `LOCAL_VECTOR_DATABASE_MAX_RECORDS_PER_COLLECTION` | `100000` | 单集合最大记录数 |
| `local_vector_database_max_top_k` | `LOCAL_VECTOR_DATABASE_MAX_TOP_K` | `100` | 单次检索 Top-K 上限 |
| `local_vector_database_storage` | `LOCAL_VECTOR_DATABASE_STORAGE` | `sqlite` | 本地向量数据库存储路径（默认 sqlite：存量路径零变化；pg_array 为新建集合的可选 PostgreSQL 原生数组路径，零新增依赖） |
| `vector_kb_management_storage_root` | `VECTOR_KB_MANAGEMENT_STORAGE_ROOT` | `./.data/vector_kb_management` | 知识库元数据存储根目录（独立 sqlite3 落点） |
| `vector_kb_management_max_file_size_mb` | `VECTOR_KB_MANAGEMENT_MAX_FILE_SIZE_MB` | `10` | 单文件大小上界（MB，超限确定性拒绝） |
| `vector_kb_management_max_chunks_per_kb` | `VECTOR_KB_MANAGEMENT_MAX_CHUNKS_PER_KB` | `100000` | 单知识库分片总量上界（与内核条目上限对齐） |
| `vector_kb_management_default_chunk_length` | `VECTOR_KB_MANAGEMENT_DEFAULT_CHUNK_LENGTH` | `500` | 默认分片长度上限（50..2000） |
| `vector_kb_management_default_overlap` | `VECTOR_KB_MANAGEMENT_DEFAULT_OVERLAP` | `50` | 默认相邻分片重叠长度（必须 < max_chunk_length） |
| `vector_kb_management_default_similarity_threshold` | `VECTOR_KB_MANAGEMENT_DEFAULT_SIMILARITY_THRESHOLD` | `0.75` | 语义分块默认相似度阈值（仅 semantic） |
| `vector_kb_write_max_text_length` | `VECTOR_KB_WRITE_MAX_TEXT_LENGTH` | `500000` | 写入模式待写入文本长度上界（字符数；仅约束写入，默认不改变既有检索/加载行为） |
| `vector_kb_annotation_max_items` | `VECTOR_KB_ANNOTATION_MAX_ITEMS` | `32` | 自定义标注项数上界（默认 32） |
| `vector_kb_annotation_max_value_length` | `VECTOR_KB_ANNOTATION_MAX_VALUE_LENGTH` | `512` | 自定义标注值长度上界（默认 512 字符） |

### 运行环境与安全强化（arch_review_v2_tasks G1/G2/G3，仅追加）

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `environment` | `ENVIRONMENT` | `development` | 运行环境（production 时弱密钥 fail-fast 拒启动；默认 development 仅告警不阻断） |
| `token_verify_mode` | `TOKEN_VERIFY_MODE` | `both` | 令牌校验模式（both 默认：存量明文令牌继续可用，GK-02；production 下仅允许 jwt，非 jwt 值由启动强校验拒绝启动，B0/D2） |
| `token_audience` | `TOKEN_AUDIENCE` | `orchestration-platform-api` | JWT 受众校验值（aud） |
| `token_issuer` | `TOKEN_ISSUER` | `orchestration-platform` | JWT 签发方校验值（iss） |
| `token_clock_skew_seconds` | `TOKEN_CLOCK_SKEW_SECONDS` | `60` | JWT exp 校验时钟偏移容忍（秒） |
| `rbac_bootstrap_admin_user` | `RBAC_BOOTSTRAP_ADMIN_USER` | `` | bootstrap 初始管理员用户标识（空则不创建初始绑定） |

### G6 实例级步数护栏（arch_review_v2 3.6，CHG-01/04/08；卡 A2；DC-10）

| 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `workflow_max_steps` | `WORKFLOW_MAX_STEPS` | `500` | 实例级最大执行步数（轮次全局限额；超出抛 WF_BUDGET_EXCEEDED） |
