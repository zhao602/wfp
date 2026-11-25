# B 档技术债台账（tech-debt-ledger.md）

> 建档日期：2026-10-18（cleanup_dead_code 批次 4 产出）
> 数据来源：孤儿复扫双源对齐（`scan_orphans.py` 基线 58 / 复扫 62）+ spec 5.1.4 B 档表 + design 1.2.3 实测清单
> 维护约定：本台账是 B 档孤儿模块的唯一权威清单；后续清理须先更新本台账再动代码。

## 1. 台账目的与范围

- **B 档定义**：孤儿模块但与治理/集成功能关联，须逐项确认接线意图后方可清理（spec 术语表）。
- **用户裁决（2026-10-18）**：B 档本轮全部不动，建立本台账并逐文件打 `TODO(tech-debt)` 标记；下个迭代安排基于 APM 链路追踪的专项排查，确认无调用后分批清理。
- **与 A/C 档关系**：A 档 8 组已在本轮清理（verify×16、config.py、parallel_executor、checkpoint_compressor、workflow_compiler、空壳包×2、create_governance_components、bugs）；C1（langgraph 链）裁决保留并已固化定位注释（C1 链成员 workflow_engine.py 后经 M4 B10 移除，见 §3 注记）；C2（9 个 SDD 目录）已归档。
- **标记格式**：`# TODO(tech-debt): B档候选孤儿（族群 Bx），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md`（Python 3.10 无 `@Deprecated` 装饰器，统一注释形式）。

## 2. 用户裁决记录（2026-10-18）

| 裁决项 | 结论 |
|--------|------|
| C1 langgraph 孤儿链 | 保留；新增架构规范单测 `tests/unit/test_langgraph_isolation.py` + TODO 注释，排入下迭代重构 |
| C2 SDD 残留目录 | 归档至 `.codeartsdoer/specs/archived/` |
| A8 output/ 产物 | 对外交付物，保留；仅 `bugs` 临时文件清理 |
| B 档策略 | 全部不动；建台账 + 打 TODO 标记；下迭代 APM 链路追踪专项排查后分批清理 |
| F1~F3 附带发现 | 另立任务处理 |
| G0 基线 26 失败 | 接受基线继续清理（口径：通过数不减 + 失败集合不扩大不新增）；另立任务修复 |

## 3. 13+2 族群清单总表（56 个已标记文件）

