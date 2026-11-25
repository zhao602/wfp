# 更新日志

本文件记录「企业级多Agent工作流编排平台」全部版本的变更历史。 遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式。

---

## [未发布]

### M4 引擎解耦（B10/A1）

- **EngineSettings 引擎类型化配置**：`engine/abstractions/engine_config.py`
  （17 字段 frozen dataclass，默认逐一镜像 `Settings(_env_file=None)`，
  parity 测试锁定）；引擎域 7 文件 `get_settings` 清除改构造注入
  （scheduler / graph_compiler / langgraph_adapter / plugin_node_loader /
  code / agent / llm 执行器）；`application/engine_wiring.py`
  `build_engine_settings()` 为宿主（api/worker/cli）唯一装配点。
- **NodeUsageRecorder 协议注入**：`engine/abstractions/node_usage_recorder.py`
  Protocol + `Scheduler` 构造尾参注入——引擎域 `governance` 依赖清零
  （未装配进程注入 None == 原跳过语义）。
- **adapter api 依赖移除**：`LangGraphAdapter` 惰性 api 模块导入删除，
  `executor_registry` 缺省改引擎内新建空 `NodeExecutorRegistry()`
  （生产由 `application_factory` 显式注入容器注册表）；`settings`
  构造注入生效 env 覆盖。
- **IWorkflowEngine 死接口移除**：全仓零生产引用的
  `engine/abstractions/workflow_engine.py` 删除 + 注释/文档清理
  （技术债台账 C1 销项，44→43）。
- **AST 导入门禁（CI 步）**：`scripts/check_engine_imports.py` +
  `tests/unit/test_engine_import_isolation.py`（importlib 单一逻辑源）+
  ci.yml backend job 插步 + `scripts/run_local_ci.py` 镜像——engine/
  67 文件禁 6 前缀（api / configuration / governance / application /
  worker / cli），零违规基线。
- **54 开关全量矩阵**：`docs/switch-matrix.md`（默认 / 行号 / 类别 /
  消费层 / 裁剪点 / 收敛位置；OEM 3 开关单列）+ md↔Settings 一致性
  测试；新发现 6 个未接线开关登记（mcp_server_enabled /
  scheduler_batch_mode / checkpoint_compression_enabled /
  sandbox_network_default_deny / supervisor_orchestration_enabled /
  data_transform_custom_code_enabled）。
- **门禁基线（本批实测）**：全量 pytest **3735 passed + 3 skipped
  exit 0**（+32 本批）；vitest 1010+13；typecheck 13 零新增；e2e 6/6；
  四基准 A/B vs `4ead441` 匹配态零一致回归（linear +6.4% 双窗反向判
  噪声 / parallel +4.6% / pipeline +0.4% / sse -2.8%；测量窗机器频率
  节流同码 3.2x 对称，fast-state 复测欠账登记）；ruff（CI 口径）：
  基线自身 ~5.4k 违规（CI 真实 ruff 从未绿——本地一直 py_compile 降级），
  本批自增 8 文件零违规。

### M5 worker 模式 API 补全

- **M3 欠账四端点清偿（worker 读穿透）**：
  - `/events` 列表：统一读取器回读事件仓储，归一化形态与注册表口径一致
    （`NODE_SUCCEEDED`→`NODE_COMPLETED`、payload dict、`occurred_at`）。
  - `/results`：事件推导快照——非终态 `outputs: null`（轮询友好）；
    succeeded 输出取检查点快照 `node_outputs`（INV-08 唯一权威输出
    存储，检查点仓储不可达回退 `{}`）；`finished_at` 取快照留痕时刻。
  - `/recover`：快照守卫 + 检查点续跑作业入队（幂等键
    `{instance_id}:recover:{checkpoint_id}`、`trigger_source=recover`、
    origin 作业载荷 yaml/版本/租户/trace 补齐）；pending 窗口无检查点
    → 500「无可用检查点」（从未运行的如实口径）。
  - `/review`：快照守卫 + 编译重建（origin yaml 优先，工作流仓储
    (definition_id, version) 回退）+ `ReviewDecisionService` 复用（决策
    留痕/终态映射零重复实现）；approve 留痕 `STATUS_CHANGED(running)`
    后入队续跑作业（`:review-resume:` 幂等键、前置队列可用性检查，
    防「running 但无续跑作业」悬挂态）。
- **可见窗消除**：worker 模式提交即落 `INSTANCE_QUEUED` 事件——
  提交→认领窗口期 `GET /instances/{id}`、SSE 推导 **pending**（不再
  404）；快照 `STATUS_CHANGED` 状态推导全分支 + 仓储读取器
  `review_timeout` 终态扩展（审核 reject/timeout 决策立即可见）。
- **配额 worker 落库口径**：worker 模式并发配额计数走
  `count_active_by_tenant`（pending/running 作业数，local/postgres
  双后端）；count→check→enqueue 跨 await 准入竞态为 best-effort
  诚实口径；队列不可达降级注册表计数（≈ 0，配额近似旁路）。
