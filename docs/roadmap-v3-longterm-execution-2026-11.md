# V3 长期执行方案与实现路径（B9–B16 · 2026-11-22）

> **本文定位**：`docs/roadmap-v3-2026-11.md` 的**执行层**——把 v3 §3.2（中期）与 §3.3（长期）具体化为批次级路径 B9–B16，每批定目标 / 范围锚点 / 出口验收 / 风险 / 人日。v3 回答「往哪走、为什么」，本文回答「按什么顺序、以什么口径验收」。
>
> **不重复的内容**：能力事实与代码位置见 `docs/architecture-review-2026-11.md`；竞品论证见 `docs/differentiation-strategy-2026-11.md`；性能数字见 `docs/benchmark.md`。
>
> **执行惯例**：批次启动时仍按项目惯例建三件套（spec/design/tasks）+ `_baseline/` 冻结 + 出口记录五节 + 单提交 `main`；本文只做批次级承诺，不预填批次内任务清单。
>
> **状态**：已批准落档（2026-11-22）；v3 §第三部分交叉链接已补（B8d e2e 批出口后，见 v3 §第三部分头部承接注记）。

## 1. 定位与结论

1. **短期池（v3 §3.1）已基本消化**：B7 / B8a / B8b / B8c / B8d 已收口落 `main`（四提交，tag v0.2.0；B8d = `4d93831`），B8e（A5 边界成文）在途，PyPI / remote 待凭据（B8b / B8a 移交项）。
2. **中期是长期的前置链**：**B14 的 T4（故障注入）依赖 B9 的 worker 接管；B16 的 F5 依赖 B11 的 F3**——中期不可跳过。
3. **长期六批 ≈ 65–68 人日**；加中期 32–40 人日，B8 后全程 ≈ 97–108 人日 ≈ 20–22 周；叠加短期已投 ~27–30 人日，全 v3 ≈ 124–138 人日。**超 v3 的 120 上限时按 §8 裁剪顺序削减，备选池不得偷偷加塞。**
4. **单人 2 周迭代 / 10 人日纪律不变**；B9 因体量拆两个子窗口，仍是一批一出口记录。

## 2. 现状基线（2026-11-22 实测）

| 项 | 值 | 出处 |
|---|---|---|
| git | `main` 6 提交：`79f9d70`(v0.2.0) / `1b2f9b4`(B8b) / `6925074`(B8c) / `4d93831`(B8d) / `d2058a6`(B9-M1) / `bcc34ac`(本文落档；B8e 随后收口见其出口记录)；remote 未设（凭据移交） | `git log` |
| pytest | **3490 collected = 3487 passed + 3 skipped，exit 0**（B8e 复测 2026-11-23，336.33s；较 3468 +22 = B9-M1 +21、B8e +1） | 各批出口记录 |
| vitest | 1007 passed + 13 skipped，123 文件 | B8a 出口 |
| typecheck | tsc exit 2，13 errors 零新增（基线文件清单在档） | B8a 出口 |
| e2e | 5 路径 spec + helpers 已落库，playwright.config 在；B8d 已收口（见下「B8d 收口」行） | `web/e2e/`、B8d tasks.md |
| 性能基准 | linear_50 p95=21759.9ms / parallel_8 p95=15872.8ms / pipeline_100k p95=27281.2ms·峰值 247.8MB / sse_1000 p95=85.3ms；脚本 `scripts/perf/` | B8c exit-record |
| 已知 flake | `test_event_export_core.py::test_deterministic_serialization` 微秒翻转（B7/B8b 登记共 2 次）——失败全量复跑，登记不修改 | B7/B8b 出口 |
| F1 未启动证据 | `execution_instance_router.py:1015/:1287` 仍为 `asyncio.create_task`；全仓无执行 worker 模块 | grep 实测 |
| S2 现状 | `saml_adapter.py:76`「简化实现：实际应使用 xmlsec 解析并验签」；B8a 已在 README 标 experimental | 代码 + B8a 出口 |
| F3 现状 | `workflow_definition_router.py` 仅 validate/save（无持久化）；执行时 YAML 内联提交（`execution_instance_router.py:332` yaml_text/definition）；domain 模型已有 `definition_version` 字段 | 代码实测 |
| F5 后端已备 | 订阅 CRUD/enable/disable/deliveries/dead-letters/redrive/stream 全在（`event_export_router.py:149–:266`），仅缺前端管理面 + 密钥轮换 | 代码实测 |
| U3 地基 | `ValidationIssue` 已含 `node_id`/`edge_id`/`location`（YAML 行列号），缺字段级定位与前端一键跳转 | `domain/models/common.py` |
| U2 地基 | 调度器区域/join 语义在（`scheduler.py:756–:766` wait_all/wait_any/wait_n_of_m；`get_parallel_positions`），缺 API 暴露与前端泳道/甘特 | 代码实测 |
| S3 地基 | Dockerfile 三阶段 + 非 root + 前端 dist 反代托管已在（38 行）；缺 SBOM/扫描/digest 锁/只读根 | `Dockerfile` |
| B8c 设计输入 | 执行期同步 I/O 长占事件循环、local_file 检查点 O(n²)、node_outputs 累缩——B9 worker 化的实证动机 | B8c exit-record §5 |
| B8d 收口 | e2e 十连绿（10 轮 6 passed）；pytest 3468 exit 0（271.25s）；提交 `4d93831` 落 `main`；v3 附录 B 已随批刷新 | B8d exit-record |
| B8d 新登记缺陷 D1 | realtime 订阅管理器全运行时未装配（`/api/v1/subscriptions` 恒 503，e2e 缺陷断言锁定；**非** B16/F5 的 event-export 订阅面 `/event-export/subscriptions`）——修复排期待裁决 | v3 附录 B、`web/e2e/04-subscriptions.spec.ts` |