| 族群 | 模块（相对 src/orchestration_platform/） | 标记 | 保留理由 / 待确认点 |
|------|------------------------------------------|------|---------------------|
| B1 治理拦截器族（5→2） | interceptors/{audit,budget,global_scheduler,metering}_interceptor.py、rbac/abac/abac_interceptor.py | 已标记（audit/budget/metering 三件已销项） | governance_factory 仅装配 auth/tenant_isolation/quota 三个；audit_enabled/metering_enabled 默认 True——须确认"已设计未接线"还是"已被替代"；**audit_interceptor 实测结论（2026-11-19，arch_review_v2_tasks_wave2 任务 5.6/CHG-70 补记）：全仓零实例化点——`AuditInterceptor` 类唯一引用为 `InterceptorChain` 构造器 Optional 槽位（interceptors/base.py:235，默认 None），生产审计走 P2 事件总线订阅路径（AuditEventSubscriber，DC-25），与 B1 既有拦截器互为独立通道——确认"已设计未接线"，维持待接线不销项（Wave2b F7/APM 专项复核）**；**B0/D3 销项更新（2026-11-21，roadmap B0）：audit/budget/metering 三拦截器已由 governance_factory.create_governance_chain 六槽装配接线为生产调用点（构造失败各自降级 None，链编排跳过该槽）——2026-11-19「维持待接线不销项」结论由本接线取代，拦截器通道与 P2 事件总线订阅通道并存不冲突；三文件首行销项声明在位，见第 4 章；global_scheduler/abac 两件维持待接线（装配点移交后续批次）** |
| B2 治理仓储族（19→10） | persistence/abstractions/{org_tree,role,tenant,token,token_usage,user}_repository.py ×6、local_file/local_file_{...} ×6、postgres/__init__.py + postgres_{...} ×7 | 已标记（role/user/tenant 三族 9 文件已销项） | RBAC 默认走内存 permission_registry；须确认是否属 storage_backend=postgres 路径交付物；**role/user/tenant 三族 9 文件已由 RBAC 落库接线销项（2026-11-18，arch_review_v2_tasks G3/CHG-06/CHG-14，见第 4 章）** |
| B3 租户管理（2） | tenant/{org_tree_manager,user_manager}.py | 已标记 | 与 B2 联动，一并确认 |
| B4 计费/缓存/调度辅助（3） | billing/alert_notifier.py、cache/cache_refresh_service.py、scheduler/models.py | 已标记 | 配置项（alert_channel_type 等）存在，须确认接线计划 |
| B5 SSO/NL 编排抽象（3） | sso/abstractions/__init__.py、sso/abstractions/identity_protocol_adapter.py、nl_orchestration/abstractions/nl_orchestration_service.py | 已标记 | 对应路由被开关守护且存在，抽象接口零引用须确认是否有替代实现 |
| B6 MCP 集成（4） | integrations/mcp/mcp_client.py、mcp/transports/{http,sse,stdio}_transport.py | 已标记 | mcp_server_enabled 默认 True 且 mcp_server_router 已注册——须确认客户端侧接入计划 |
| B7 沙箱（1） | integrations/sandbox/subprocess_executor.py | 已标记 | sandbox_subprocess_mode 配置存在，须确认接线计划 |
| B8 贡献市场持久化（4） | mcp_tool_market/contribution/abstractions/{__init__,contribution_repository,contribution_service}.py、persistence/postgres_tool_market_repository.py | 已标记 | node_contribution_enabled 默认 False（开关守护），仓储层未被路由引用 |
| B9 上下文引擎（1） | context_engine/context_persistence.py | 已标记 | 须确认是否属规划中的上下文持久化路径 |
| B10 触发器（1） | engine/realtime_subscription/kafka_event_listener.py | 已标记 | kafka_enabled 默认 False，开关关联模块 |
| B11 插件 SDK（3） | plugin_sdk/debug_runner.py、engine/plugin_nodes/abstractions/{__init__,node_package_loader}.py | 已标记 | debug_runner 可能为文档记载的开发者调试 CLI（docs/plugin_sdk_development_guide.md） |
| B12 YAML Schema（1） | workflow/yaml_schema/__init__.py（包目录，spec 原误记 .py 已修正） | 已标记 | 须确认是否为对外发布的 YAML Schema 契约（包内 workflow_definition_v1.schema.json 随台账记录） |
| B13 前端配置（0） | web/.codeartsdoer/（配置目录非源码） | **仅台账** | 前端 agent 独立工程上下文，非重复配置（2026-10-18 复核维持） |
| B14 企业连接器族（4，复扫新发现） | data_source_connectors/enterprise/abstractions/{__init__,credential_store}.py、enterprise/{erp,oa}_connector.py | 已标记 | spec 表外新发现；enterprise_connector_enabled 开关守护的连接器族子集——erp/oa 两连接器未被 registry 注册，须确认交付计划 |
| B15 verify 残留暴露族（4，批次 3 副作用；原 5） | application/execution_instance_service.py、context_engine/context_manager.py、integrations/data_source/readonly_data_source_client.py、observability/governance_metrics.py | 已标记 | 删除 16 个 verify_*.py 后暴露的真孤儿（此前仅被验证脚本引用）；execution_instance_service 疑似被 router 直接绕过的服务层、governance_metrics 疑似被重构遗留——须重点确认接线意图（structured_logger 已销项，见第 4 章） |
| C1 链成员（1） | engine/abstractions/workflow_engine.py | 已标记（族群记 C1） | langgraph 孤儿链三文件之一（IWorkflowEngine Protocol）；同链 langgraph_adapter.py 已有 C1 定位声明注释（不重复打标记）、langgraph_state_mapping.py 被 adapter 引用不属孤儿（**2026-11-23 M4 B10 销项**：Protocol 全仓零生产引用，文件删除；链余 adapter/state_mapping 两文件随双运行时保留） |

## 4. 复扫排除记录（不标记不清理）