- **D9 心跳租约 TTL 收口**：`IExecutionQueue.heartbeat()` 增可选
  `lease_ttl_seconds` 参——worker 心跳按自身租约续传（claim/take_over/
  heartbeat 全链一致），不再覆盖回队列全局默认；旧双参签名实现兼容
  （worker 构造期一次签名探测，含 `**kwargs` 型实现）。
- **PG JSONB 读侧修复**：事件 `payload`（asyncpg 未配置 JSONB codec
  时为全量事件 dump 字符串 → `_decode_stored_event_payload` 解内层）
  与作业 `initial_inputs`（str 直接 `["key"]` 必崩 →
  `_decode_jsonb_dict`）双修复；快照 `definition_id` 两后端一致。
- **有意分歧（安全优先）**：worker 模式 `/recover` 对
  waiting_review/review_timeout → 409——worker 无法代持注册表挂起态，
  且从门检查点续跑 = 绕过已留痕审核决策（in_process 可重挂起分支
  分歧入档，docs/deploy.md §10.3）。
- **已知项**：`/checkpoints`、`/rollback`、`/replay` 维持注册表口径
  （worker 模式 404）；worker 运行无 run_log_recorder；worker 重放
  缺 business_category 透传；worker POST 响应硬编码
  `"status": "running"`。

### M6 定义版本与发布态（B11/F3）

- **域模型发布态**：`WorkflowDefinition` +`status`(draft|published)/
  `draft_of`/`published_at`（旧 JSON 缺字段默认 draft，兼容加载）；
  `DefinitionRef` 同步富化字段；`WF_STATE_CONFLICT` 复用 409 通道。
- **仓储发布态协议**：`IWorkflowRepository`
  +`get_latest_version`/`get_published`/`publish` 三方法；
  `save` 写边界 409 守卫——发布态版本改写抛
  `InstanceStateConflictError(WF_STATE_CONFLICT)`（local_file 与 PG
  双实现；PG 单语句 `ON CONFLICT ... DO UPDATE ... WHERE
  status <> 'published' RETURNING status`）。
- **v1_10 迁移配对**：`v1_10_workflow_publish_state.sql`（三列 +
  索引 + 迁移登记，幂等守卫）+ `v1_10_rollback.sql` 回滚配对；
  `table_definitions.sql` 同步；迁移 README 清单补 v1_9/v1_10 行。
- **定义管理六端点**（`workflow_definition_router.py` 全重写）：
  `POST /workflows` 真落库（版本=(最新或 0)+1、draft、
  draft_of=已发布版本、YAML 版本号服务端覆盖）；`POST
  /workflows/{id}/publish`（缺省最新、幂等 200）；`GET
  /workflows/{id}?version=`（event-loop hack 清理，直接 await）；
  `GET /workflows/{id}/versions`（状态富化降序裸数组）；`POST
  /workflows/{id}/rollback` **发布回滚语义**——旧内容发新版本号
  （深拷贝 + published + draft_of=目标版本，历史线性不可变）。
- **执行双轨**（`start_instance`）：`yaml_text` 内联轨权威不变；
  `definition_id` 引用轨——显式 `definition_version` 精确取版、
  缺省取 `get_published`（无已发布版本 422、未知 404，严格分流）；
  双空提交 422 双轨提示。
- **版本追溯链**：`InstanceRuntime` +`definition_version`（构造可选
  参数，worker 作业传递）；提交侧 `INSTANCE_QUEUED` 载荷
  +`definition_version`（单载体——`INSTANCE_STARTED` 载荷维持 `{}`
  保 INV-08 三态事件载荷不变契约）；读穿透快照
  `read_instance_snapshot` 从事件载荷提取；`GET /instances/{id}`
  双路径条件键随行（非 None 才带）。
- **既有测试零修改**：全量 4 失败经自增代码回退修正（STARTED 载荷
  回归 `{}`、快照改条件键），既有测试文件零改动；新增测试 4 文件
  61 例（publish_state 18 / publish_migration 15 /
  definition_endpoints 18 / execution_definition_ref 10）。
- **门禁基线（本批实测）**：全量 pytest **3796 passed + 3 skipped
  exit 0**（worktree 隔离 HEAD `74a64d2` + 本批 19 文件，防并行批
  在途污染；3735+61 精确闭合，点符 + collect-only 三角核定）；
  vitest 1010+13（零 web 触碰）；typecheck 0 error 实测（基线
  档案 13 沿 M3 口径，零新增）；e2e 6/6（17.7s，worktree 隔离）；
  四基准匹配态声明论证（热路径 diff 仅实例级 O(1) 两处，基线沿用）；
  ruff 16 自增文件零违规；**版本维持 0.3.0**（用户裁决，统一升位
  递延）。

### B12 数据分块与检查点写路径优化（P3 核心）

