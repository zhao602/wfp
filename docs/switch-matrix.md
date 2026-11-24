# 开关矩阵（docs/switch-matrix.md）

> 数据源：`orchestration_platform/configuration/settings.py` `Settings.model_fields`
> 中全部 `bool` 字段（**54 个**，2026-11-23 实测；roadmap M4 B10 design D8）。
> 一致性守护：`tests/unit/test_switch_matrix_consistency.py`——本表主表行名
> 集合 == Settings bool 字段集合；默认值列与字段默认值逐一比对。
> 消费层/裁剪点/收敛位置均经全仓 grep 实证（含 `api/application_factory.py`
> 28 处装配门控行号）。

## 阅读说明

- **默认**：`True` 开关关闭即裁剪功能面；`False` 开关默认关闭态与历史基线
  零行为变化（HC-07/GK-11 纪律）。
- **消费层**：运行期读取该开关的代码域；「经 EngineSettings 注入」= M4 B10
  后引擎域经 `engine/abstractions/engine_config.py` 构造注入消费，引擎组件
  不再 import `configuration`（守护：`tests/unit/test_engine_import_isolation.py`
  + `scripts/check_engine_imports.py`，CI 独立步）。
- **收敛位置**：开关判定的单点（装配门控/拦截器/执行器）；多点位以
  `文件:行` 列出。
- **未接线开关**：声明存在但全仓无运行期读取（详见各行备注与文末登记），
  翻转无任何行为变化——**登记欠账**，出口记录收口。

## 54 开关全表

