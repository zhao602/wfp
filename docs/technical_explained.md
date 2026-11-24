# 企业级多 Agent 工作流编排平台 —— 技术讲解

> 面向 Agent 初学者的全量技术讲解文档。
> 本文档覆盖平台全部模块（phase1-4 基础能力 + phase5 偏差修正 G1~G8），
> 以「这个模块是做什么的、为什么这么设计、初学者怎么入手」三问为线索，
> 帮助新人在一天内建立对整个代码仓的心智模型。

---

## 目录

- [一、平台是做什么的](#一平台是做什么的)
- [二、整体架构（五层）](#二整体架构五层)
- [三、领域层：枚举、错误码与模型](#三领域层枚举错误码与模型)
- [四、引擎层：调度、节点执行与检查点](#四引擎层调度节点执行与检查点)
- [五、应用层：范例库与执行服务](#五应用层范例库与执行服务)
- [六、配置体系](#六配置体系)
- [七、治理层：十二个子模块全览](#七治理层十二个子模块全览)
- [八、集成层：LLM、数据源、MCP 与沙箱](#八集成层llm数据源mcp-与沙箱)
- [九、上下文引擎](#九上下文引擎)
- [十、插件 SDK 与 MCP 工具市场](#十插件-sdk-与-mcp-工具市场)
- [十一、持久化层](#十一持久化层)
- [十二、可观测性](#十二可观测性)
- [十三、API 层：路由全览与条件注册](#十三api-层路由全览与条件注册)
- [十四、前端架构](#十四前端架构)
- [十五、八种编排模式（按范例学习路径）](#十五八种编排模式按范例学习路径)
- [十六、第五次偏差修正（G1~G8）](#十六第五次偏差修正g1g8)
- [十七、四条工程约束的设计意图](#十七四条工程约束的设计意图)
- [十八、测试体系](#十八测试体系)
- [十九、给初学者的下一步](#十九给初学者的下一步)

---

## 一、平台是做什么的

一句话：**把"多个 Agent 协作完成任务"这件事，变成可以用 YAML 声明、可以编排、可以观测、可以治理的工程问题。**

一个工作流（Workflow）由节点（Node）和边（Edge）组成。节点类型包括：

| 节点类型 | 作用 |
| --- | --- |
| `trigger` | 入口：手动触发、事件触发 |
| `data_source` | 读取外部数据（Hologres/Kafka 等） |
| `data_transform` | 数据清洗/过滤/映射/聚合/连接 |
| `agent` | 调用 LLM 完成认知任务 |
| `tool` | 调用工具（MCP 工具、本地函数） |
| `subworkflow` | 嵌套调用另一个工作流定义 |
| `control_flow` | 控制流：条件分支、循环、并行汇聚、人工审核 |
| `dynamic_injector` | 运行时按条件动态生成节点实例 |
| `plugin` | 第三方插件节点（经 SDK 开发） |
| `supervisor` | 监督者编排（G1 新增，见第十六章） |

所有节点执行器都实现同一个协议（`NodeExecutor`）：输入 `NodeExecutionContext`，
输出 `NodeExecutionResult`。调度器只认协议，不认具体实现——这就是为什么
新增 SUPERVISOR 节点类型时，既有代码零修改（仅注册表追加一个注册方法）。

---

## 二、整体架构（五层）

```
┌─────────────────────────────────────────────────────────┐
│  API 层（FastAPI）：路由按功能开关条件注册                   │
│  application_factory.py 装配 → 14 个顶层路由 + 12 个治理路由 │
├─────────────────────────────────────────────────────────┤
│  应用层：范例库 / 执行实例服务 / 回滚重放 / 日志脱敏          │
├─────────────────────────────────────────────────────────┤
│  引擎层：调度器 / 节点执行器注册表 / 检查点 / 并行执行器       │
│         LangGraph 适配器 / 监督者编排器（G1）               │
│         实时订阅 + 触发器模板（G6）/ 插件节点加载器           │
├─────────────────────────────────────────────────────────┤
│  领域层：枚举（30+）/ 模型（10 类）/ 错误码（80+）/ 校验器     │
├─────────────────────────────────────────────────────────┤
│  基础设施：YAML 解析 / 事件总线 / 仓储（内存/文件/PG）        │
│  可观测：MetricsCollector / Agent 绩效（G3）/ 审计 / 结构化日志│
│  治理：RBAC+ABAC / 计量计费 / 调度 / 沙箱 / SSO / 评估 ...   │
│  集成：LLM 主备 / 数据源连接器 / MCP 客户端+服务端 / 沙箱     │
└─────────────────────────────────────────────────────────┘
```

初学者只需要先记住三个"单一事实来源"：

1. **错误码集中在 `domain/errors.py`**——前端和调用方按码分支处理；
2. **枚举集中在 `domain/models/enums.py`**——只追加成员，不修改既有取值；
3. **配置集中在 `configuration/settings.py`**——与 `.env.example` 键名一一对应。

---

## 三、领域层：枚举、错误码与模型

> 路径：`src/orchestration_platform/domain/`

领域层是整个平台的"契约层"——定义了所有枚举值、数据模型和错误码，
**不依赖任何其他层**。其他层引用领域层，领域层不引用其他层。

### 3.1 枚举（`domain/models/enums.py`，30+ 个枚举类）

按阶段递进：

| 阶段 | 关键枚举 | 说明 |
| --- | --- | --- |
| 一 | `NodeType`(11种) | 节点类型：AGENT/TOOL/DATA_SOURCE/TRIGGER/CONTROL_FLOW/SUBWORKFLOW/DYNAMIC_INJECTOR/DATA_TRANSFORM/PLUGIN/SUPERVISOR |
| 一 | `ControlFlowKind`(4种) | 控制流子类型：条件分支/循环/并行汇聚/人工审核门 |
| 一 | `InstanceStatus`(10种) | 实例状态：PENDING→RUNNING→SUCCEEDED/FAILED/CANCELLED... |
| 一 | `EventType`(25+种) | 执行事件类型：节点开始/完成/失败、检查点保存、状态变更... |
| 二 | `ContextWindowStrategyType`(4种) | 上下文窗口策略：滑动窗口/摘要压缩/重要性排序/结构化无损裁剪 |
| 二 | `DataTransformOperation`(4种) | 数据转换操作：MAP/FILTER/AGGREGATE/JOIN |
| 二 | `DataSourceType`(11种) | 数据源类型：CSV/JSON/REST_API/DATABASE/HOLOGRES/KAFKA/FLINK/SPARK/CRM/ERP/OA |
| 三 | `ResourceType`(7种) | 受保护资源类型（RBAC） |
| 三 | `MaskMethod`(4种) | 脱敏方法：替换/掩码/哈希/删除 |
| 四 | `ContributionState`(7态) | 贡献状态机：DRAFT→SUBMITTED→SCAN→REVIEW→...→PUBLISHED |
| 五 | `TriggerTemplateType`(3种) | 触发器模板：滑动窗口异常/阈值持续突破/方差突变 |

**设计纪律**：枚举只追加成员，不修改既有取值，不删除——保证序列化向后兼容。

### 3.2 错误码（`domain/errors.py`，80+ 个错误码）

按模块分组，前缀即模块名：

| 前缀 | 模块 | 示例 |
| --- | --- | --- |
| `WF_*` | 工作流定义 | WF_YAML_SYNTAX_ERROR / WF_CYCLIC_GRAPH / WF_LOOP_NOT_CONVERGED |
| `NODE_*` | 节点执行 | NODE_AGENT_OUTPUT_INVALID / NODE_LLM_UNAVAILABLE / NODE_TOOL_TIMEOUT |
| `STATE_*` | 状态管理 | STATE_CHECKPOINT_NOT_FOUND / STATE_CHECKPOINT_CORRUPTED |
| `TRIGGER_*` | 触发器 | TRIGGER_TEMPLATE_UNKNOWN / TRIGGER_TEMPLATE_PARAM_INVALID |
| `DATASOURCE_*` | 数据源 | DATASOURCE_CONNECTION_FAILED / DATASOURCE_READ_TIMEOUT |
| `MCP_*` | MCP 工具 | MCP_CONNECTION_FAILED / MCP_TOOL_PERMISSION_DENIED |
| `SANDBOX_*` | 沙箱 | 沙箱隔离违规相关 7 个码 |
| `SUP_*` | 监督者编排（G1） | SUP_ROUTE_DECISION_FAILED / SUP_STATE_RESTORE_FAILED |
| `AUDIT_*` | 审计（G5） | AUDIT_CONTENT_HASH_MISMATCH |

异常类层级：`DomainError`（基类，携带 `code`/`message`/`details`）→
`WorkflowValidationError`(422) / `InstanceStateConflictError`(409) /
`ResourceNotFoundError`(404) / `NodeExecutionError` / `TriggerError`...

**初学者记住**：看到错误码前缀就知道是哪个模块出的错，前端按码分支处理用户提示。

### 3.3 数据模型（`domain/models/`，10 个模型文件）

| 文件 | 核心模型 | 说明 |
| --- | --- | --- |
| `workflow_definition.py` | WorkflowDefinition | 工作流定义（ID+版本+节点列表+边列表+元数据） |
| `node_definition.py` | NodeDefinition | 节点定义（ID+类型+配置引用） |
| `edge_definition.py` | EdgeDefinition | 连线定义（源节点+目标节点+条件表达式） |
| `node_configurations.py` | 五类节点配置 | AgentNodeConfiguration / ToolNodeConfiguration / DataSourceNodeConfiguration / ControlFlowNodeConfiguration / DataTransformNodeConfiguration |
| `execution_instance.py` | ExecutionInstance | 执行实例（定义ID+状态+当前节点+共享状态引用） |
| `shared_state.py` | SharedState | 共享状态（键值对字典，节点间数据传递的载体） |
| `execution_event.py` | ExecutionEvent | 执行事件（类型+载荷+时间戳，事件溯源的基础） |
| `checkpoint.py` | Checkpoint | 检查点（实例ID+节点ID+状态快照+序号） |
| `common.py` | 公共类型 | VersionedEntity / PaginatedResult 等 |

### 3.4 校验器（`domain/validation/`，4 个校验器）

| 校验器 | 职责 |
| --- | --- |
| `definition_structure_validator.py` | 五要素结构校验：节点非空、边合法、单触发器、无悬空边 |
| `graph_topology_validator.py` | 图拓扑校验：环路检测、可达性分析 |
| `node_configuration_validator.py` | 节点配置校验：各类型配置字段完整性 |
| `validation_report_builder.py` | 校验报告构建：汇总错误/警告，生成可读报告 |

---

## 四、引擎层：调度、节点执行与检查点

> 路径：`src/orchestration_platform/engine/`

引擎层是平台的"心脏"——负责编译工作流定义、调度节点执行、管理检查点。

### 4.1 节点执行器（`engine/nodes/`，9 个执行器）

每个执行器实现 `NodeExecutor` 协议（`base.py` 中定义），调度器统一调用：

| 执行器 | 节点类型 | 职责 |
| --- | --- | --- |
| `agent_node_executor.py` | AGENT | 调用 LLM 推理，ReAct 工具调用循环、结构化输出校验、主备模型切换 |
| `tool_node_executor.py` | TOOL | 通过 ToolInvoker 调用本地函数或 MCP 工具 |
| `data_source_node_executor.py` | DATA_SOURCE | 只读查询数据源，空结果标记成功 |
| `trigger_node_executor.py` | TRIGGER | 入口标记节点，支持手动/定时/数据事件触发 |
| `control_flow_node_executor.py` | CONTROL_FLOW | 条件分支/循环/并行汇聚/人工审核门四类子类型 |
| `subworkflow_node_executor.py` | SUBWORKFLOW | 递归调用引擎实现子工作流嵌套 |
| `dynamic_injector_node_executor.py` | DYNAMIC_INJECTOR | 运行时按条件求值动态生成节点实例 |
| `data_transform_node_executor.py` | DATA_TRANSFORM | 对数据集执行映射/过滤/聚合/Join |
| `supervisor_orchestration/node_executor.py` | SUPERVISOR | 监督者编排（G1，见第十六章） |

### 4.2 节点执行器注册表（`engine/node_executor_registry.py`）

`NodeExecutorRegistry` 按 `NodeType` 登记与查找执行器。提供三个登记方法：
- `register(executor)`：通用登记
- `register_phase2_executors()`：登记子工作流 + 动态注入 + 数据转换执行器
- `register_supervisor_executor()`：登记监督者编排器（G1）

**关键设计**：注册表是"开放-封闭"的——新增节点类型只需追加执行器 + 注册方法，
既有执行器和调度器零修改。

### 4.3 调度器（`engine/scheduler.py`，433 行）

调度器是引擎层的核心，负责：
- **确定性调度**：按拓扑序执行节点，同一输入同一执行路径
- **条件分支求值**：`RestrictedExpressionEvaluator` 仅允许比较与逻辑运算（安全沙箱）
- **循环控制**：`loop_counters` 递增，达上限返回 `WF_LOOP_NOT_CONVERGED`
- **并行汇聚**：`PARALLEL_JOIN` 节点等待所有分支完成后汇聚结果
- **人工审核门**：`HUMAN_REVIEW_GATE` 暂停执行等待外部审批
- **超时控制**：节点级 + 实例级超时
- **检查点写入**：统一由调度器写检查点（不在各执行器中分散写入）

### 4.4 检查点与恢复

| 文件 | 职责 |
| --- | --- |
| `checkpoint_compressor.py` | 检查点状态快照差分编码、字段裁剪与引用去重 |
| `shared_state_manager.py` | 管理 SharedState 读写，向节点执行器提供只读状态视图 |
| `parallel_executor.py` | 将 PARALLEL_JOIN 从"顺序等待"升级为"真正并行执行 + 收口" |

### 4.5 LangGraph 适配（`engine/adapter/`）

| 文件 | 职责 |
| --- | --- |
| `langgraph_adapter.py` | LangGraph 双运行时适配器：将 CompiledWorkflow 编译为 LangGraph StateGraph |
| `langgraph_state_mapping.py` | SharedState 与 LangGraph 状态字典双向转换 |

**重要约束**：业务代码禁止直接 `import langgraph`，所有图编译、检查点、中断恢复
经此适配层封装。测试中有静态断言扫描新增包的 import 树。

### 4.6 图编译（`engine/graph_compiler.py` + `abstractions/`）

| 文件 | 职责 |
| --- | --- |
| `graph_compiler.py` | 将 WorkflowDefinition 编译为 CompiledWorkflow 中间表示 |
| `abstractions/compiled_workflow.py` | 框架无关的可执行图中间表示 |
| `abstractions/workflow_compiler.py` | IWorkflowCompiler 编译契约 |

---

## 五、应用层：范例库与执行服务

> 路径：`src/orchestration_platform/application/`

应用层是 API 层和引擎层之间的"协调层"——编排领域对象、调用引擎、管理生命周期。

| 文件 | 职责 |
| --- | --- |
| `example_library_service.py` | 加载预置范例工作流定义，检查环境变量依赖，返回范例列表（含可用性标记）。内部维护 `_EXAMPLE_REGISTRY` 字典登记所有范例。 |
| `execution_instance_service.py` | 管理执行实例生命周期——创建、启动、检查点生成、故障恢复 |
| `rollback_replay_service.py` | 实现实例回滚与两种模式重放（STRICT_REPLAY / RE_EXECUTE），状态可靠性核心闭环 |
| `log_sanitizer.py` | 对事件载荷进行脱敏处理，防止密钥、令牌等敏感信息落盘 |

---

## 六、配置体系

> 路径：`src/orchestration_platform/configuration/settings.py`

`Settings(BaseSettings)` 使用 pydantic-settings 从 `.env` 加载，
通过 `get_settings()`（lru_cache）提供全局唯一实例。

### 配置分组概览（90+ 个配置键）

| 分组 | 关键配置 | 说明 |
| --- | --- | --- |
| API 服务 | api_host / api_port / api_access_token | 服务监听地址与鉴权令牌 |
| 存储后端 | storage_backend / local_storage_root / postgres_* | local_file 或 postgres |
| 工作流约束 | workflow_max_nodes(50) | 单工作流最大节点数 |
| 引擎调度 | node_default_timeout_seconds / loop_max_iterations / checkpoint_interval_seconds | 调度参数 |
| LLM 主备 | llm_primary_* / llm_fallback_* | 主模型失败自动切备用 |
| MCP 工具 | mcp_server_enabled / mcp_transport / mcp_connection_pool_max | MCP 服务端与连接池 |
| RBAC | rbac_enabled / rbac_permission_cache_ttl_seconds | 鉴权开关与缓存 |
| 审计 | audit_enabled / audit_hash_algorithm / audit_content_hash_enabled | 审计与内容哈希（G5） |
| 监督者编排 | supervisor_orchestration_enabled / supervisor_orchestration_state_backend | G1 开关与状态后端 |
| 触发器模板 | trigger_template_enabled | G6 开关 |
| Agent 绩效 | agent_performance_enabled / agent_performance_memory_window_size | G3 开关与窗口大小 |
| 全局调度 | global_scheduler_enabled / scheduler_preemption_enabled | 调度与抢占 |
| NL 编排 | nl_orchestration_enabled / nl_orchestration_max_correction_rounds | 自然语言编排 |
| Redis | redis_enabled / redis_url | 分布式缓存 |
| Kafka | kafka_enabled / kafka_bootstrap_servers | 事件总线 |
| 沙箱 | sandbox_enabled / sandbox_default_isolation | Agent 执行沙箱 |
| 插件 SDK | plugin_sdk_enabled / plugin_sdk_packages_root | 第三方插件 |
| SSO | sso_enabled / sso_session_ttl_seconds | 企业身份集成 |
| 评估框架 | evaluation_enabled / evaluation_llm_scoring_enabled | Agent 效能评估 |

**设计纪律**：
- `settings.py` 与 `.env.example` 键名一一对应双同步
- `.env.example` 只放占位符默认值，真实密钥只进 `.env`（被 .gitignore 忽略）
- G1~G6 每项新能力都有 `xxx_enabled` 开关，**默认 False**

---

## 七、治理层：十二个子模块全览

> 路径：`src/orchestration_platform/governance/`

治理层提供企业级平台所需的横切关注点。所有治理能力通过开关控制，
未启用时以"单租户默认全权限"降级模式运行（`degraded_mode.py`）。

### 7.1 治理拦截器链（`governance/interceptors/`，8 个拦截器）

请求经拦截器链依次处理，任一拦截器可拒绝请求：

| 拦截器 | 职责 |
| --- | --- |
| `auth` | RBAC 鉴权：校验令牌 + 权限 |
| `tenant_isolation` | 租户隔离：确保请求只访问本租户资源 |
| `quota` | 配额校验：实例数/工作流数不超限 |
| `budget` | 预算熔断：成本超预算时拒绝 |
| `metering` | 用量计量：记录 Token 消耗 |
| `audit` | 审计日志：异步追加写入 |
| `global_scheduler` | 全局调度：排队/优先级/抢占 |

### 7.2 各子模块概览

| 子模块 | 职责 | 关键文件 |
| --- | --- | --- |
| **tenant/** | 租户生命周期、隔离、配额、组织树、令牌管理、用户管理 | tenant_manager / tenant_isolator / quota_checker / org_tree_manager / token_manager / user_manager |
| **rbac/** | RBAC 鉴权引擎 + 权限缓存 | permission_checker / permission_registry / permission_cache |
| **rbac/abac/** | ABAC 策略引擎 + RBAC+ABAC 组合决策 | abac_policy_engine / combined_decision_engine / attribute_evaluator |
| **audit/** | 审计日志异步写入、哈希链校验、字段级内容哈希（G5）、脱敏引擎、合规导出 | audit_logger / hash_chain_verifier / content_hasher / mask_engine / compliance_exporter |
| **audit/tombstone/** | 墓碑标记（脱敏原始内容保持哈希链连续）、GDPR 被遗忘权 | tombstone_service / right_to_be_forgotten_service |
| **billing/** | 预算管理、成本核算、定价规则、预算熔断守卫、告警通知 | budget_manager / cost_calculator / pricing_rule_manager / budget_guard / alert_notifier |
| **metering/** | Token 用量采集、异步入队批量写入 | token_meter / usage_buffer_queue |
| **cache/** | 分布式缓存（Redis 降级内存）、分布式锁、限流计数器 | cache_manager / redis_cache_backend / in_memory_cache_backend |
| **event_bus/** | 分布式事件总线（Kafka 降级内存）、事件重放恢复、序列号重排序 | event_bus_manager / kafka_event_bus / in_memory_event_bus / event_replay_service |
| **scheduler/** | 全局调度器、优先级队列、优先级抢占器、公平/纯优先级调度策略 | global_scheduler / priority_queue / preemptor |
| **sso/** | SAML 2.0/OIDC 断言验签（xmlsec/PyJWT）、IdP 配置管理、防重放、JIT 开通、AES-GCM 凭据加密 | sso_service / oidc_adapter / saml_adapter / replay_guard / credential_cipher |
| **evaluation/** | Agent 效能评估框架：CLEAR 四维评分、轨迹评分、基线管理、回归报告 | evaluation_runner / clear_scorer / llm_trajectory_scorer / baseline_store / regression_reporter |
| **nl_orchestration/** | 自然语言编排：注入防护→意图解析→YAML 生成→拓扑校验，支持 LLM 自纠错循环 | nl_orchestration_service / nl_intent_parser / yaml_generator / injection_guard |
| **agent_discovery/** | 语义索引 Agent 发现：关键词倒排索引 + 可选向量索引 + RRF 混合检索 | discovery_service / hybrid_retriever / keyword_index / vector_index |
| **resource/** | 资源池管理（并发槽位/CPU/内存） | resource_pool |

### 7.3 治理持久化（`governance/persistence/`）

- **abstractions/**：7 个 Protocol（audit_log / org_tree / role / tenant / token / token_usage / user 仓储）
- **local_file/**：7 个本地文件实现
- **postgres/**：6 个 PostgreSQL 实现

---

## 八、集成层：LLM、数据源、MCP 与沙箱

> 路径：`src/orchestration_platform/integrations/`

集成层封装所有外部系统交互，业务层只面向抽象接口。

### 8.1 LLM 客户端（`integrations/llm/`）

`primary_fallback_llm_client.py`：主备 LLM 客户端，主模型失败自动切换备用并标记事件。
配置项 `llm_primary_*` 和 `llm_fallback_*` 分别配置主备模型的 base_url / api_key / model / timeout。

### 8.2 数据源连接器（`integrations/data_source_connectors/`）

连接器注册表 + 9 种连接器：

| 连接器 | 数据源类型 | 说明 |
| --- | --- | --- |
| csv_connector | CSV | 本地 CSV 文件读取 |
| json_connector | JSON | 本地 JSON 文件读取 |
| rest_api_connector | REST_API | HTTP REST API 调用 |
| database_connector | DATABASE | 通用数据库（JDBC 风格） |
| hologres_connector | HOLOGRES | 阿里 Hologres 实时数仓 |
| kafka_connector | KAFKA | Kafka 消息队列 |
| flink_connector | FLINK | Flink 流计算 |
| spark_connector | SPARK | Spark 批计算 |

企业连接器（`data_source_connectors/enterprise/`）：CRM / ERP / OA + 凭证存储 + 数据范围守卫。

**安全约束**：`data_source/read_only_statement_guard.py` 拦截所有写/删/结构变更语句。

### 8.3 MCP 客户端与服务端（`integrations/mcp/` + `integrations/mcp_server/`）

**MCP 客户端**（`mcp/`）：
- `mcp_client.py`：统一入口
- `connection_pool.py`：连接池管理
- `tool_discovery_cache.py`：工具发现缓存
- `transports/`：三种传输协议（STDIO / HTTP / SSE）

**MCP 服务端**（`mcp_server/`）：
- 将平台能力暴露为 MCP 工具，9 个工具声明
- 鉴权与错误映射

### 8.4 Agent 执行沙箱（`integrations/sandbox/`）

| 文件 | 职责 |
| --- | --- |
| `logical_isolation_runtime.py` | 逻辑隔离（三守卫：网络/路径/资源） |
| `container_isolation_runtime.py` | 容器物理隔离 |
| `subprocess_executor.py` | 子进程隔离执行 |
| `network_filter.py` | 网络白名单过滤 |
| `path_guard.py` | 文件路径守卫 |
| `policy_manager.py` | 沙箱策略管理 |
| `resource_meter.py` | 资源用量计量 |
| `violation_publisher.py` | 违规事件发布（三路落点：日志/事件总线/告警） |

### 8.5 工具调用器（`integrations/tools/`）

- `local_function_tool_invoker.py`：本地函数工具调用器
- `sandboxed_tool_invoker.py`：沙箱化工具调用器（装饰器模式）

---

## 九、上下文引擎

> 路径：`src/orchestration_platform/context_engine/`

上下文引擎管理 Agent 执行时的上下文窗口——超限时按策略裁剪，保证不丢关键信息。

| 文件 | 职责 |
| --- | --- |
| `models.py` | Agent 执行上下文结构化模型（系统提示/历史对话/工具结果/共享状态引用） |
| `context_manager.py` | 统一管理上下文组装、更新与裁剪，超限时调用窗口策略 |
| `window_strategies.py` | **4 种窗口策略**：滑动窗口 / 摘要压缩 / 重要性排序 / 结构化无损裁剪（G2） |
| `pruning_records.py` | 无损裁剪记录（区块标识/原因/内容摘要哈希/时间戳，仅追加语义） |
| `semantic_cache.py` | 基于嵌入向量的相似查询缓存，余弦相似度匹配，预期节省 60-95% Token |
| `context_persistence.py` | 上下文检查点持久化与跨节点传递（继承/隔离两种模式） |

**G2 结构化无损裁剪**：只裁剪语义上已冗余的三类结构单元（重复段落/已消费区块/冗余字段），
每步记录 PruningRecord 可追溯，绝不静默丢内容。

---

## 十、插件 SDK 与 MCP 工具市场

### 10.1 插件节点 SDK（`plugin_sdk/`）

第三方开发者可用 SDK 开发自定义节点，不修改平台核心：

| 文件 | 职责 |
| --- | --- |
| `plugin_node_base.py` | 第三方节点开发基类与生命周期契约（setup/execute/teardown） |
| `models.py` | 插件节点 manifest 与注册信息等领域模型 |
| `manifest_validator.py` | 校验插件节点 manifest 格式与完整性 |
| `package_validator.py` | 校验插件节点包目录结构与内容一致性 |
| `debug_runner.py` | 命令行入口，不启动完整平台即可调试节点 |

### 10.2 插件节点加载（`engine/plugin_nodes/`）

- `plugin_node_executor.py`：实现 NodeExecutor Protocol，登记 NodeType.PLUGIN
- `plugin_node_loader.py`：动态导入插件节点包、manifest 校验、实例池管理

### 10.3 MCP 工具市场（`mcp_tool_market/`）

| 文件/子模块 | 职责 |
| --- | --- |
| `tool_market.py` | 工具市场注册中心，维护已注册工具元数据声明、版本与权限标注 |
| `version_manager.py` | 同一工具标识的多版本共存管理 |
| `permission_checker.py` | 校验调用方是否具备工具所需权限（READONLY/READWRITE/ADMIN） |
| **contribution/** | 第三方贡献：提交/流转/发布协调、HMAC/RSA 签名校验、7 态状态机、ast 危险 API 静态扫描 |
| **persistence/** | 工具市场仓储（内存/本地文件/Postgres 三后端） |

---

## 十一、持久化层

> 路径：`src/orchestration_platform/persistence/`

| 文件 | 职责 |
| --- | --- |
| `abstractions/repositories.py` | 三类仓储 Protocol：IWorkflowRepository / ICheckpointRepository / IEventRepository（异步接口） |
| **local_file/** | 本地文件实现：工作流定义（JSON，按 definition_id + version 组织，保留全历史版本）、检查点、事件 |
| **postgres/** | PostgreSQL 实现：工作流定义、检查点、事件 |
| **postgres/schema_migrations/** | SQL 迁移脚本：v1_1_phase2_enhancement.sql + rollback、v1_2_phase3_governance.sql + rollback |

**设计纪律**：仓储接口统一为 Protocol（异步），实现按配置注入。
本地文件实现用于开发/测试，Postgres 实现用于生产。

---

## 十二、可观测性

> 路径：`src/orchestration_platform/observability/`

| 文件 | 职责 |
| --- | --- |
| `metrics_collector.py` | 采集实例执行数、成功数、失败数、平均耗时、节点失败分布（线程安全），供 `/metrics` 接口 |
| `structured_logger.py` | 结构化日志，贯穿统一 trace_id，含实例/节点/轮次/耗时/结果 |
| `governance_metrics.py` | 治理可观测指标：鉴权/计量/成本/预算/审计/调度/资源池七类指标 |
| **agent_performance/**（G3） | Agent 绩效旁路记录与查询（见第十六章 4.3 节） |

---

## 十三、API 层：路由全览与条件注册

> 路径：`src/orchestration_platform/api/`

### 13.1 顶层路由（`api/routers/`，9 个路由器）

| 路由器 | Tag | 主要端点 |
| --- | --- | --- |
| `workflow_definition_router.py` | 工作流定义 | `POST /workflows/validate`、`POST /workflows`、`GET /workflows/{id}` |
| `execution_instance_router.py` | 执行实例 | `POST /instances`、`GET /instances/{id}`、`GET /instances/{id}/events`、`GET /instances/{id}/checkpoints` |
| `rollback_replay_router.py` | 回滚/重放/审核 | `POST /instances/{id}/rollback`、`POST /instances/{id}/replay`、`POST /instances/{id}/review` |
| `realtime_subscription_router.py` | 实时数据订阅 | `POST /subscriptions`、`GET /subscriptions`、`POST /{id}/pause`、`POST /{id}/resume`、`DELETE /{id}` |
| `context_query_router.py` | 上下文查询 | `GET /instances/{id}/context` |
| `mcp_server_router.py` | MCP 服务端 | `GET /mcp/server/tools` |
| `nl_orchestration_router.py` | 自然语言编排 | `POST /nl_orchestration/create`、`POST /nl_orchestration/modify` |
| `plugin_node_router.py` | 插件节点管理 | `GET /plugin/nodes`、`POST /plugin/nodes/install` |
| `trigger_template_router.py` | 触发器模板 | `GET /realtime-subscriptions/templates` |

### 13.2 治理路由（`api/routers/governance/`，12 个路由器）

| 路由器 | Tag | 说明 |
| --- | --- | --- |
| `governance_router.py` | 治理 | 租户/鉴权/用量/成本/预算/审计/调度（核心治理端点） |
| `abac_router.py` | ABAC 策略管理 | 策略 CRUD |
| `agent_discovery_router.py` | Agent 发现 | 注册/查询/搜索/重建索引 |
| `agent_performance_router.py` | Agent 绩效聚合 | `GET /governance/agent-performance` |
| `cache_router.py` | 缓存管理 | 状态/健康检查 |
| `contribution_router.py` | 贡献市场管理 | 提交/流转/重扫描/历史 |
| `enterprise_connector_router.py` | 企业连接器 | CRUD + 凭证 + 测试连接 |
| `evaluation_router.py` | Agent 效能评估 | 场景集/评估任务/基线/回归（11 个端点） |
| `event_bus_router.py` | 事件总线管理 | 状态/健康检查 |
| `sandbox_router.py` | 沙箱管理 | 策略/违规/状态 |
| `sso_router.py` | SSO 企业身份 | IdP 配置/发起/回调 |
| `tombstone_router.py` | 墓碑与被遗忘权 | 标记/遗忘/查询/删除 |

### 13.3 条件注册逻辑（`application_factory.py`）

`create_app()` 中路由注册分两类：

**无条件注册**（始终启用）：6 个基础路由 + governance_router + cache_router + event_bus_router

**条件注册**（受 settings 开关控制）：

| 开关 | 路由 |
| --- | --- |
| `nl_orchestration_enabled` | nl_orchestration_router |
| `abac_enabled` | abac_router |
| `tombstone_enabled` | tombstone_router |
| `sandbox_enabled` | sandbox_router |
| `plugin_sdk_enabled` | plugin_node_router |
| `node_contribution_enabled` | contribution_router |
| `sso_enabled` | sso_router |
| `enterprise_connector_enabled` | enterprise_connector_router |
| `evaluation_enabled` | evaluation_router |
| `agent_discovery_enabled` | agent_discovery_router |
| `trigger_template_enabled` | trigger_template_router |
| `agent_performance_enabled` | agent_performance_router |

**异常处理器**：`WorkflowValidationError→422`、`InstanceStateConflictError→409`、`ResourceNotFoundError→404`、`DomainError→500`

**健康检查**：`GET /healthz`、`GET /metrics`

---

## 十四、前端架构

> 路径：`web/src/`

技术栈：React 18 + TypeScript + React Flow 11 + Vite 5 + Vitest

`App.tsx` 管理"画布 / 管理后台"双视图切换。

### 14.1 画布区（`canvas/`）

| 组件 | 职责 |
| --- | --- |
| `WorkflowCanvas.tsx` | 基于 React Flow 的画布容器 |
| `NodePalette.tsx` | 节点面板（拖拽创建） |
| `NodeConfigurationPanel.tsx` | 节点配置面板 |
| `ConnectionValidator.ts` | 连线校验（类型兼容性） |
| `nodes/`（8 个 NodeView） | 五类基础节点 + 数据转换/动态注入/子工作流节点渲染 |

### 14.2 工作流定义双向转换（`workflow-definition/`）

- `canvasToYamlExporter.ts`：画布 → YAML
- `yamlToCanvasImporter.ts`：YAML → 画布
- `definitionTypes.ts`：定义类型

### 14.3 执行可视化（`execution-visualization/`）

- `ExecutionEventStreamClient.ts`：执行事件流 SSE 客户端
- `NodeExecutionStatusOverlay.tsx`：节点状态叠加层

### 14.4 管理后台（`admin/AdminConsole.tsx`）

管理后台根组件，聚合全部治理面板，侧边栏导航：

| 面板 | 路径 | 职责 |
| --- | --- | --- |
| GovernanceDashboard | `governance/` | 治理仪表盘 |
| AbacPolicyPanel | `governance/` | ABAC 策略管理 |
| TombstonePanel | `governance/` | 墓碑管理 |
| NLOrchestrationPanel | `nl_orchestration/` | 自然语言编排 |
| SandboxPolicyPanel | `sandbox/` | 沙箱策略管理 |
| SsoSettingsPanel | `sso/` | SSO 配置 |
| EvaluationDashboard | `evaluation/` | 评估仪表盘 |
| AgentDiscoveryPanel | `agent_discovery/` | Agent 发现 |
| AgentPerformancePanel | `agent_performance/` | Agent 绩效（G3） |
| PluginNodeManager | `plugin_nodes/` | 插件节点管理 |
| ContributionSubmitWizard | `market/` | 贡献提交向导 |
| ContributionReviewBoard | `market/` | 贡献审核看板 |
| EnterpriseConnectorPanel | `enterprise_connectors/` | 企业连接器 |
| ExampleLibraryPanel | `example-library/` | 范例库面板 |

---

## 十五、八种编排模式（按范例学习路径）

> 路径：`examples/`（12 个范例目录）

建议初学者按以下顺序阅读范例（每个范例目录内均有 YAML 与说明）：

### 1. 顺序流水线（examples/sequential_pipeline/）
最小编排链路：trigger → data_source → agent → tool。
从这里理解"节点按边连接的顺序执行，输出经共享状态传递"。

### 2. 并行执行（examples/parallel_execution/）
`control_flow(parallel_join)` 把多个 Agent 并行分派再汇聚。
理解并行度上限与失败策略（fast_fail / wait_all）。

### 3. 监督者路由（examples/supervisor_routing/）
监督者 Agent 先输出路由决策，`conditional_branch` 按决策把任务分发给财务或运营专家。
**这是理解 G1 监督者编排器的预备课**：路由决策是确定性的（同一输入同一输出）。

### 4. 层级委派（examples/hierarchical_delegation/）
主定义通过 `subworkflow` 节点嵌套调用组长子定义。
理解"工作流定义可以像函数一样被复用"。

### 5. 反思迭代（examples/reflection_iteration/）
评审 Agent 打分，`loop` 循环迭代直到达标或达最大次数。
理解防死循环约束（loop_max_iterations）。

### 6. 条件循环组合（examples/conditional_loop_pipeline/）
循环 + 条件分支组合使用。已在范例库服务登记，可直接 `load_example` 加载。

### 7. 数据工程链（examples/data_engineering/ 与 data_transform_pipeline/）
大数据连接器（Hologres 读取、Kafka 写出）+ 数据转换 + Agent 分析。
data_transform_pipeline 展示无 Agent 的纯数据链。

### 8. 实时订阅（examples/realtime_subscription/）
事件到达 → 过滤规则匹配 → 自动启动工作流。
**这是理解 G6 触发器模板的预备课**：订阅 = 数据源 + 过滤规则 + 目标工作流。

### 9. 数据科学双范例（examples/feature_engineering/ 与 drift_detection/，G4 新增）
特征工程：上游数据 → 清洗映射 → 特征工程 Agent → 产物落盘。
漂移检测：基线存在性条件分支（缺失不误报）→ 漂移 Agent → 事件发布 + 告警联动。

### 其他范例
- `subworkflow_nesting/`：最小子工作流复用
- `data_transform_pipeline/`：纯数据转换（无 Agent）

---

## 十六、第五次偏差修正（G1~G8）

以下五项能力全部**默认关闭**（配置开关 False），开启后才改变平台行为。

### 16.1 G1 监督者编排器（engine/supervisor_orchestration/）

问题：既有层级委派靠"预写的子工作流定义"表达层级，缺一层**运行时的确定性编排**。

方案：新增 SUPERVISOR 节点类型 + 四个核心组件——

- **路由决策表**（route_decision_table.py）：YAML 声明"关键词 → 子编排器"映射，
  按 priority 降序 + 声明顺序稳定排序后逐条匹配。
  **同一输入永远得到同一决策**（确定性）。
- **状态存储**（orchestration_state_store.py）：每个决策点落检查点
  （内存/本地文件/Postgres 三后端可插拔）。中断后从快照续跑，
  快照损坏则明确终止，**不产生孤儿执行**。
- **子编排器**（sub_orchestrator.py）：调度领域专家集群，失败按
  "重试 N 次 → 标记失败 → 携带原因"确定性处置链处理。
- **逐级聚合器**（finding_aggregator.py）：去重（哈希相同合并）→
  限量（每子编排 top-K 条）→ 附来源（禁止匿名汇聚）。
  Supervisor 接收信息量 = 直接下级数 × K，**与全体 Agent 总数解耦**（线性增长）。

从范例学：examples/supervisor_routing/ 演示了监督者+条件分支的经典形态。

### 16.2 G2 结构化无损裁剪（context_engine/）

问题：上下文超限时既有策略是有损的（截断/摘要）。

方案：新增 STRUCTURAL_LOSSLESS 策略——只裁剪**语义上已冗余**的三类结构单元：
重复段落（保留首份）、已消费区块（结果已写入后续状态）、冗余字段。
每一步裁剪都记录 PruningRecord（原因+摘要指纹），可追溯可审计。
无候选可裁时零移除并给出提示，**绝不静默丢内容**。

### 16.3 G3 Agent 绩效度量（observability/agent_performance/）

问题：平台只有全局指标，看不到"单个 Agent 干得怎么样"。

方案：**订阅式旁路喂数**——记录器订阅既有执行事件通道，从事件载荷中
提取 agent_id/成功与否/耗时/Token/质量评分，写入绩效仓储（内存环形窗口
或 Postgres），一次聚合查询返回四维指标（成功率/平均耗时/Token 总量/评分均值）。

关键设计纪律：**不改既有 MetricsCollector/GovernanceMetrics 的任何 record_* 方法**
（旁路原则）——新增能力不能污染既有采集路径。
前端查询面板见管理后台「Agent 绩效聚合」菜单。

### 16.4 G5 审计内容哈希（governance/audit/）

问题：审计哈希链只能证明"链条完整"，不能证明"载荷未被篡改"。

方案：开启 audit_content_hash_enabled 后，新条目写入时对 input/output
载荷计算 SHA-256 字段级哈希；校验器新增 verify_content_integrity 方法定位
被篡改的条目与字段。**entry_hash 链值与修正前逐字节一致**（向后兼容红线），
旧条目校验自动跳过、新旧条目混布链条整体校验不中断。

### 16.5 G6 触发器模板（engine/realtime_subscription/trigger_templates/）

问题：用户想配"均值偏离基线""连续 N 次超阈值"这类**统计语义**的触发条件，
但既有过滤规则只能做单事件比较。

方案：三类预置模板，三分层实现——

- **注册表**：参数校验（如方差倍数必须 >1.0），非法拒绝、不产生半成品；
- **展开器**：订阅创建时把模板展开为「既有 EventFilterRule 粗筛规则 + 有状态评估器」
  两部分——粗筛规则进入既有过滤路径（**EventFilterRule 本身零修改**）；
- **评估器**：滑动窗口（窗口均值偏离基线超容差）/阈值持续突破（连续 N 次
  才触发一次，计数中断即清零防抖）/方差突变（本窗方差 > 前窗方差×倍数）。
  评估器按订阅维度隔离，暂停/删除订阅时同步销毁防泄漏。

从范例学：examples/drift_detection/drift_alert_subscription.example.json
演示了漂移分数连续 3 次突破阈值时触发告警的完整订阅配置。

### 16.6 G4 数据科学范例与 G7/G8 工程修正

- G4：新增特征工程/漂移检测两个范例（examples/ 共 12 个），
  漂移告警经 G6 模板订阅联动；Agent 绩效面板聚合进 AdminConsole。
- G7：examples/README.md 重写，文档条目数 = 实际范例数 = 12。
- G8：pyproject.toml 声明修正为 `requires-python = ">=3.10"`，
  与实测运行环境一致；全库 3.10 禁用特性静态扫描零命中。

---

## 十七、四条工程约束的设计意图

### 17.1 LangGraph 抽象隔离

平台选型 LangGraph 做图编排，但**业务代码禁止直接 import langgraph**。
所有图编译、检查点、中断恢复经平台自有的抽象隔离层（engine/adapter）封装。
原因有二：一是编排语义要可预测可审计（确定性控制路径），二是底层编排框架
可替换而不波及业务层。测试中有专门的静态断言扫描新增包的 import 树。

### 17.2 Python 3.10 兼容

运行环境实测 3.10.20，因此全库禁用四类 3.11+/3.12+ 独有特性：
`enum.StrEnum`（改用 `(str, Enum)` 混入）、`typing.Self`（改用字符串前向引用）、
`asyncio.TaskGroup`（改用 gather）、PEP 695 `type` 语句（改用
typing_extensions.TypeAliasType）。这些不是风格偏好，而是**能跑与不能跑**的
区别——测试里用正则对 src/ 全库扫描，命中即失败。

### 17.3 配置开关默认关闭

G1~G6 每项新能力都有一个 `xxx_enabled` 开关，默认 False：
- 开关关闭时，新代码路径完全不生效（路由不注册返回 404、执行器不注册、
  主循环不评估模板），**平台行为与修正前逐字节一致**；
- settings.py 与 .env.example 键名一一对应双同步，.env.example 只放
  占位符默认值，真实密钥只进 .env（被 .gitignore 忽略）。

这是企业级平台的"零惊扰升级"原则：部署新版不等于启用新功能。

### 17.4 重依赖延迟导入 + 降级

asyncpg/Redis/Kafka 等重依赖一律在函数内延迟导入，不可用时降级到
纯进程内实现并在 data_source/is_degraded 等标识中暴露降级来源。
例如 Postgres 绩效仓储连接失败自动切换内存环形窗口（data_source 变为
`in_memory_degraded`），漂移事件在 Kafka 不可用时经内存降级通道照常发布。
初学者记住一句话：**沙箱里能跑的，生产里才能跑得更好；沙箱里跑不了的，
也不该让进程死掉。**

---

## 十八、测试体系

> 路径：`tests/`

### 18.1 测试组织

| 目录 | 文件数 | 说明 |
| --- | --- | --- |
| `tests/unit/` | 11 个 | 单元测试：YAML 解析、拓扑校验、控制流执行、调度确定性、仓储实现、语义缓存、实时订阅、MCP 服务端、MCP 工具市场、大数据连接器、框架依赖隔离 |
| `tests/integration/` | 6 个 | 集成测试：检查点恢复、条件分支执行、循环执行、回滚重放、MCP 工具集成、范例可运行性 |
| `tests/governance/` | 12 个 | 治理测试：RBAC 多租户、审计合规、计量计费、调度资源、墓碑 GDPR、ABAC 扩展、NL 编排、事件总线、Redis 缓存、兼容性 |
| `tests/phase4/` | 11 个 | 生态测试：插件 SDK、插件节点集成、沙箱单元/集成、SSO、企业连接器、评估框架、Agent 发现、贡献流程、生态兼容性、生态 E2E |
| `tests/verify_*.py` | 17 个 | 验收脚本：阶段一 10 个 + 阶段五 7 个（G1~G8） |
| `tests/fixtures/` | — | 合法定义 5 个 + 非法定义 10 个（环路/悬空边/写操作/过大/...） |

### 18.2 测试范式

本平台测试采用 **unittest 风格 + `_run_async` 辅助函数**模式：

```python
import unittest

class TestXxx(unittest.TestCase):
    def test_something(self):
        result = _run_async(some_async_coro())
        self.assertTrue(result.succeeded)
```

`_run_async` 辅助函数创建事件循环并驱动协程执行。
每条断言标注验收编号（如 `# A1.2 确定性路由`），便于追溯需求规格。

### 18.3 运行测试

```bash
# 设置 PYTHONPATH
export PYTHONPATH=src

# 运行单个验收脚本
python -m unittest tests.verify_g1_supervisor_orchestration

# 运行全部 G 系列验收（分批避免超时）
python -m unittest tests.verify_g1_supervisor_orchestration tests.verify_g2_structural_lossless_pruning
python -m unittest tests.verify_g3_agent_performance tests.verify_g4_data_science_examples
python -m unittest tests.verify_g5_audit_content_hash tests.verify_g6_trigger_templates
python -m unittest tests.verify_g7_g8_docs_and_compat
```

---

## 十九、给初学者的下一步

1. **跑通最小范例**：阅读 examples/sequential_pipeline/ 的 YAML，
   理解 trigger → data_source → agent → tool 的执行流程；
2. **理解调度器**：阅读 `engine/scheduler.py`，关注拓扑序执行、
   条件分支求值、循环控制三个核心机制；
3. **理解节点执行器协议**：阅读 `engine/nodes/base.py`，
   所有节点执行器都实现 `NodeExecutor` 协议——这就是扩展点；
4. **理解领域层**：浏览 `domain/models/enums.py` 和 `domain/errors.py`，
   建立对节点类型、状态、错误码的全局认知；
5. **理解配置体系**：浏览 `configuration/settings.py`，
   理解"所有开关默认 False"的零惊扰升级原则；
6. **跑通监督者范例**：阅读 examples/supervisor_routing/ 的 YAML，
   对照第十六章 16.1 节理解确定性路由；
7. **理解触发器模板**：把 examples/drift_detection/drift_alert_subscription.example.json
   通过 `POST /api/v1/subscriptions` 创建订阅，理解模板展开机制；
8. **打开管理后台**：启动前端，打开「Agent 绩效聚合」面板，
   发起一次查询，理解四维聚合口径；
9. **学习测试范式**：阅读 `tests/verify_g1_supervisor_orchestration.py`，
   学习 unittest + `_run_async` + 验收编号标注的测试风格；
10. **尝试扩展**：参照 `plugin_sdk/` 开发一个自定义节点，
    经 `plugin_node_loader` 加载后在工作流中使用。

---

## 附录：项目统计

| 维度 | 数值 |
| --- | --- |
| 源文件总数 | ~500 个 |
| Python 文件 | ~439 个 / ~32000 行 |
| TypeScript 文件 | ~61 个 / ~3400 行 |
| 范例数 | 12 个 |
| 枚举类 | 30+ 个 |
| 错误码 | 80+ 个 |
| 配置键 | 90+ 个 |
| API 路由 | 26 个（14 顶层 + 12 治理） |
| 节点类型 | 11 种 |
| 测试文件 | 57+ 个（含 17 个验收脚本） |
| 开发阶段 | 5 个（phase1-4 + phase5 偏差修正） |

---

*交付登记：本文档为技术讲解文档（全量版），覆盖 phase1-4 全部模块 + phase5 偏差修正（G1~G8）。
编写时点：2026-09-26，G1~G8 全部验收通过（142 tests OK）。*