- **取数游标化**：data_source 执行器 has_more 单一驱动分页循环；
  CSV 连接器生成器流式行读取（免整文件载入）；截断标志语义修正
  （恰达上限 + has_more=True → truncated=True，数据未读完即截断）。
- **快照结构共享**：`node_outputs` 写入值不可变约定固化（D2.2 审计）；
  `snapshot()` 零深拷贝结构共享，多消费者共享行引用。
- **变换行数守卫**：`data_transform_ops` 模块常量上限（INV-15 模式）+
  超限告警/截断/拒绝三态处置，21 算子全对账；错误码复用零新增。
- **检查点写路径 D7-lite（gate 触发改道，spill-to-disk 裁撤）**：
  - 播种零拷贝：`SharedState.for_seeding`（model_construct 免验证），
    5 个大载荷构造点转换（router/service/adapter/worker/state_mapping）；
  - same-round 侧车索引：`rounds.idx`（round→checkpoint_id；先索引后
    文件写序不变量 + 无索引/损坏/陈旧三重回退自愈）——append O(n²)
    重解析消除（linear_50 p50 −77%、sse_1000 setup 45.1s→11.0s）；
  - 紧凑 dump：local_file append 去缩进（串 18.2MB→8.1MB）；
  - 执行结果零验证：`NodeExecutionResult.output_value` JsonValue→Any
    （联合类型容器重建 +16.9MB 消除；进程内瞬态从不反序列化）。
- **内存 gate 达成**：pipeline_100k server 峰值 **140.7MB ≤ 150MB**
  （T0 基线 248.1 → 140.7，−43.3%；248.1→213.0→156.8→140.7 阶梯
  见批内 design D7.5）——spill-to-disk 正式裁撤（−2 人日）。
- **OOM 专项测试**：新增 5 文件 49 例（游标分页 9 / CSV 流式 8 /
  行数守卫 7 / 快照共享 9 / 检查点索引 16），既有测试零修改。
- **门禁基线（本批实测）**：全量 pytest **3906 collected / 215 文件
  exit 0**（精确闭合：T0 3756 + M6 会话中提交 +61 + 本批 +49 + 并行批
  未提交 +40；B12 隔离预演树 1ef7576+本批 20 文件全量 exit 0——staging
  纯度验证）；vitest 1012+13（零 web 触碰）；typecheck 0 error；e2e 6/6
  （44.4s，worktree 隔离 8331/5175）；四基准 A/B 同窗 worktree 对照
  （A=1ef7576 干净 / B=+本批）：内存 **B 140.7×2 零方差 vs A 248.7
  冷/226.4 暖**；timing p95 pipeline −41% / linear −77% / parallel
  −66% / sse −17%（105.6 单轮尖峰复测 71.5 界定）；ruff 本批 20 文件
  零违规；AST 门禁 PASS；**版本维持 0.3.0**。

### 输出展示扇出与行作用域修复（display_fanout 并行批）

- **输出展示扇出全执行**：scheduler `_display_fanout_targets` + 主循环
  集成——OUTPUT_DISPLAY 节点多出边时全部终点依序执行（此前仅单条）；
  round_index / checkpoint 写路径与主循环一致（next=None 终态语义）。
- **多出边校验告警**：`_validate_multi_outedges`——OUTPUT_DISPLAY 与
  条件/并行出边混布时 WARNING（非阻断），前置提示拓扑歧义。
- **过滤行作用域（OP-08）**：`_RowScope`——filter/map 算子表达式内
  变量引用解析为当前行字段（此前恒失败），契约用例
  `test_filter_with_variable_reference_filters_rows` 锁定。
- **悬浮层运行态收口**：overlay 折叠态/完成态 +「完成但无输出」提示；
  App `sessionStorage` 活动实例键——刷新后恢复运行实例面板。
- **提示文案**：nodeConfigHints 多出边提示补充。
- **测试**：新增 `test_display_fanout.py`（5）+
  `test_data_transform_row_scope.py`（8）；chain 契约用例改名收口；
  overlay 测试 +2（折叠/完成态）；本批 py 新文件 ruff 零违规。
- **门禁基线（本批实测，落定树）**：全量 pytest **3861 collected / 215
  文件 exit 0**（collect-only 与 junit 双核一致；与 B12 段记录 3906 差
  −45：其「并行批 +40」为在途估算，本批落定实测 +13，M6 +61 / B12 +49
  组件级复验精确一致，残余 −18 无原始捕获可审计）；vitest 1012+13；
  typecheck 0 error；e2e 6/6（37.3s）；AST 门禁 PASS；**版本维持 0.3.0**。

### B14 契约与故障批（T2+T4）

- **契约 snapshot 门禁（T2）**：`scripts/check_contract_snapshot.py` +
  双金档 `tests/fixtures/contracts/`——openapi_v1.json（126 路径全量
  OpenAPI 字节冻结）+ contract_surfaces.json（SDK 方法 8 / web 端点
  23 / apiTypes 声明导出 25·复导出 31 三面提取钉扎）；`--regen` 纪律
  与破坏性变更流程落 `docs/sdk-contract.md` §5.1；接线 run_local_ci
  步 2.5 + 薄包装 `test_contract_snapshot_gate.py`。TS 对拍按批内
  裁决待 F6 解冻，本批不手写 TS SDK。