## 3. 实现路径总览

```
B8e(A5 边界成文, 2–3 人日)
        │
        ▼
B9(F1 执行无状态化 + P5 取消, 22–26 人日) ──→ B14(T4 故障注入)
        │                                        │
        ▼                                        ▼
B10(A1 引擎解耦, 10–12 人日)              B15(S3+S4+S5 交付加固)
                                                 │
B11(F3 版本发布态, 9–10 人日) ──→ B16(F5 订阅管理面)  │
B12(P3 数据分块, 8–10 人日)          B16(F4 HITL 运营面)
B13(U2+U3, 13 人日) ──────────────→ B16(运营面收尾)
S2(SSO 闭环, 5–8 或 0.5 人日) —— 可插小批，建议 B15 前
```

- 严格依赖只有四条：**B8d→B9**（e2e 安全网先行）、**B9→B14(T4)**（kill -9 注入需接管能力）、**B14(T4)→B15(S4)**（恢复演练需故障注入验证过）、**B11(F3)→B16(F5)**（订阅按版本生效）。
- 其余顺序可换（单人无法真并行）：B11/B12/B13 互不依赖；B13 前置条件为「B8d 十连绿在档」（前端改动安全网）。

### 3.1 周历（1 人 / 2 周迭代 / 10 人日）

| 周次 | 批次 | 人日 | 累计（B8 后） |
|---|---|---|---|
| 0–2 | B8d e2e 收口（已落 `4d93831`）→ B8e A5 边界成文（在途） | 10–13 | 13 |
| 3–8 | **B9** F1+P5（两子窗口） | 22–26 | 35–39 |
| 9–10 | **B10** A1 引擎解耦 | 10–12 | 46–51 |
| 11–12 | **B11** F3 版本发布态 | 9–10 | 56–61 |
| 13–14 | **B12** P3 数据分块 | 8–10 | 65–71 |
| 15–16 | **B13** U2+U3 | 13 | 78–84 |
| 17–18 | **B14** T2+T4 | 11 | 89–95 |
| 19–20 | **B15** S3+S4+S5 | 13 | 102–108 |
| 21–22 | **B16** F4+F5 | 11 | 113–119 |
| 插班 | **S2** SSO 闭环（建议 B15 前） | 5–8（或 0.5） | 118–127 |

> 对 v3 的口径修正：v3 §3.2 中期记 28 人日（F1+A1 同批记 18），但分项为 F1 18 + A1 10–12 + P5 4 = 32–34；本文按分项实测口径排期，差异以 B9/B10 拆批消化，不隐藏超支。

## 4. 中期前置链（B9 / B10 / S2）

### B9 执行无状态化批（F1 主体 + P5）— 22–26 人日，2–3 迭代窗口