| 模块 | 排除原因 |
|------|----------|
| persistence/postgres/schema_migrations/__init__.py | SR-106 保护（数据库结构迁移入口，独立执行路径） |
| engine/nodes/{agent,tool,trigger}_node_executor.py | **活代码**：execution_instance_router._build_executor_registry() 以 importlib 字符串动态导入注册（SR-004 动态导入保护；AST import 扫描盲区实证） |
| engine/adapter/langgraph_adapter.py | C1 保留锚点，已有定位声明注释（T3.3 固化，含 TODO(重构) 排期） |
| engine/adapter/langgraph_state_mapping.py | 被 langgraph_adapter.py 引用（非孤儿）；C1 链成员随链保留 |
| governance/persistence/{abstractions,local_file}/...audit_log_repository.py ×2 | 不在孤儿清单（被活跃代码引用），B2 边界项复核为活跃 |
| integrations/mcp/transports/{base,__init__}.py | 不在孤儿清单（被 connection_pool.py 等活跃代码引用），B6 边界项复核为活跃 |
| governance/nl_orchestration/abstractions/nl_intent_parser.py | 不在孤儿清单（被活跃实现引用），B5 边界项复核为活跃 |
| mcp_tool_market/contribution/abstractions/nl_intent_parser.py | 同上规则复核（活跃） |
| 各族群内活跃件 | B1 的 auth/quota/tenant_isolation/base.py、B3 的 token_manager/quota_checker 等、B4 的 budget_manager/cache_manager 等（不在孤儿清单，未标记） |
| observability/structured_logger.py | **销项（2026-11-04 补记）**：原 B15 成员；workflow_run_logging 轨已将其接线为生产调用点（observability/run_log/sink.py:29/159/210，design DC-01），文件首行销项声明在位 + 专属单测 tests/unit/test_structured_logger_log_record.py；标记移除合法，B 档待清理定位解除 |
| governance/persistence 三族 9 文件：{role,user,tenant}_repository.py（abstractions / local_file / postgres 三层 × 三族） | **销项（2026-11-18 补记）**：原 B2 族成员；arch_review_v2_tasks G3「RBAC 落库」接线为生产调用点——role 族经 governance_factory.create_governance_chain 构造注入 RbacPermissionChecker（CHG-06，checker 缓存未命中查库含组织继承）；user/tenant 族经 governance/rbac/bootstrap.py 幂等初始化调用（CHG-14，默认租户存在性确认 + 初始管理员绑定创建用户）；文件首行销项声明在位，标记移除合法，B 档待清理定位解除；回引处置主台账 docs/roadmap-dispositions-2026-11.md（CHG-40，两台账互相引用不合并） |
| governance/interceptors/{audit,budget,metering}_interceptor.py 三件 | **销项（2026-11-21 补记）**：原 B1 族成员；roadmap B0/D3「治理链断链修复」接线为生产调用点——governance_factory.create_governance_chain 六槽装配（audit/budget/metering 槽位，构造失败各自降级 None，链编排跳过该槽）；2026-11-19 audit_interceptor「全仓零实例化点、维持待接线不销项」结论由本接线取代（拦截器通道与 P2 事件总线订阅通道并存不冲突）；文件首行销项声明在位，标记移除合法，B 档待清理定位解除；回引处置主台账 docs/roadmap-dispositions-2026-11.md |

## 5. 下迭代 APM 链路追踪专项排查计划

1. **排查方法**：在沙箱/预发环境启用 APM（或轻量方案：为关键入口装配调用链埋点），运行全量集成场景（工作流执行/治理路由/工具市场/企业连接器），采集模块级真实调用数据。
2. **判定标准**：APM 链路数据中零调用的 B 档模块 → 进入清理候选；有调用的 → 补齐接线并从台账销项。
3. **清理流程**：按族群分批（建议顺序：B15 → B14 → B1~B12，B15/B14 证据最新鲜）；每批沿用本轮双门槛验证（沙箱静态 + 用户环境 pytest/装配冒烟）。
4. **责任移交**：本台账 + `scan_orphans.py` 口径脚本移交下迭代任务；清理前须先更新本台账销项。
5. **langgraph 链（C1）重构**：单独排期——补齐真实引擎接入或整链移除并同步更新隔离约束表述（详见 langgraph_adapter.py 头部 TODO(重构) 注释）。（进展：B6 双运行时已使 adapter 成为受 `langgraph_runtime_enabled` 开关守护的真实链路；C1 链成员 workflow_engine.py 已于 M4 B10 移除，2026-11-23，见 §3 注记。）

## 6. 与 spec 预估差异说明