- **SDK 对拍 drill（T2）**：`scripts/drill/b14_sdk_parity.py`——真实
  后端（BenchServer）+ `wfp_sdk.WorkflowClient` 9 方法全序列 wire
  对拍（validate 合法/非法双域、submit、start、SSE 全量回放、轮询
  同源对账、wait、results、cancel 409 INSTANCE_CANCEL_CONFLICT、
  终态快照）；PASS 11/11，留档 `_drills/`（含隔离工作树复跑件）。
- **PG 断连注入（T4）**：`test_postgres_unavailable_injection.py`
  （8 例，asyncpg 0.32.0 真接口）——连接池创建失败单例不毒化 + 恢复；
  事务中段断连干净中止（ROLLBACK、零后继语句、连接归还）+ API 边界
  500 映射；执行队列租约三重门控（claim FOR UPDATE SKIP LOCKED；
  heartbeat/complete/fail lease_owner+status 门控）。
- **LLM 超时注入（T4）**：`test_llm_timeout_injection.py`（4 例，
  真实 HttpLlmClient + httpx patch）——超时文案内聚「LLM 请求超时
  （上限 Xs）」零外逸；重试退避 base×2ⁿ；执行器 LLM_INFERENCE_FAILED；
  调度器 NodeExecutionError + 下游零执行。
- **webhook 5xx 注入（T4）**：`test_webhook_delivery_fault_injection.py`
  （4 例）——恒 500 → 5 次尝试 → failed + DLQ 完整 payload_snapshot；
  瞬态恢复 delivered；红驱 attempts 累计（5+1=6）；全链路 bus→DLQ→
  换 client→红驱→送达；每请求 X-Orch-Signature 在场且复算。
- **检查点崩溃窗与 kill-9（T4）**：`test_checkpoint_crash_window_injection.py`
  （2 例）——W1 幽灵索引项（再写放行 + 索引自愈）/ W2 torn 数据文件
  （exists() 即信吸收、latest_for_instance 跳过回退）；kill-9 drill
  （m3_kill9_takeover.py）零修改重跑 PASS 6/6 新鲜留档。
- **run_local_ci 加固**：GBK 防崩（stdout/stderr reconfigure
  errors=replace）+ 契约 snapshot 插步。
- **门禁基线（隔离工作树权威口径，HEAD 420958a + 本批 11 文件）**：
  全量 pytest **3877 passed + 3 skipped = 3880 collected exit 0**
  （220 文件；本批 +19：门禁 1/PG 8/LLM 4/webhook 4/崩溃窗 2）；
  run_local_ci ALL PASS（lint 761 文件 / 引擎隔离 / 契约 snapshot
  3.5s / collect 3880）；parity drill 复跑 PASS 11/11；**版本维持
  0.3.0**（金档冻结即契约面不变，无 API 变更）。

### B13 可视化与错误批（U2+U3）

- **Round-trip 三字段（T1）**：`definitionTypes.ts`
  `ControlFlowNodeConfiguration` 追加 `parallel_branches` / `wait_policy`
  / `wait_count`（与后端 `JoinWaitPolicy` 对齐）；`canvasToYamlExporter.ts`
  白名单拷贝（数组 `Array.isArray` 判）；导入侧零改动（spread 守卫
  SHA 比对）；`parallelRoundTrip.test.ts` 新增——workflow-definition
  目录 vitest 43 passed（6 文件）。
- **分支事件桥接（T2，U2 后端）**：`execution_runner.py`
  `NodeEventCallbackImpl` 新增可选 `on_parallel_branch`（沿 append_event
  范式；event_type 按 status 取 PARALLEL_BRANCH_STARTED/COMPLETED；
  payload {join_node_id, branch_entry_id, status} + 顶层 branch_id）；
  `scheduler.py` `_publish_parallel_event` 治理总线早退**之前**插入
  getattr 守卫桥接（publisher=None 泳道事件仍入流）；worker 模式同
  Impl 自动覆盖（导入同一性断言）；`test_parallel_branch_stream_events.py`
  4 passed。
- **泳道面板（T3，U2 前端）**：新 `parallelRegionStatus.ts`
  （deriveParallelRegions join 扫描 + edges 分支路径推导；
  reduceBranchStatuses 五态 + 三 wait 策略文案 + 已收敛）；
  `NodeExecutionStatusOverlay.tsx` 分支事件分流（节点映射前）+ 泳道块
  （`wfp-overlay-parallel*` BEM，非收起区）；新测试 2 文件——两目录
  vitest 99 passed（13 文件）。