- **目标**：执行单元从 API 进程剥离为 worker；实例状态全落库；启动与心跳接管在途实例；cancel 传播到并行区域（裁撤兄弟分支、中断 LLM 流）。
- **锚点**：`execution_instance_router.py:1015/:1287`（create_task 退化为提交动作）；`engine/scheduler.py`（单游标 + B6 并行区域：:364/:438/:756–766）；检查点仓储双后端；B8c 三条已知特性为设计输入（同步 I/O 阻塞事件循环是 worker 化的直接实证）。
- **范围决策（批内 spec 定稿）**：worker 形态（先同进程独立 task group，独立进程为二阶段）；接管心跳窗口与租约；幂等键（instance_id 唯一约束防双跑）；执行状态表 schema。
- **出口验收**：`kill -9` worker → 在途实例被接管续跑（drill 脚本 + 记录留档）；两副本并发不重复执行；cancel 后并行分支全部终止、无僵尸节点（B2 409 竞态先例入回归）；全量 pytest 精确闭合 exit 0；四基准重跑 p95 漂移 ≤5%（linear_50 含 local_file 检查点 O(n²) 主导项——复测口径换 postgres 后端在 spec 定）。
- **风险**：调度语义回归面全项目最大——缓解为拆两子窗口（①状态落库 + 提交/执行分离 ②心跳接管 + kill -9 + 幂等），每窗口独立全量门禁；drill 脚本先于重构落盘。
- **进度（2026-11-23）**：M1 收口（`d2058a6`：执行队列抽象 + local_file/postgres 双后端 + 租约/心跳/接管语义 + v1_8 迁移 + 统一提交路径）；M2 收口（`4ff5597`：独立 worker 进程形态——`python -m orchestration_platform.worker` + compose `worker:` 服务 + 状态/事件读穿透 + EventType +4 + start_instance 透传 yaml_text + 优雅停机；kill -9 验证为模拟死亡集成测试，真实 drill 移交 M3 已补）；**M3 收口（0.3.0，见 `roadmap_m3_cancel_semantics` exit-record：P5 实例级取消与超时全链 + 双真实 drill（taskkill /F 强杀接管 6/6、两副本认领互斥 4/4）+ PG 接管原子性测试锁定 + cron 版本钉 + 全门禁（pytest 3584+3 / vitest 1007+13 / typecheck 13 / e2e 6/6 / 四基准 M3 vs M2 A/B 零回归——当日绝对值因机器内存状态漂移 +20~37%，A/B 证据在档）；D9 发现：heartbeat 续租无 TTL 参走 settings 默认，短租约部署需 env 全链对齐（欠账 M4+））**。
- **M3 小批输入（2026-11-23 登记）**：P5 cancel 语义（原计划）+ 并行 M2 移交/差异项——①真实 `kill -9` 进程 drill（非模拟）；②两副本认领并发 drill（M2 spec 非目标明确移交）；③四基准重跑 p95 ±5% + e2e 5 路径 + vitest/typecheck 全门禁（M2 T9 仅列 pytest + compose config）；④`recover`/`re_execute`/`review`/`cron` 四处调用点 yaml_text 载荷补齐（M2 仅 start_instance 透传，其余端点 worker 模式不完整）。**四项均已随 M3 收口处理（①②=双 drill；③=全门禁 A/B；④=cron 版本钉 + 其余端点欠账 M4+）**。

### B10 引擎解耦批（A1）— 10–12 人日，1 迭代

- **目标**：`engine/` 仅依赖 domain 与 abstractions；配置/仓储/事件出口全部经注入；OEM 裁剪从「前端 + 路由开关」下沉到装配层。
- **时点**：紧随 B9（同改调度器语义相邻，省一次回归）；若 B9 超支可与之换序后置，但须在 B14 前完成（T4 注入面受益于解耦边界）。
- **出口验收**：`engine/` import 清单零 `api`/`configuration` 直接依赖（脚本断言入 `wfp-check` 或 CI）；54 开关体系裁剪点收敛装配层并梳理成表；全门禁绿。
- **风险**：注入面过宽 → 拆两步（先配置/仓储，后事件出口）。
- **进度（2026-11-24）**：M4 收口（见 `roadmap_m4_engine_decoupling` exit-record：EngineSettings 17 字段 + build_engine_settings 装配点 + usage_recorder 协议注入 + adapter api 依赖移除 + IWorkflowEngine 删除（台账 44→43）+ AST 导入门禁（CI 插步，engine 67 文件 6 前缀零违规）+ 54 开关矩阵；全门禁绿——pytest 3735+3 / vitest 1010+13 / typecheck 13 / e2e 6/6 / 四基准匹配态 A/B 零一致回归（测量窗机器频率节流同码 3.2x 对称，fast-state 复测欠账）/ ruff 基线 ~5.4k 债务发现（CI 真实 ruff 从未绿），本批自增文件零违规）。