- spec 预估：B 档约 47 个（13 族群，基于旧扫描口径）。
- 实际标记：**57 个**（15 族群 + C1 链成员 1 个）——后经 workflow_run_logging 接线销项 1 个（structured_logger），又经 arch_review_v2_tasks G3「RBAC 落库」接线销项 B2 三族 9 文件（role/user/tenant，2026-11-18，见第 4 章），再经 roadmap B0/D3「治理链断链修复」接线销项 B1 族三拦截器（audit/budget/metering，2026-11-21，见第 4 章），再经 M4 B10 移除 C1 链成员 workflow_engine.py（IWorkflowEngine Protocol，2026-11-23，见 §3 注记），现存量 **43 个**。
- 差异构成：+4（B14 enterprise 族，spec 表外）+5（B15 verify 残留暴露族，批次 3 副作用）+2（B2 postgres/__init__.py、B5 sso/abstractions/__init__.py、B8/B11 __init__.py 等包文件按"整包实体模块全孤儿才标记"规则纳入）等口径细化。
- 扫描脚本口径修正：design 2.7 原型双重前缀 bug 修正后，孤儿判定更精确（含 __init__.py 包名推导）。

## 7. 功能缺失类技术债（B 档外登记，2026-10-20 新增）

> 本章条目不属于第 3 章 B 档孤儿族群（无对应待清理代码），而是独立登记的**功能缺失型**技术债；B 档清理流程不适用于本章条目。

### TD-01 定时触发（scheduled）调度能力缺失

| 字段 | 内容 |
|------|------|
| 现象 | `TriggerType.SCHEDULED` 无运行时调度消费点（全库唯一命中 `domain/validation/node_configuration_validator.py:140`），实例不会因定时被自动启动，故 `input_template` 在定时触发下无实际触发路径 |
| 影响 | 用户在定时场景可能误配 `input_template` 且困惑于「配了不生效」 |
| 处置 | `trigger_input_template_resolution` 需求仅显式声明为已知限制（模型注释 / 前端提示 / 用户文档三处同步声明）；补齐调度能力**另立需求** |
| 关联 | `.codeartsdoer/specs/trigger_input_template_resolution/` |

**TD-01 销项登记（2026-11-19，arch_review_v2_tasks_wave2 任务 5.6 / CHG-70）**

| 字段 | 内容 |
|------|------|
| 销项日期 | 2026-11-19 |
| 销项证据 | F14 定时触发能力已交付（Wave2a P4）：CHG-47（`application/schedule/cron_parser.py`——5 段式解析 + `next_fire_at` 纯函数 + FrequencyPreset 映射）、CHG-48（`application/schedule/schedule_scanner.py`——ScheduleScanner 假时钟扫描器 + TriggerScheduleRegistry）、CHG-49（`api/routers/trigger_schedule_router.py`——触发器管理端点，开关守卫）+ `application_factory` 装配（`build_cron_fire_callback` 启动链，任务 4.4）；P4 门禁记录：**BASELINE-P4 = 3147 collected / 0 failed / 3 skipped / exit 0**（2026-11-19，`arch_review_v2_tasks_wave2/batch-records.md` §P4） |
| 交付口径 | `TriggerType.SCHEDULED` 已有运行时消费点：ScheduleScanner 到期扫描 → `build_cron_fire_callback`（定义重取/编译 → `run_and_record`）→ 实例自动启动——「实例不会因定时被自动启动」现象消除；`input_template` 在定时触发路径下经触发上下文进入模板映射（实际触发路径打通） |
| 处置兑现 | 「补齐调度能力另立需求」由 F14 兑现（spec PB-3：F14 实施后 TD-01 销项；FR-04/FR-07）；「先台账后代码」纪律以两段兑现——处置主台账 C-4 预登记（2026-11-18）先行，本销项登记收口；销项四要素 = 销项日期 / 销项证据 / 交付口径 / 状态 |
| 遗留边界 | 多副本部署注册表幂等 / 落库持久化 / misfire 追补属 Wave2b F9 登记态（design §6.3 R3）；三处已知限制声明原文维持——模型注释（`domain/models/node_configurations.py:196/:206`）、前端提示（`web/src` nodeConfigHints.ts 等）、用户文档（`docs/node-usage-guide.md:70`）——更新必要性评估已登记（Z-1 前端零改动 / 范围守恒优先，Wave2b F9 与前端波及届时一并裁决），详见 `trigger_input_template_resolution/DISPOSITION.md` Wave2 追加块 |
| 状态 | **已销项（关闭）**——本章条目直接关闭，B 档清理流程不适用（本章首段约定） |