- **ValidationIssue.field（T4，U3 后端）**：`domain/models/common.py`
  + `sdk/wfp_sdk/models.py` 镜像（dataclass/from_dict 补链）；validator
  可得子集填充 20 站（触发器段 schedule/event_source/input_template +
  预设段 16 站 + agent model_identifier），结构与拓扑类保持 None；
  **join 三字段镜像站回退**：design D4 假设 validator 已有 parallel_join
  等待字段校验（基线实无），初版净新增三段 ERROR 与
  `GraphCompiler.compile:78` 入口全量 validate 冲突——编译期
  `details["rule"]` 契约（既有测试零修改红线）被提前拦截，全量
  pytest 3 FAILED 实测定位后回退（行为回基线，编译期契约由
  `test_parallel_fanout_compile.py` 6a/6b/6c 继续锁定），遗留登
  tech-debt TD-02；`test_validation_issue_field_location.py` 10 passed
  + ruff clean。
- **校验面板 + 一键跳转（T5，U3 前端）**：`apiTypes.ts` 裸报告修型
  （`ValidateWorkflowResponse` = `ValidationReport` 形状，请求体
  `yaml_text` 对齐 router）；新 `ValidationIssuesPanel.tsx`（色点 /
  code / message / `节点 X · 字段 Y · 行 Z` 定位 / 「定位到画布」/
  role=alert）；`App.tsx` 运行前置校验（canvasToYaml → validate →
  未通过渲染面板不启动，sessionStorage effect 零触碰）；
  `WorkflowCanvas.tsx` 受控 `focusNodeRequest`（nonce → setCenter
  zoom 1 / duration 300）；vitest 选区 97 文件 812 passed + 1 skipped、
  typecheck 0。
- **失败输入快照（T6，U3）**：NODE_FAILED payload 附 input 快照——
  `_sanitize_failed_input` = RunLogRedactor→Truncator（顺序沿 recorder
  口径，上限 `log_max_payload_bytes`）；scheduler 失败路径新形态回调
  `on_node_failed_with_input`（快照复用 / run_log_recorder 缺席现场
  `dict(node_outputs)` 补采 / 旧回调兜底；:1304 取消路径不动）；前端
  `failureFeedback.ts` + overlay 折叠 JSON 块（aria-expanded +
  `wfp-overlay-feedback__snapshot*` 浅/深双轨）；
  `test_node_failed_input_snapshot.py` 5 passed（1.11s）+ overlay
  vitest 2 passed（exec-vis 9 文件 64 passed）+ 调度器相关回归 34/34
  （junit errors=0 failures=0）。
- **e2e 扩充（T7）**：`06-validation-jump.spec.ts`（缺省条件分支空
  conditions → 校验面板「1 错误 / 0 警告」/ 问题行 / 定位行 → 负向
  断言无 instances POST → 定位到画布选中态 → 关闭）；`07-parallel-
  visualization.spec.ts`（「导入 YAML」filechooser 导入确定性并行夹具
  `e2e/fixtures/parallel-visualization.yaml`——trigger 双 output_display
  分支 + parallel_join（data_transform 需列表输入而 UI 硬编码空 inputs
  不可行，形态偏差在档）→ 泳道「并行汇聚 · 并行分支」/ display_a·
  display_b 成功 / 已收敛 / 成功: 4；TestClient probe 全链路预验证：
  validate valid=true 仅 1 WARNING、实例 succeeded、
  PARALLEL_BRANCH_STARTED/COMPLETED×2）；新 spec 独立连续 2 轮全绿
  （11.9s/11.0s）+ 全量 8 spec 8 passed（20.4s）。
- **门禁基线（本批实测，锚 420958a）**：全量 pytest **3896 passed +
  3 skipped = 3899 collected / 0 failed exit 0**（= 3861 基线 + 本批
  +19（T2 4/T4 10/T6 5）+ B14 并行会话 +19，junit errors=0
  failures=0）；vitest **1046 passed + 13 skipped（129 文件）**
  （基线 1012+13，本批 +34）；typecheck **0 errors（tsc EXIT=0）**；
  e2e **8/8**；ruff 本批新/改 py 文件零违规；AST 引擎隔离（pytest
  全量含 `test_engine_import_isolation` 绿）；**版本维持 0.3.0**。
  B14（3f2a489）于本批收口期间并行提交，本批提交叠其上；join 三字段
  回退后 `test_parallel_fanout_compile.py` 14/14 恢复绿。

---

## [0.3.0] - 2026-11-23

> **版本覆盖说明**：本版本覆盖 M2（独立 worker 进程）与 M3（实例级取消与
> 超时）两个批次线；M2 未单独升位，并入本版评估收口。

### M2 独立 worker 进程与读穿透（并入本版）

- **独立 worker 进程**（`python -m orchestration_platform.worker`）：
  认领/租约/心跳/接管收口全链——与 api 同镜像、同任务、不同 command；
  `EXECUTION_MODE=worker` 环境切换。
- **接管语义（at-least-once）**：kill -9 / 宕机零清理，租约到期后由
  其他 worker `take_over_expired` 接管；重放范围 = 被杀时正在执行的节点
  （上游节点从检查点恢复不重放）。