### S2 SSO 闭环（可插小批）— **已裁决：xmlsec 验签落地（2026-11-23）**

- **现状**：验签未实现（`saml_adapter.py:76` 注释自证）；`replay_guard.py` 已有重放防护骨架；B8a 已在对外 README 标 experimental（即时风险已缓解）。
- **裁决**：**xmlsec 验签 + 重放防护落地（5–8 人日），摘 experimental**——目标客户（国内 ISV/行业 SaaS 私有化）采购几乎必问 SSO，骨架已半成，补齐即消除 v3「假支持」最高级风险。
- **排期**：插班小批（建议 B15 交付加固前闭环；与中期窗口的衔接届时裁决）。

## 5. 长期批次详规（B11–B16）

### B11 定义版本与发布态批（F3，长期首位）— 9–10 人日

- **范围**：定义持久化存储（DB 表）+ `version`/`status`(draft|published)/`draft_of`；发布态不可变；实例绑定版本；回滚到指定版本。
- **锚点**：`workflow_definition_router.py`（现仅 validate/save，无持久化）；执行入口 YAML 内联（`execution_instance_router.py:332`）；domain 已有 `definition_version` 字段可承接。
- **出口验收**：发布态改写返回 409；实例可追溯执行版本号；回滚 drill 留档；**老路径（yaml_text 内联提交）保持兼容**——既有测试零修改为理想，必须改锚点时在 spec 登记。
- **风险**：schema 迁移与旧画布状态兼容；执行入口双轨期口径。

### B12 数据分块批（P3 核心）— 8–10 人日

- **范围**：取数与变换改游标/分块；内存上限与行数告警；spill-to-disk 可裁（超支先裁）。
- **锚点**：`engine/nodes/data_transform_ops/`（registry + handlers_{ai,cleaning,dev,structure} + masking）；`integrations/data_source_connectors/`；基准 `pipeline_100k`（p95 27281.2ms / 峰值 247.8MB）为验收标尺。
- **出口验收**：pipeline_100k 内存峰值目标 ≤150MB（spec 定稿数字）；p95 不劣化 >5%；OOM 路径专项测试。
- **F2 三模板（清洗/特征/同步）不排期**：v3 F2「P1 但延后」，待 N5 设计伙伴确认需求后另立批。

### B13 可视化与错误批（U2+U3）— 13 人日

- **U2 并行可视化**：后端状态已在（scheduler 区域/join 语义、`get_parallel_positions`）——补实例详情 API 暴露分支状态与 join 等待原因；前端泳道或甘特视图（`WorkflowCanvas.tsx` 920 行，改动以 vitest 123 文件为安全网）。
- **U3 错误可行动化**：`ValidationIssue` 已有 node_id/edge_id/location——补字段级定位、前端一键跳转画布节点、执行失败附输入快照。
- **出口验收**：并行执行可在 UI 解释（分支状态 + join 等待原因可见）；校验错误一键定位到节点+字段；vitest 新增守护；e2e 路径扩充。
- **前置**：B8d 十连绿在档（不满足则本批顺延，先做 B14）。

### B14 契约与故障批（T2+T4）— 11 人日

- **T2 契约测试**：OpenAPI snapshot + Python SDK 契约用例（前端契约面 `apiTypes.ts` 234 行 / `orchestrationApiClient.ts` 277 行纳入 snapshot 对照）；TS 对拍待 F6 解冻（v3 F6 为 P2 冻结项），本批不手写 TS SDK。
- **T4 故障注入**：四类——PG 断连、LLM 超时、webhook 5xx、worker `kill -9` 后续跑（依赖 B9）；脚本入 `scripts/`。
- **出口验收**：四注入全绿并留档；契约 snapshot 纳入本地门禁（CI 待 remote 后接入）。

### B15 交付加固批（S3+S4+S5）— 13 人日

- **S3 镜像供应链**：Dockerfile 三阶段 + 非 root 已在——补 SBOM、镜像扫描、基础镜像 digest 锁、只读根文件系统。
- **S4 备份恢复与升级演练**：PG（compose `postgres-data` 卷）+ `.data/` 备份脚本；迁移回滚演练；升级 runbook；**RTO 有实测数字**。
- **S5 离线交付包**：镜像 tar + 依赖 wheels + 校验和 + 安装脚本；**离线环境从零到可用 drill 留档**。
- **依赖**：S4 恢复演练 ← T4（B14）；S2 建议已闭环（见 §4）。