### TD-02 ValidationIssue.field 覆盖余量与 join 等待字段 validate 阶段定位缺失

| 字段 | 内容 |
|------|------|
| 现象 | field 机制已交付（`domain/models/common.py` + `sdk/wfp_sdk/models.py` 镜像 + 面板「字段 Y」定位行），但站点填充仅 20 处（触发器段 schedule/event_source/input_template + 预设段 16 站 + agent model_identifier，B13 T4）；其余校验站点（结构/拓扑/其余配置类）field=None。**join 三字段（wait_policy/wait_count/parallel_branches）validate 阶段定位缺失**：B13 T4 初版按 design D4 净新增三段镜像校验（设计假设 validator 已有 parallel_join 等待字段校验，基线实无），与 `GraphCompiler.compile:78` 入口全量 validate 冲突——编译期 `details["rule"]` 契约（`test_parallel_fanout_compile.py` 6a/6b/6c，既有测试零修改红线）被提前拦截为 issues 形态（全量 pytest 3 FAILED 实测定位），已回退（行为回基线） |
| 影响 | 校验面板对多数问题的「字段 Y」定位行缺失（仅「节点 X」）；join 等待字段配置错误在运行前校验不可见——面板 valid=true 放行后启动时被编译期拒绝，用户看到启动失败而非面板字段定位 |
| 处置 | 全量覆盖：按校验器类别批量补 field 参数（低风险机械活，逐类补 pytest 断言）；join 三字段：重构 compile 入口与编译期校验的分层（validate 报 issue 且保留编译期 rule 契约——单源转译或阶段豁免二选一），落地须连跑 `test_parallel_fanout_compile.py` 全 14 例锁定 |
| 关联 | `.codeartsdoer/specs/roadmap_b13_visualization_errors/`（tasks.md T4 回退裁决）；`src/orchestration_platform/engine/graph_compiler.py:78` |

### TD-03 RunLogRedactor URL 凭据模式类字符长串二次方回溯

| 字段 | 内容 |
|------|------|
| 现象 | `observability/run_log/redactor.py:32` `_URL_CREDENTIAL_PATTERN` 对超长连续类字符段（`[a-z0-9+.-]` 无空格串）产生二次方级回溯——B13 T6 测试开发期实测对抗性长串输入 redact() 单次 **58.62s**；既有测试以空格分隔短 token 规避（`tests/unit/test_node_failed_input_snapshot.py:230-232` 注释在档） |
| 影响 | 极端节点输出（超长无空格字母数字点串）进入失败输入快照 / run log 脱敏路径时可能秒级~分钟级阻塞（B13 起 NODE_FAILED 快照路径新增暴露面） |
| 处置 | 模式改写降低回溯（预长度门 / 两段式匹配 / 预扫描短路三选一），补性能回归测试（1MB 量级类字符 blob redact ≤ 1s）后移除测试规避注释 |
| 关联 | `tests/unit/test_node_failed_input_snapshot.py`（规避口径注释）；`tests/unit/test_run_log_redaction_truncation.py`（值模式既有覆盖） |

## 附录：复查命令集

```bash
# 孤儿复扫（与基线同口径，输出与 orphan_baseline.txt / orphan_rescan.txt 对比）
python .codeartsdoer/specs/cleanup_dead_code/scan_orphans.py

# B 档标记计数核对（现应 = 44；原 57，structured_logger 销项 -1、B2 三族销项 -9、B1 三拦截器销项 -3，见第 4 章）
git grep -l "TODO(tech-debt)" -- src/ | wc -l

# 族群分布核对（按族群号统计）
git grep -l "TODO(tech-debt): B档候选孤儿（族群 B1）" -- src/ | wc -l   # 应 = 2（原 5，audit/budget/metering 三件销项）
git grep -l "TODO(tech-debt): B档候选孤儿（族群 B2）" -- src/ | wc -l   # 应 = 10（原 19，role/user/tenant 三族 9 文件销项）

# langgraph 隔离断言（SR-303，命中须全部位于 engine/adapter/）
git grep -n -E "import langgraph|from langgraph" -- src/
```