- **读穿透**：worker 执行实例不在 api 内存注册表，`GET /instances/{id}`
  与 SSE 经事件仓储回读兜底。
- **事件终态持久化**：`PersistingInstanceRegistry._INSTANCE_EVENT_MAP`
  状态映射——cancelled/failed/succeeded/waiting_review 随事件落账。

### M3 实例级取消与超时（P5）+ B9 收口

- **取消标志与迁移**：`v1_9_job_cancel_requested.sql`（
  `cancel_requested` 列 + 活跃作业部分索引）；`IExecutionQueue`
  +`request_cancel`/`find_active_by_instance`（local_file/postgres 双后端）。
- **取消双路**：抢占式 `runner_task.cancel()` 为主 + 协作式
  `cancellation_checker`（Scheduler 构造注入）节点边界兜底；
  in_process 快路径零行为变化。
- **取消端点 worker 扩展**：`POST /instances/{id}/cancel` 读穿透三态
  （活跃置标志 / 终态 409 `INSTANCE_CANCEL_CONFLICT` / 不可见 404）；
  pending 盲区 api 侧落 `INSTANCE_CANCELLED` 事件即取消即可见；
  认领/接管时标志收口 + 幂等事件补落（查重后写）。
- **实例级看门狗**：`INSTANCE_TIMEOUT_SECONDS`（默认 1800，支持小数秒）
  ——in_process/worker 两模式同点；超时终态 failed + 失败码
  `WF_INSTANCE_TIMEOUT`（非 cancelled、非 timeout_terminated，终态集合
  冻结）；`asyncio.TimeoutError`（Exception 系）与 `CancelledError`
  （BaseException 系）严格分流。
- **cron 定义版本钉**：cron fire 作业钉 `definition_version`，worker
  重建按 (definition_id, version) 精确取版——fire→认领窗口升版不漂移。
- **双 drill 留档**（`scripts/drill/` + `_drills/*.json`）：kill -9 强杀
  接管（taskkill /F 等价，检查点续跑、中断节点 at-least-once 重放）+
  双副本并发认领互斥（6 作业零双执行）。
- **已知项（D9）**：`IExecutionQueue.heartbeat()` 续租走
  `EXECUTION_QUEUE_LEASE_SECONDS` 默认、无 TTL 参——生产调整租约请统一
  用环境变量全链对齐（详见 `docs/deploy.md` §10.4）。

### 依赖与门禁

- **门禁基线（0.3.0）**：全量 pytest **3584 passed + 3 skipped exit 0**
  （M2 收口基 3526+3 + M3 增量 58）；vitest 123 文件 1007+13；
  typecheck 13 既有错误零新增；e2e 6/6（D1 realtime 503 缺陷断言锁定）；
  四基准 M3 vs M2 HEAD 同机 A/B 对照零回归（当日绝对值因机器内存状态
  漂移 +20~37%，A/B 证据随批归档）。
- **欠账登记**：`/results`、`/events` 列表、`/recover`、`/review` 四端点
  worker 适配（M4+）；配额 `count_active_by_tenant` worker 落库口径；
  D9 heartbeat TTL 参（接口演进 M4+）；PG 事件 payload 读丢失（既有）；
  D1 realtime `/subscriptions` 恒 503（e2e 断言锁定）。

---

## [0.2.0] - 2026-11-22

> **版本编号说明**：自 B 网格批次线（2026-11 平台重构）起，版本号以 `pyproject.toml`
> 的 0.x 线为准；本段之下的 1.x 历史为早期单体阶段存档，与当前版本线不连续。

### 对外基线（roadmap-v3 B8a，N1/N2/N6）

- **许可证（N1）**：全仓采用 Apache-2.0——`LICENSE` 标准全文落库；根 / SDK
  `pyproject.toml` license 声明与 classifier。
- **仓库基线（N2）**：首次提交落 `main` 分支；annotated tag `v0.2.0`；版本三处升位
  （根 / SDK / web 均 `0.2.0`）；发布与推送 runbook（`docs/repo-release.md`，
  remote 凭据后补执行）。
- **README（N6）**：嵌入视角改写——SDK 30 行示例首屏；修正三处口径漂移
  （「单游标调度」→ 单游标确定性调度 + 并行扇出区域；spec_version「v1.0/1.1/1.2」
  → v1.0/1.1；范例「15 个 YAML」→ 16 个）；SSO experimental 标注；
  LangGraph 双运行时严格子集声明；差异化与文档索引链接。

### 平台能力收口（B0–B7 批次线摘要）

- **B0–B1 基线与存储治理**：全量测试门禁体系（精确闭包 exit 0 口径）；
  PostgreSQL 仓储形态（asyncpg，`[postgres]` extra 显式安装，缺失报错不静默降级）
  与 local_file 零依赖形态并存。