### B16 运营面批（F4+F5）— 11 人日

- **F4 HITL 运营面**：suspend/resume 已接（`execution_instance_router` resume_checkpoint / suspended_checkpoint_id）——补审批队列、超时提醒、批量处理、窄屏可用。
- **F5 订阅管理面**：后端 API 全备（`event_export_router.py:149–:266`）——补订阅 CRUD UI、投递日志、手动重投、死信查看、签名密钥轮换；订阅按版本生效（依赖 B11）。
- **出口验收**：审批与订阅可自助运营；e2e 路径扩充至运营面；全门禁绿。

## 6. 备选池（不排期，不得偷偷加塞）

T3 性能 CI 门（3，先以本地 gate 脚本代 CI）；A7 文档一致性门禁（3）；A2 校验器拆分（5–7）；A3 配置分域（5）；A6 契约单一源（5，F6 联动）；S1 KMS/Vault（6）；P2 运行时背压（6–8）；P4 OTel/Prometheus（8）；P6 检查点治理（3–4）；U4 引导（3）；U5 无障碍（4）；U6 i18n（6–8，P3 长期）；F2 全量（20–25，N5 触发）；F6 TS SDK（6–8，N5/A6 触发）；S6 审计留存（4）；S7 配额运营面（5）；T5 覆盖率门槛（2）；T6 变异测试（5）。

## 7. 门禁与对账纪律（常设，沿 B3–B8 惯例）

1. 每批出口记录五节 + 单提交 `main` + CHANGELOG；API 变更按 semver 升 0.3.0（B11/B16 大概率触发）。
2. 全量 pytest 精确闭合对账（当前基线 **3490 = 3487 + 3**，增量按来源归因，参照 B3-D5 并行对账纪律）；vitest **1007+13（123 文件）** 不回退；typecheck **13 零新增**；e2e 5 路径绿（B8d 收口后升为常设门）；触执行路径的批次四基准重跑 p95 漂移 ≤5%。
3. 已知 flake `test_deterministic_serialization`：失败即全量复跑，登记不修改。
4. **在途会话边界**：本文落档动作零触碰 B8d 范围（`web/e2e/`、`web/playwright.config.ts`、`web/vite.config.ts`、`docs/testing.md`、v3 附录 B）；B9 启动前置 = B8d 出口记录落档。

## 8. 风险与裁剪顺序

| # | 风险 | 缓解 |
|---|---|---|
| 1 | B9 回归面全项目最大 | 拆两子窗口 + drill 脚本先行 + B6 14 例/e2e 5 路径为网 |
| 2 | B8d e2e 未达十连绿 | B13 顺延，先做 B14；B9 不受影响 |
| 3 | 容量超 v3 120 上限（本路径合计 118–127） | 按序裁剪：①S2 走降级声明（−5~7）②B12 裁 spill-to-disk（−2）③B13 仅保 U3 后置 U2（−6~8）④B15 的 S3 只做 SBOM+扫描（−3） |
| 4 | N5 无试点签约 | F2/F6/U6 维持冻结；B12 不含模板；对外验证依赖 N4 基准与 demo |
| 5 | PyPI/remote 凭据长期缺位 | N3 收口停滞不阻塞代码批次；每批出口在 CHANGELOG 留发布面备注 |

## 9. 执行状态与后续动作

1. **P1 已执行（2026-11-22）**：本文落档 + README 文档索引 +1 行；与在途 B8d 零文件冲突。
2. **P2 已执行（2026-11-22）**：B8d e2e 批收口落 `main`（提交 `4d93831`，出口记录五节在档，v3 附录 B 已随批刷新）后，在 v3 §第三部分头部补本文交叉链接；并发冲突窗口解除后执行。
3. **P3 两项裁决已定（2026-11-23）**：①B9 启动——已启动（M1 执行队列与统一提交路径落 `d2058a6`；M2 由并行会话以 `roadmap_m2_worker_process`（独立 worker 进程形态）执行中，本会话同日三件套（同进程形态）因撞批撤回，移交/差异项已登 §4 B9「M3 小批输入」）；②S2 走向——**xmlsec 验签落地**（摘 experimental，5–8 人日，插班时点建议 B15 前）。