| 开关名 | 默认 | settings.py 行号 | 类别 | 消费层 | 裁剪点（关闭态行为） | 收敛位置 | 备注 |
|---|---|---|---|---|---|---|---|
| langgraph_runtime_enabled | False | 95 | LangGraph 适配 | API 装配 | 第二运行时不启用（默认零变化） | api/application_factory.py:236 | HC-07；True 且依赖缺失降级内置引擎并告警 |
| mcp_server_enabled | True | 105 | MCP 工具 | 无 | — | — | 未接线：全仓零读取（平台 MCP 仅客户端侧）；登记欠账 |
| llm_retry_enabled | False | 128 | LLM 推理重试 | 集成层 | infer 行为逐字节等价（GK-11） | integrations/llm/factory.py | B2-C4；仅超时/连接失败重试 |
| agent_market_enabled | False | 144 | 市场 | API 装配/路由 | 市场路由不注册（404） | api/application_factory.py:534 | **OEM**（module_id=agent_market，见下节） |
| tool_market_enabled | False | 150 | 市场 | API 装配/路由 | 市场路由不注册（404） | api/application_factory.py:556 | **OEM**（module_id=tool_market） |
| agent_market_dev_enabled | False | 159 | 市场 | API 装配/路由 | 用户自主开发路由不注册（404） | api/application_factory.py:545 | OP-9/DC-07/INV-DEV-10 |
| log_rotation_enabled | True | 189 | 运行日志 | 可观测性 | 轮转停用 | observability/run_log/sink.py | design 2.2.3 |
| log_redaction_enabled | True | 198 | 运行日志 | 可观测性 | 仍保留字段名黑名单兜底（红线不可全关） | observability/run_log/redactor.py | — |
| log_async_enabled | True | 201 | 运行日志 | 可观测性 | 异步队列停用（同步写） | observability/run_log/sink.py | — |
| scheduler_batch_mode | True | 213 | 引擎调度 | 无 | — | — | 未接线：全仓零读取；登记欠账 |
| checkpoint_compression_enabled | True | 215 | 引擎调度 | 无 | — | — | 未接线：全仓零读取；登记欠账 |
| rbac_enabled | True | 225 | 治理-RBAC | 治理层 | 鉴权拦截器不装配（降级模式） | governance/governance_factory.py；interceptors/auth_interceptor.py | — |
| tenant_isolation_enabled | True | 230 | 治理-租户 | 治理层 | 租户隔离拦截器不装配 | governance/interceptors/tenant_isolation_interceptor.py | — |
| metering_enabled | True | 236 | 治理-计量 | API 装配 + 治理层 | set_usage_recorder 不装配、用量查询路由旁路 | api/application_factory.py:818 | M4 B10 后引擎侧经 usage_recorder ctor 注入，engine 不读此开关 |
| budget_fail_closed | True | 243 | 治理-预算 | 治理层 | False=超限仅告警不拒绝 | governance/billing/budget_guard.py | — |
| audit_enabled | True | 248 | 治理-审计 | 治理层 | 审计拦截器不装配 | governance/governance_factory.py；interceptors/audit_interceptor.py | — |
| audit_content_hash_enabled | False | 255 | 治理-审计 | 治理层 | 不产 input_hash/output_hash（旧行为） | governance/audit/audit_logger.py | G5/T208 |
| structural_lossless_pruning_enabled | False | 257 | 引擎-Agent 上下文 | 引擎域（经 EngineSettings 注入）+ 上下文引擎 | 走既有滑动窗口兜底 | application/engine_wiring.py → engine/abstractions/engine_config.py；消费 engine/nodes/agent_node_executor.py:328 | M4 B10 注入化；G2/T213 |
| supervisor_orchestration_enabled | False | 259 | 引擎-监督者 | 引擎域（设计预留） | SUPERVISOR 按未知节点类型拦截 | engine/node_executor_registry.py:99（register_supervisor_executor） | 未接线：注册方法零调用方、开关零读取——True 亦不启用；登记欠账 |
| trigger_template_enabled | False | 264 | 触发器模板 | API 装配 + 引擎订阅 | 模板路由不注册、主循环不评估模板 | api/application_factory.py:469；engine/realtime_subscription/subscription_manager.py | G6/T225 |
| agent_performance_enabled | False | 266 | 治理-绩效 | API 装配/路由 | 记录器不挂载、路由不注册 | api/application_factory.py:487 | G3/T229 |
| global_scheduler_enabled | True | 269 | 治理-全局调度 | 治理层 | 全局调度器不装配 | governance/scheduler/global_scheduler.py；interceptors/global_scheduler_interceptor.py | — |
| scheduler_preemption_enabled | True | 273 | 治理-全局调度 | 治理层 | 抢占器不装配 | governance/scheduler/preemptor.py | — |
| nl_orchestration_enabled | False | 282 | NL 编排 | API 装配/路由 | 入口路由不注册 | api/application_factory.py:343 | — |
| abac_enabled | False | 289 | 治理-ABAC | API 装配 | ABAC 策略引擎不装配 | api/application_factory.py:353 | — |
| tombstone_enabled | True | 294 | 治理-墓碑 | API 装配 | 审计删除走物理删除 | api/application_factory.py:363 | — |
| redis_enabled | False | 298 | 缓存 | 治理层 | 降级内存缓存（空 URL 同） | governance/cache/cache_manager.py | — |
| kafka_enabled | False | 306 | 事件总线 | API 装配 + 治理层 | 不启用 Kafka 总线（内存总线兜底） | api/application_factory.py:650；governance/event_bus/event_bus_manager.py | 空地址同样降级内存总线 |
| event_bus_publish_enabled | False | 315 | 事件总线 | API 装配 | 领域事件发布旁路 | api/application_factory.py:626/:658 | CHG-59/DC-21 |
| audit_event_subscription_enabled | False | 317 | 事件总线 | API 装配 | 审计事件订阅不装配 | api/application_factory.py:627/:671 | CHG-59/DC-22 |
| schedule_scanner_enabled | False | 321 | Cron 触发 | API 装配 | 扫描器不启动 | api/application_factory.py:873/:887 | CHG-59/DC-28/29 |
| trigger_schedule_api_enabled | False | 323 | Cron 触发 | API 装配/路由 | 管理路由不注册 | api/application_factory.py:871 | — |
| event_export_enabled | False | 327 | 事件出口 | API 装配 | 出口装配零挂载（worker 侧同 gate 同一装配体） | api/application_factory.py:628/:694 | roadmap B3；内存态 DLQ |
| event_export_api_enabled | False | 328 | 事件出口 | API 装配/路由 | 管理路由不注册（订阅/死信/SSE） | api/application_factory.py:751 | — |
| sandbox_enabled | False | 338 | 沙箱 | API 装配 + 集成层 | 沙箱路由不注册、invoker 不走沙箱 | api/application_factory.py:391；integrations/tools/sandboxed_tool_invoker.py | T131 |
| sandbox_network_default_deny | True | 345 | 沙箱 | 无 | — | — | 未接线：全仓零读取；登记欠账 |
| sandbox_subprocess_mode | False | 346 | 沙箱 | API 路由 + 集成层 | 走逻辑隔离（默认） | integrations/sandbox/subprocess_executor.py | — |
| llm_node_enabled | False | 353 | 引擎-LLM 节点 | 引擎域（经 EngineSettings 注入） | 执行器返回 LLM_NODE_DISABLED | application/engine_wiring.py → engine/nodes/llm_node_executor.py | M4 B10 注入化；CHG-02 |
| code_node_enabled | False | 354 | 引擎-代码节点 | 引擎域（经 EngineSettings 注入） | 执行器返回 CODE_NODE_DISABLED | application/engine_wiring.py → engine/nodes/code_node_executor.py | M4 B10 注入化；CHG-02 |
| plugin_sdk_enabled | False | 360 | 插件 SDK | API 装配 + 引擎域（经 EngineSettings 注入） | 插件路由不注册、loader 不加载 | api/application_factory.py:401；application/engine_wiring.py → engine/plugin_nodes/plugin_node_loader.py | M4 B10 注入化；T149 |
| node_contribution_enabled | False | 366 | 节点贡献 | API 装配/路由 | 贡献路由不注册 | api/application_factory.py:411 | T153 |
| sso_enabled | False | 373 | SSO | API 装配 + 治理层 | SSO 不装配 | api/application_factory.py:421；governance/sso/sso_service.py | T162 |
| enterprise_connector_enabled | False | 380 | 企业连接器 | API 装配 | 连接器不装配 | api/application_factory.py:431 | T174 |
| evaluation_enabled | False | 385 | 评估框架 | API 装配/路由 | 评估路由不注册 | api/application_factory.py:448 | T182 |
| evaluation_llm_scoring_enabled | False | 386 | 评估框架 | 治理层 | 纯规则评分（默认） | governance/evaluation/evaluation_runner.py | — |
| agent_discovery_enabled | False | 393 | Agent 发现 | API 装配/路由 | 语义索引路由不注册 | api/application_factory.py:458 | T192 |
| local_vector_database_enabled | False | 409 | 向量-本地库 | 集成层 | local_backend 委托分支不触达（INV-L05） | integrations/local_vector_database/config.py；integrations/vector_knowledge_base/local_backend.py | CHG-L16/DC-07 |
| vector_real_embedding_enabled | False | 419 | 向量-诚实化 | 集成层 | 写入行为逐字节等价 | integrations/vector_kb_management/ingestion.py | B2-C5/DC-B2-05；开启时 hash_fallback 新写入以可辨识错误拒绝 |
| vector_kb_management_enabled | False | 425 | 向量-知识库管理 | API 装配/路由 + 引擎节点 | /api/v1/kb-management/** 404、不触达本地库 | api/application_factory.py:568；engine/nodes/vector_knowledge_base_node_executor.py | **OEM**（module_id=knowledge_base） |
| vector_kb_annotation_enabled | False | 440 | 向量-标注 | API 装配/路由 + 领域模型 | 标注端点 404、不产 annotations | api/application_factory.py:580；domain/models/node_configurations.py | CHG-12/DC-02/INV-07 |
| data_transform_custom_code_enabled | False | 446 | 引擎-数据转换 | 引擎域（仅 docstring 引用） | 运行期恒不执行用户脚本（DC-08 不变） | engine/nodes/data_transform_ops/handlers_dev.py:94；custom_code_guard.py（静态校验） | 未接线：开关零运行期读取，custom_code 录入恒登记+静态校验；登记欠账 |
| auth_token_issuance_enabled | False | 474 | 安全-JWT | API 装配/路由 | POST /api/v1/auth/token 404 | api/application_factory.py:592 | CHG-13 |
| rbac_bootstrap_enabled | False | 479 | 安全-RBAC 初始化 | API 装配 | 启动期幂等初始化不执行 | api/application_factory.py:604；governance/rbac/bootstrap.py | CHG-14 |
| workflow_step_guard_enabled | False | 492 | 引擎-步数护栏 | 引擎域（经 EngineSettings 注入） | 行为与基线完全一致（HC-07） | application/engine_wiring.py → engine/scheduler.py | M4 B10 注入化；CHG-01/04/08/DC-10 |

## OEM build-time 3 开关（单列）

> OEM 白标构建面（`scripts/build_oem.py` `MODULE_REGISTRY`，roadmap B5）：
> 每品牌按 `oem/brands/*.yaml` 的 modules 开关渲染 `backend.env`
> （部署时与基础 .env 合并，重启生效——settings `@lru_cache`）。
> 前端侧为 `VITE_OEM_*` 12 变量构建期烘焙（品牌色/产品名/禁用面板清单），
> 属前端产物面、不在本表 54 开关内。

| Settings 字段（主表行） | module_id | menu key | env flag（backend.env 行） | settings.py 行号 | 装配门控 |
|---|---|---|---|---|---|
| agent_market_enabled | agent_market | agent_market | AGENT_MARKET_ENABLED | 144 | api/application_factory.py:534 |
| tool_market_enabled | tool_market | tool_market | TOOL_MARKET_ENABLED | 150 | api/application_factory.py:556 |
| vector_kb_management_enabled | knowledge_base | knowledge_base | VECTOR_KB_MANAGEMENT_ENABLED | 425 | api/application_factory.py:568 |

## 未接线开关登记（欠账，出口记录收口）

| 开关名 | 形态 | 影响 |
|---|---|---|
| mcp_server_enabled | 声明零引用 | 翻转无行为变化 |
| scheduler_batch_mode | 声明零引用 | 翻转无行为变化 |
| checkpoint_compression_enabled | 声明零引用 | 翻转无行为变化 |
| sandbox_network_default_deny | 声明零引用 | 翻转无行为变化 |
| supervisor_orchestration_enabled | 注册方法零调用方（engine/node_executor_registry.py:99） | True 亦不启用，恒关闭态等价 |
| data_transform_custom_code_enabled | 仅 docstring 引用（handlers_dev.py:94） | 运行期行为由 DC-08 恒定约束，开关无效 |