- **B2 嵌入 SDK**：`wfp-sdk` 同步客户端——9 方法面、SSE 事件订阅、同构错误家族、
  零平台内部依赖（隔离 venv 可独立安装）。
- **B3 双运行时**：LangGraph 适配层——单源 node 语义、`langgraph_runtime_enabled`
  开关（默认关闭）、13 例等价矩阵与严格子集防线（`_validate_subset`）。
- **B4/B6 扇出与 HITL**：并行区域 `wait_all / wait_any / n_of_m`、失败游标恢复、
  严格区域语义、SSE 前沿上报（`examples/parallel_execution/parallel_fanout.yaml`）；
  HITL 人工审批挂起/恢复；执行使用量上报。
- **B5 OEM 白标**：品牌注入（`scripts/build_oem.py` + `oem/brands/`）与品牌 API 面。
- **B7 收割批**：usage 汇总端点、节点级 token 记账、tenant_id 口径统一、
  调度器租户传播、事件订阅持久化；版本治理文档（`docs/spec-versioning.md`：
  HC-02 兼容承诺 + 1.x 只增不删三承诺）。

### 性能基线（roadmap-v3 B8c）

- **四基准脚本**（`scripts/perf/`）：线性 50 节点 / 并行 8 分支 / 10 万行管道 /
  SSE 1000 事件——全栈真实 uvicorn + `wfp_sdk` 口径，两遍法（计时遍 +
  tracemalloc 内存遍），p50/p95 nearest-rank；原始 JSON 结果随批归档
  （`.codeartsdoer/specs/roadmap_b8c_perf_baseline/results/`）。
- **基线文档**（`docs/benchmark.md`）：环境声明、四表汇总、已知特性三条
  （local_file 检查点 O(n²) 重解析 / 共享状态链级累缩 / 执行期事件循环占用）、
  合成数据标注与复现命令。

### 门禁口径（v0.2.0 实测）

- 后端 pytest：**3468 collected（3465 passed + 3 skipped），exit 0**。
- 前端 typecheck：13 基线错误（历史存量，口径为零新增）；
  vitest：**1007 passed + 13 skipped**。

---

## [1.4.0] - 2026-09-24

### 新增

#### M25 Agent 执行沙箱（T130-T143）

- 沙箱策略模型与枚举（`IsolationMode`、`NetworkPolicyMode`、`ViolationType`、`SandboxErrorCode`）
- 三守卫机制：路径守卫（`PathGuard`）、网络过滤（`NetworkFilter`）、资源计量（`ResourceMeter`）
- 逻辑隔离运行时（`LogicalIsolationRuntime`）——同进程隔离，零外部依赖
- 子进程执行器（`SubprocessExecutor`）——进程级隔离
- 容器隔离运行时（`ContainerIsolationRuntime`）——容器级隔离（接口预留）
- 违规发布器（`ViolationPublisher`）——异步发布沙箱违规事件
- 沙箱化工具调用器（`SandboxedToolInvoker`）——透明包装 Agent 工具调用
- REST API（`/api/v1/sandbox/policies`、`/api/v1/sandbox/sessions`）
- 前端面板（`SandboxPolicyPanel`）

#### M26 插件 SDK 与节点市场（T144-T161）

- 插件 SDK 基础设施：`PluginNodeBase` 基类、`PluginManifest` 模型、manifest 校验器、打包校验器、本地调试运行器
- 插件节点加载器（`PluginNodeLoader`）与执行器（`PluginNodeExecutor`）
- `NodeType.PLUGIN` 枚举成员与 `PluginNodeConfiguration` 模型
- `GraphCompiler` 扩展 `plugin_schema_provider` 可选参数（向后兼容）
- 贡献市场：模型、状态机（`ContributionStateMachine`）、签名校验（`SignatureVerifier`）、静态安全扫描（`StaticSecurityScanner`）、发布服务
- REST API（`/api/v1/plugin-nodes`、`/api/v1/contributions`）
- 前端面板（`PluginNodeManager`、`ContributionSubmitWizard`、`ContributionReviewBoard`）

#### M27 SSO 与企业连接器（T162-T181）

- SSO 模型与枚举（`SsoProtocolType`）
- 凭据加密器（`CredentialCipher`）——AES-GCM 对称加密
- IdP 配置管理器（`IdpConfigManager`）
- SAML 适配器（`SamlAdapter`）与 OIDC 适配器（`OidcAdapter`）
- 防重放守卫（`ReplayGuard`）——基于 TTL 缓存
- JIT 供给（`JitProvisioner`）——自动创建/更新用户与角色映射
- SSO 服务（`SsoService`）——统一发起/回调/降级
- 企业连接器模型与枚举（`DataSourceType.CRM/ERP/OA`）
- 凭据仓库（`EnterpriseCredentialStore`）——版本化凭据管理
- 数据范围守卫（`DataScopeGuard`）与连接测试器（`ConnectionTester`）
- CRM/ERP/OA 连接器骨架
- REST API（`/api/v1/auth/sso/**`、`/api/v1/connectors/enterprise/**`）
- 前端面板（`SsoSettingsPanel`、`EnterpriseConnectorPanel`）

#### M28 评估框架（T182-T191）

- 评估模型与枚举（`EvaluationType`、`EvaluationTaskState`）
- 场景集管理（`ScenarioSetService`）
- 轨迹评分器：规则评分（`RuleBasedTrajectoryScorer`）+ LLM 评分（`LlmTrajectoryScorer`）
- CLEAR 四维评分（`ClearScorer`）——Correctness / Latency / Efficiency / Architecture / Robustness
- 评估执行器（`EvaluationRunner`）——异步执行场景集
- 基线存储（`BaselineStore`）与回归报告（`RegressionReporter`）
- REST API（`/api/v1/evaluation/**`）
- 前端面板（`EvaluationDashboard`）

#### M29 语义索引发现与收尾（T192-T203）

- 语义发现模型与枚举（`DescriptionSource`）
- 关键词倒排索引（`KeywordIndex`）——BM25 评分
- 向量索引（`VectorIndex`）——可插拔后端（本地 / 外部）
- 混合检索（`HybridRetriever`）——RRF 融合排序
- 发现服务（`DiscoveryService`）——统一查询入口
- REST API（`/api/v1/agent-discovery/**`）
- 前端面板（`AgentDiscoveryPanel`）
- 端到端集成测试与兼容性回归测试
- 8 份阶段文档

#### 前端管理后台入口

- `AdminConsole` 管理后台根组件，聚合 phase3/phase4 全部 12 个治理与管理面板
- `App.tsx` 添加画布编辑器 ↔ 管理后台视图切换

### 修复

- `.env.example` 中 `TOKEN_SIGNING_SECRET` 占位符笔误（`change-me-token-secretF` → `change-me-token-secret`）
- `debug_runner.py` 移除不优雅的 `if False else` 条件表达式
- `sso_router.py` 移除未使用的 `HTTPException` 导入

---

## [1.3.5] - 2026-09-24

### 新增

#### 偏差修正（T80-T129）

- ABAC 属性策略引擎扩展（属性求值器、策略仓储、策略引擎、拦截器）
- 墓碑与被遗忘权（GDPR 合规删除、墓碑服务、降级响应）
- 语义缓存（缓存后端抽象、LRU 内存实现、Redis 实现）
- 事件总线（事件总线抽象、内存实现、Redis Stream 实现）
- NL 自然语言编排（意图解析器、NL 编排服务、模板生成器）
- 兼容性回归测试套件

---

## [1.3.0] - 2026-09-24

### 新增

#### M19-M24 生产治理（T46-T79）

- 多租户 RBAC 与组织树
- 计量与计费（令牌计量、预算控制、成本仪表盘）
- 审计日志与合规查询
- 全局调度器与资源池管理
- 拦截器链（鉴权→ABAC→租户隔离→审计→配额→预算→调度）
- 治理 REST API 与前端面板（`GovernanceDashboard`）

---

## [1.2.0] - 2026-09-24

### 新增

#### 引擎增强（T24-T45）

- 回滚与重放机制
- 实时数据订阅
- 上下文查询 API
- MCP 服务端集成
- 大数据连接器（Hologres、Kafka、Flink、Spark）
- 语义缓存
- 执行可视化

---

## [1.1.0] - 2026-09-24

### 新增

#### 核心引擎（T1-T23）

- YAML 声明式工作流定义与解析
- 工作流图拓扑校验与编译
- 调度器（确定性调度）
- 节点执行器（Agent、Tool、DataSource、Trigger、控制流、子工作流、动态注入、数据变换）
- 检查点与故障恢复
- 仓储抽象与实现（内存 / PostgreSQL）
- FastAPI 应用工厂与 REST API
- React 画布编辑器（节点面板、画布、配置面板、范例库）
- YAML 导入/导出

---

## 环境要求

- Python ≥ 3.10（实测基线 3.10.20）
- Node.js ≥ 18
- PostgreSQL ≥ 14（可选：`[postgres]` extra + postgres profile；未配置时 local_file 形态）
- Redis ≥ 6（可选，未配置时降级为内存实现）

## 已知限制

- SSO 企业身份为 **experimental**：SAML/OIDC 适配器为骨架，验签能力未生产验证
  （roadmap S2 范围）
- LangGraph 双运行时为**严格子集**：并行扇出、HITL、回滚重放等五类场景在子集外，
  由内置图状态机引擎执行（见 `docs/langgraph-equivalence-matrix.md`）
- 企业连接器（CRM/ERP/OA）为骨架实现，需按实际系统适配
- 容器隔离运行时为接口预留，需对接实际容器运行时
- 向量索引外部后端为接口预留，需对接实际向量数据库
- 前端 `npm run build` 受 `tsc --noEmit` 门控：13 基线错误清零前（roadmap A4），
  生产构建路径以 `vite build` 口径为准