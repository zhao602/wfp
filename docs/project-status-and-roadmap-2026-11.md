# 项目现状体检与新规划（2026-11-20）

> 定位：**单一事实源**。回答六个问题——目标是什么、已完成什么、架构长什么样、问题在哪、进度多少、接下来做什么。
> 竞品与路线论证不在此重复，见 `docs/differentiation-strategy-2026-11.md`；能力矩阵与代码位置见 `docs/architecture-review-2026-11.md`。
> 本文所有数字为 2026-11-20 实测或标注出处的记录值，口径冲突见附录 A。

---

## 0. 口径与冲突处置

**实测时点**：2026-11-20。复跑命令：`find src -name "*.py" | wc -l`、`grep -rh "@router\." src/orchestration_platform/api/routers/ | wc -l`、`python -m pytest -q`。

**三份文档的分工**

| 文档 | 回答什么 | 不该在这里找什么 |
| --- | --- | --- |
| 本文 | 现在在哪、要去哪、怎么去 | 代码级能力矩阵细节、竞品论证 |
| `architecture-review-2026-11.md` | 每个能力实现了没有、证据在哪 | 排期与批次 |
| `differentiation-strategy-2026-11.md` | 为什么选这条路、不做什么 | 任务拆解 |

**数字存在多口径**（29/30/36 篇文档；测试 2797/2953/3190/3250 例；py 文件 469/501）。处理原则：**以实测为准，旧文档不改，只在附录 A 加交叉注记**。旧数字是历史快照，不改它们才有可追溯性。

---

## 1. 项目目标

**一句话**：一个可被第三方产品嵌入的、声明式（YAML 契约）的 Agent 工作流编排与治理内核。

**三层目标（由外到内）**

| 层 | 目标 | 判据 |
| --- | --- | --- |
| 产品 | 让 ISV / 行业 SaaS 把自己的流程内核换成它，界面仍由客户自己做 | 有外部产品跑在它上面 |
| 技术 | 同一份 YAML 可校验、可 diff、可进 CI、可在第二种运行时复现 | `wfp validate` 被外部仓库使用 |
| 治理 | 审计、计量、配额、降级同源于一条事件流 | 客户能导出审计、能按租户对账 |

**明确不做**（详见 differentiation §4）：模板市场、插件市场、模型聚合面板、Prompt IDE、零代码 C 端 Bot、自研向量库、通用 RAG 问答、NL Copilot。

**北极星指标**（当前全为 0，这是判断「是否真的在推进」的唯一硬指标）
1. 外部仓库把 `wfp validate` 放进 CI 的数量；
2. 把引擎嵌入自有产品的外部集成方数量；
3. 同一份 YAML 在两种运行时跑出等价结果的用例数。

---

## 2. 已完成内容

### 2.1 规划批次（有台账、有基线）

| 批次 | 内容 | 结果 | 证据 |
| --- | --- | --- | --- |
| Wave1 `arch_review_v2_tasks` | 52 个子任务 + 12 个 GK | 全部勾选 | `.codeartsdoer/specs/arch_review_v2_tasks/tasks.md` |
| Wave2 P1–P6 | 台账、事件总线+审计落盘、HITL、Cron、重放、终验 | 6 批完成，基线 2990→3190 例，全 exit 0；实施 25 人日 | `arch_review_v2_tasks_wave2/batch-records.md:176-234` |
| `arch_review_gap_b1` 批 | 13 项 | 收口 | `docs/roadmap-dispositions-2026-11.md:183-193` |
| `arch_review_gap_b4` 批 | 8 项 | 收口 | 同上 |
| `arch_review_gap_b2` 批 | 21 项 | 18/21，T6/T7 延后 | `arch_review_gap_b2_observability_perf/tasks.md:42-49` |
| `arch_review_gap_b3_ecosystem` 批 | 生态 8 项 | **未开始**（目录不存在） | — |
| cleanup_dead_code_v2 | 144 项 | **暂停**，登记漂移待重锚 | `cleanup_dead_code_v2/validation_records.md:144-152` |
| roadmap B0 断链修复 | 表名对齐迁移 `v1_5` + 生产 profile 强制 jwt + 拦截器 6/6 | 收口（2026-11-21，收集 3288 exit 0，PG 四步演练 + 六仓储冒烟全过） | `.codeartsdoer/specs/roadmap_b0_broken_link_fix/tasks.md` |
| roadmap B1 多租户强隔离 | `v1_6` 补 `tenant_id` + `v1_7` RLS 24 策略 + 58 跨租户负向用例 | 收口（2026-11-21，收集 3317 exit 0） | `.codeartsdoer/specs/roadmap_b1_tenant_isolation/tasks.md` |
| roadmap B2 嵌入 SDK | `wfp-sdk` Python 包（提交/触发/订阅/取消）+ 隔离 venv demo | 收口（2026-11-22，收集 3382 exit 0，`DEMO PASS`） | `.codeartsdoer/specs/roadmap_b2_embedded_sdk/tasks.md` |
| roadmap B3 事件出口 | Webhook/SSE + 签名/重投/死信 + 三订阅方扇出 | 收口（2026-11-22，收集 3382 exit 0，0 丢失 6/6、p95 < 2s） | `.codeartsdoer/specs/roadmap_b3_event_export/tasks.md` |
| roadmap B4 计量配额闭环 | 配额闸门 429 + 执行级记账 + 对账 CLI | 收口（2026-11-22，收集 3390 exit 0，对账零差异） | `.codeartsdoer/specs/roadmap_b4_metering_quota/tasks.md` |
| roadmap B5 OEM 白标 | 构建期品牌/主题/模块开关 + 双产物 | 收口（2026-11-22，收集 3421 exit 0，双品牌 16 项断言） | `.codeartsdoer/specs/roadmap_b5_oem_whitelabel/tasks.md` |
| roadmap B6 并行扇出 | 编译期区域激活 + 调度器双路径 + `WAIT_ALL/ANY/N_OF_M` + 前沿恢复 | 收口（2026-11-22，收集 3431 exit 0，23 新用例） | `.codeartsdoer/specs/roadmap_b6_parallel_fanout/tasks.md` |
| roadmap B7 收割批 | usage 端点真实化 + 节点级 token 记账 + 双默认租户统一 + 执行链穿参 + 订阅持久化 + P7 版本护栏 | 收口（2026-11-22，收集 3468 exit 0，37 新用例，B0–B6 移交池四组收割闭环） | `.codeartsdoer/specs/roadmap_b7_harvest/tasks.md` |

### 2.2 能力交付（按用户可感知的事说）

- **能跑、能恢复**：检查点已接线（`api/application_factory.py:131`、`engine/scheduler.py:288/751`）；人工审核挂起/恢复可用（`scheduler.py:562/589`、`api/routers/execution_instance_router.py:698`）；回滚重放不再返回假成功（`api/routers/rollback_replay_router.py:195/291`）。
- **能审计**：审计事件订阅已挂载（`api/application_factory.py:601-639`），查询返回真实条目。
- **能进 CI**：`wfp` CLI 四命令真实实现（`pyproject.toml:40-41`；`cli/commands/diff.py:52` 接 `workflow/semantic_diff/engine.py`），`.github/workflows/wfp-check.yml:78/87` 已在 PR 上跑 validate 与 diff。
- **能一键起**：`Dockerfile`、`docker-compose.yml`、`ci.yml` 均在位。
- **能定时**：Cron 触发已落地（Wave2 P4）。
- **前端已拆**：`NodeConfigurationPanel.tsx` 由 3710 行降到 411 行，节点表单拆到 `web/src/canvas/panels/*`（12 个子表单）。
- **契约有第二运行时**：LangGraph 适配 compile/start 为真实实现，等价矩阵 8 例 + `docs/langgraph-equivalence-matrix.md`（默认关，`configuration/settings.py:65`）。

---

## 3. 核心模块与架构

### 3.1 规模（2026-11-20 实测）

| 维度 | 数值 |
| --- | --- |
| 后端 | 501 个 `.py` / 71,807 行 |
| 前端 | 276 个 `.ts` / `.tsx` / 57,344 行 |
| API | 顶层路由模块 19 个（另有 `governance/` 子目录）、127 个端点、`application_factory.py` 32 处 `include_router` |
| 管理后台 | 18 个面板（`web/src/admin/menuConfig.ts`） |
| 测试 | 后端 119 unit + 14 integration；前端 121 文件 998 例；后端最近记录 3250 例 |
| 文档 / 范例 | 36 篇 / 12 套 15 个 YAML |

### 3.2 分层

```
web/src (画布 + 18 面板)
   │  REST 127 端点
api/ ── application/ ── engine/ (编译 → 单游标调度 → 14 类执行器)
                       ├─ governance/ (拦截器链 · 事件总线 · RBAC · 租户 · 审计 · 计量 · 调度)
                       ├─ integrations/ (LLM · MCP · 数据源 · 沙箱 · 向量)
                       └─ persistence/ (Protocol + local_file | postgres)
```

### 3.3 成熟度三级（判断「能不能对外说」）

| 级别 | 含义 | 代表模块 |
| --- | --- | --- |
| 可交付 | 有实现、有接线、有测试 | 解析与校验链、编译、单游标调度、检查点/HITL/重放、`wfp` CLI、数据变换算子、run_log |
| 半成品 | 有实现，接线不完整 | 事件总线（publish 6 处 / subscribe 1 处）、治理拦截器（3/6）、LangGraph 适配（3 方法为桩）、计量配额 |
| 声明态 | 只有枚举、字段或文档 | 并行扇出（`enums.py:185`、`node_configurations.py:231`）、多租户 RLS、嵌入 SDK、OEM 构建 |

> 2026-11-22 注记：B0–B6 全部收口后，本表「声明态」四项已全部交付（并行扇出→B6、多租户 RLS→B1、嵌入 SDK→B2、OEM 构建→B5）；「半成品」行的事件总线已扇出改造（B3）、治理拦截器已 6/6（B0）、计量配额已闭环（B4）；「可交付」新增并行扇出调度。本表保留为 2026-11-20 快照，最新口径见 §2.1 收口行与 §7 批次表注记。

---

## 4. 当前进度

**加权完成度 ≈ 47%**（方法：按 `architecture-review` §11 的 148 人日口径加权，已完成部分按实际接线比例折算；`arch_review_gap_planning/progress_matrix.md:11-19` 在 2026-11-18 记的是 25%，Wave2 之后显著提升）。

| 能力域 | 完成度 | 缺口 | 证据 |
| --- | --- | --- | --- |
| 层 0 入场券（检查点/护栏/HITL/重放/CI） | **100%** | — | 见 §2.2 |
| R4 CLI / GitOps | **95%** | 示例示范仓未建 | `pyproject.toml:40` |
| R1 嵌入内核 | **40%** | 无 SDK、无多租户强隔离、无配额闭环、无 OEM 构建 | 无 `sdk/` 目录；`governance_factory.py:133-139` 只装 3 个拦截器 |
| R2 数据管道 | **20%** | 并行扇出缺失；流式仅小块；连接器未扩 | `engine/scheduler.py:348` 仍是 `while current_node_id` 单游标 |
| 安全 | **60%** | JWT 已落地但默认兼容明文；SSO 未验签 | `settings.py:414-416`、`sso/saml_adapter.py:79` |
| 数据层 | **30%** | 治理表建了但装配断（表名不符）；无 RLS | 附录 A 第 6 条 |
| 可观测 | **50%** | `/metrics` 是 JSON；无 Prometheus/OTel | `api/application_factory.py:280-282` |
| 前端 | **75%** | 无路由、无代码分割（自研替代）；dead code 未清 | `web/src/router/hashRouter.ts`、`admin/panelLazy.tsx` |

> 2026-11-22 注记：B0–B6 收口后本表已过时——R1 四缺口已补（SDK→B2、多租户强隔离→B1、配额闭环→B4、OEM 构建→B5）、R2 并行扇出已落地（→B6，含 `WAIT_ALL/ANY/N_OF_M` 与前沿恢复）、安全 D2 已修复（B0 生产 profile 强制 jwt）、数据层 D1/D3 已修复（B0 表名对齐 + 拦截器 6/6）。加权完成度待 B7 收割批后统一复测刷新，本表保留为 2026-11-20 快照。

---

## 5. 主要问题与风险

### 5.1 P0：三处断链（会直接让「交付」失败）

| # | 问题 | 证据 | 后果 |
| --- | --- | --- | --- |
| D1 | 治理仓储查 `governance_org_node` / `governance_role`，但迁移脚本建的是 `org_nodes` / `roles`，全仓无 RENAME 补偿 | `governance/persistence/postgres/postgres_org_tree_repository.py:44/67` vs `persistence/postgres/schema_migrations/v1_2_phase3_governance.sql:21` | 切 postgres profile 装配即失败，治理功能整体不可用 |
| D2 | 令牌默认 `token_verify_mode="both"`，JWT 失败后回退明文解析 | `configuration/settings.py:414-416`、`governance/rbac/permission_checker.py:110-115` | 存量兼容是刻意设计，但对外交付意味着「可伪造令牌」仍成立 |
| D3 | 拦截器链只装 auth / tenant_isolation / quota 三个，metering / budget / audit 类存在但无实例化 | `governance/governance_factory.py:125-140` | 计量与预算形同虚设，配额无数据支撑 |

> 2026-11-22 注记：D1/D2/D3 已由 B0 断链修复批全部修复并收口（2026-11-21，收集 3288 exit 0）——`v1_5_rename_governance_tables.sql` 表名对齐、生产 profile 强制 jwt 且明文路径 fail-fast、拦截器装配 6/6，PG 四步演练与六仓储冒烟全过。档案：`.codeartsdoer/specs/roadmap_b0_broken_link_fix/`。本节保留为 2026-11-20 快照。

### 5.2 P1：结构性缺口（对应 R1 主线）

- **无嵌入 SDK**：全仓无 `sdk/`、`client/`；外部只能直接调 REST，集成成本按周计。
- **无多租户强隔离**：`persistence/postgres/table_definitions.sql` 仅 4 张表、零 `tenant_id`；全仓无 `ROW LEVEL SECURITY` / `CREATE POLICY`。
- **事件出口缺失**：有事件总线但只有审计一个订阅方，客户无法订阅执行进度。
- **未完成标记**：后端 47 处 `TODO(tech-debt)`（与台账 47 个文件一一对应）、12 处 `NotImplementedError`。

### 5.3 P2：能力声明与实现不符（对外表述风险）

- **并行扇出**：枚举与配置字段都在，调度器仍是单游标顺序执行——UI 能画、跑不起来。
- **LangGraph**：`resume/rollback/replay` 抛错（`engine/adapter/langgraph_adapter.py:208/231/246`），文档声称的等价只覆盖顺序/条件/单层循环。
- **治理 15 表**：`release_v1.4.0.md` 与 `phase4_ecosystem_summary.md` 称可用，实测 D1 断链——统一表述应为「建表在、装配断」。

> 2026-11-22 注记：并行扇出已由 B6 落地（编译期区域激活 + 调度器双路径，激活形态范例 `examples/parallel_execution/parallel_fanout.yaml`，23 个新用例，收集 3431 exit 0，档案 `.codeartsdoer/specs/roadmap_b6_parallel_fanout/`）；治理 15 表「装配断」已由 B0 修复；LangGraph 三桩仍在 §7 冻结清单，不变。

### 5.4 风险登记

| 风险 | 概率/影响 | 缓解 | 触发信号 |
| --- | --- | --- | --- |
| 单人单点，迭代断裂 | 中/高 | 每批 ≤12 人日，批界留可回滚点 | 连续 2 天无进度 |
| 表名 RENAME 在存量库失败 | 中/高 | `IF EXISTS` + 兼容视图 + 回滚脚本，空库与存量库各跑一遍 | 存量库迁移报错 |
| 启用 RLS 后仓储大面积空结果 | 高/高 | 先补 `tenant_id` 并做影子校验，再开策略 | 单测空结果突增 |
| 无真实客户，SDK 契约返工 | 中/中 | SDK 锁 0.x，契约对齐 YAML spec，保留 raw 透传 | 试点提出不兼容需求 |
| OEM 做完无人用 | 高/高 | B5 置末、验收降为「可演示」，先自建 Demo SaaS | 第 4 迭代仍无试点 |
| 扇出破坏检查点/重放 | 中/高 | checkpoint 先加 `branch_id`，replay 用例先行 | replay 失败率上升 |
| 范围蔓延（顺手清 TODO） | 中/中 | 批内禁清理，一律登记 `tech-debt-ledger` | 批内非计划文件 >20% |

---

## 6. 阶段目标（6 个迭代后，约 2027-02 中）

四条终态，每条都可被外部验证：

1. **可外部嵌入**：外部开发者用 `pip install` 拿到 SDK，30 行内跑通「提交定义 → 触发 → 订阅 → 取结果」。
2. **租户强隔离**：两个租户的数据在 SQL 层互不可见，跨租户负向用例全拒。
3. **计量闭环**：每次执行产生一条用量记录，超配额 100% 拒绝执行，对账零差异。
4. **白标产物**：单次构建出两套品牌，关闭模块的端点返回 404 且菜单不渲染。

**中间里程碑**：迭代 2 末（B0+B1 完成）——可以谈「可私有化多租户」；迭代 4 末（B2+B6）——可以谈「可嵌入 + 并发」；迭代 6 末——可以谈「可白标交付」。

---

## 7. 关键任务与批次

> 编号说明：本节 `B0`–`B6` 是**本规划的批次**，与 `.codeartsdoer/specs/arch_review_gap_b*` 的既有实施批次不是同一套编号，勿混用。

| 批次 | 迭代 | 内容 | 人日 | 前置 | 验收（可验证） |
| --- | --- | --- | --- | --- | --- |
| **B0** 断链修复（✅ 2026-11-21 收口，档案 `.codeartsdoer/specs/roadmap_b0_broken_link_fix/`） | 1 | 表名对齐 + 补 `tenant_id`；生产 profile 强制 jwt；拦截器补到 6/6 | 6 | — | postgres profile 启动成功、15 表可读写；明文令牌路径 0 命中；全量测试仍 exit 0 |
| **B1** 多租户强隔离（✅ 2026-11-21 收口，档案 `.codeartsdoer/specs/roadmap_b1_tenant_isolation/`） | 1–2 | 仓储基类强制注入 `tenant_id` + PG RLS 双保险 + 跨租户负向用例 | 12 | B0 | 业务表策略覆盖 100%；无租户上下文查询返回 0 行；跨租户用例 ≥20 全拒 |
| **B2** 嵌入 SDK（✅ 2026-11-22 收口，档案 `.codeartsdoer/specs/roadmap_b2_embedded_sdk/`） | 2–3 | Python SDK：提交/触发/订阅/取消 + 错误码与 YAML 校验错误同构 | 10 | 契约冻结 | 隔离 venv 安装后跑通端到端 demo；不泄漏内部模块 |
| **B3** 事件出口（✅ 2026-11-22 收口，档案 `.codeartsdoer/specs/roadmap_b3_event_export/`） | 4 | Webhook / SSE、签名、重投与死信 | 8 | B2 | 断连重投 0 丢失；投递 p95 < 2s；订阅方 ≥2 |
| **B4** 计量配额闭环（✅ 2026-11-22 收口，档案 `.codeartsdoer/specs/roadmap_b4_metering_quota/`） | 5 | 拦截器记账 + 对账 + 超配额 429 | 8 | B1 | 超配额 100% 拒绝且未执行；用量行数 = 执行次数 |
| **B5** OEM 白标（✅ 2026-11-22 收口，档案 `.codeartsdoer/specs/roadmap_b5_oem_whitelabel/`） | 6 | 构建期 logo/主题/模块开关 + 双产物 | 6 | B1,B4 | 单次构建出 2 套品牌；关闭模块端点 404 且菜单不渲染 |
| **B6** 并行扇出（✅ 2026-11-22 收口，档案 `.codeartsdoer/specs/roadmap_b6_parallel_fanout/`） | 3–4 | 就绪队列 + 等待计数 + `WAIT_ALL/ANY/N_OF_M` + 状态合并 | 8 | B0 | 收敛正确（≥15 新用例）；replay 全通过；线性流 p95 退化 <5% |

**合计 58 人日**。若 B1 采用分库方案（25 人日口径），整体后延约 1.5 个迭代。

### 不进本规划（留痕，不排期）

LangGraph `resume/rollback/replay` 转真、SSO 真验签、Prometheus/OTel、dead code v2 的 144 项、市场功能复活、前端 router 迁移、R2 流式深化、`arch_review_gap_b3_ecosystem` 生态批。

**理由**：这些要么不服务 R1 主线，要么在无试点客户时 ROI 无法判断。它们不是被否定，是被推迟到有证据时再解冻。

> 需要显式说明：内部批次指针（`arch_review_gap_b2` 收口记录）写的「下一待实施批次 = B3 生态」与本规划不一致。本规划**主动改道**为 R1 主线（用户 2026-11-20 决策），B3 生态批顺延。这是排期决策，不是遗漏。

---

## 8. 优先级

| 级 | 内容 | 判据 |
| --- | --- | --- |
| **P0** | B0 三处断链 | 不修则后续所有「多租户/计量」都是空转 |
| **P1** | B1 隔离、B2 SDK | R1 主线的两根支柱，缺一不可对外 |
| **P2** | B3 出口、B4 计量、B5 白标 | 商业化闭环，依赖 P1 |
| **P3** | B6 并行扇出 | R2 楔子，只做这一个，不展开 |
| 冻结 | §7 末尾清单 | 无证据不投入 |

---

## 9. 交付物

| 批次 | 交付物 | 形态 |
| --- | --- | --- |
| B0 | 迁移脚本 `v1_5_rename_governance_tables.sql` + 回滚脚本；`token_verify_mode` 生产强制；拦截器 6/6 | SQL + 代码 |
| B1 | `tenant_id` 列与策略迁移、仓储基类改造、≥20 个负向用例 | SQL + 代码 + 测试 |
| B2 | `wfp-sdk`（Python 包）+ 端到端 demo 脚本 | 包 + 示例 |
| B3 | 事件出口模块 + 签名/重投/死信 + 订阅方示例 | 代码 + 文档 |
| B4 | 计量对账脚本 + 超配额 429 契约 | 代码 + 脚本 |
| B5 | OEM 构建脚本，单次产出 2 套品牌 | 构建产物 |
| B6 | 扇出/汇聚实现 + ≥15 用例 + 等价边界文档更新 | 代码 + 文档 |
| 全程 | 本文档；迭代出口记录；`tech-debt-ledger` 增删 | Markdown |

**测试增量目标**：+70 例（B0 10 / B1 25 / B2 10 / B3 10 / B4 8 / B5 3 / B6 15 的 60% 折算），且每次迭代出口必须 `exit 0`。

---

## 10. 依赖关系

```
B0 ──┬─→ B1 ──┬─→ B4 ──→ B5
     │        └─→ (B5 另需 B4)
     └─→ B6（可与 B1/B2 并列插入）
B2 ──→ B3
```

- **强阻塞**：B0 是全部前置（表名不通，B1/B4 装配即失败）；B1 → B4/B5（无隔离不敢对外）；B2 → B3（出口契约以 SDK 为准）。
- **顺序可换**：B1 ↔ B2（代码域不重叠，但须先冻结租户 header 与 schema 约定）；B3 ↔ B4（无强耦合，B4 先行需 B1 就绪）。
- **可插入**：B6 只依赖 B0，任意空档都能塞，且它不阻塞任何批次——这是唯一「闲时做」的任务。

---

## 11. 迭代节奏与门禁

**节奏**：1 人 / 2 周 / 约 10 人日。迭代节奏表：

| 迭代 | 周期 | 批次 | 人日 | 出口主题 |
| --- | --- | --- | --- | --- |
| 1 | 11-23 ~ 12-04 | B0 6 + B1 4 | 10 | 治理可启动 |
| 2 | 12-07 ~ 12-18 | B1 8 + B2 2 | 10 | 多租户可信 |
| 3 | 12-21 ~ 01-01 | B2 8 + B6 2 | 10 | SDK 可用 |
| 4 | 01-04 ~ 01-15 | B6 6 + B3 4 | 10 | 可嵌入 + 并发 |
| 5 | 01-18 ~ 01-29 | B3 4 + B4 6 | 10 | 出口 + 计量 |
| 6 | 02-01 ~ 02-12 | B4 2 + B5 6 | 8 | 可白标交付 |

> 2026-11-22 注记：七个批次（B0–B6，58 人日）已于 2026-11-22 提前全部收口（原排期至 2027-02 中）；各迭代出口门禁由各批出口记录承担（`.codeartsdoer/specs/roadmap_b{0..7}_*/tasks.md`，最近记录 3468 collected / exit 0——B7 收割批 2026-11-22 收口），节奏表保留为原排期快照。

**迭代出口门禁**（四条全过才算完成）
1. 全量 `pytest -q` exit 0，且新增用例数 ≥ 计划值；
2. 前端 `vitest run` 不新增失败；
3. 涉及迁移的批次必须附「空库 + 存量库」双向演练记录；
4. 批次范围外的改动一律登记 `tech-debt-ledger`，不夹带。

**缓冲**：58 人日塞进 60 人日容量，仅 2 人日余量——这是本规划最脆弱的地方。缓解：B5 可整体后延（不阻塞任何事），B6 可拆半批。

---

## 12. 待确认事项（不阻塞本次交付，但会改规划）

| # | 问题 | 缺了会怎样 |
| --- | --- | --- |
| 1 | 是否有真实客户 / 试点？ | 决定 B2、B5 验收是「演示级」还是「交付级」；无试点则 OEM 应降到最后 |
| 2 | 许可证选什么（Apache-2.0 / 商业双授权）？ | 直接改 B5 白标条款与「不可去 logo」的实现方式 |
| 3 | 私有化交付形态（离线镜像 / 单机 / K8s）？ | 改 B5 产物形态与依赖裁剪，人日 ±3 |
| 4 | 目标租户规模与隔离强度（单库 RLS / 分库）？ | B1 是 12 还是 25 人日，超预算即触发整体重排 |
| 5 | 前端是否公司门面？ | 决定 dead code v2 与 router 迁移是否要提前进规划 |

---

## 附录 A：口径冲突对照

| # | 现状描述 | 出处 | 实测 | 处置 |
| --- | --- | --- | --- | --- |
| 1 | 「Wave1 64/64 全勾」vs「P5/P6/P7 豁免未实施」 | `progress_matrix.md:145` vs `roadmap-dispositions:120` | P5（`pyproject.toml:40`）、P6（`cli/commands/diff.py:52`）已交付 | 采实测；豁免条标「已过期」，P7 已补（2026-11-22 B7 W4：`docs/spec-versioning.md` + `tests/unit/test_spec_version_guard_snapshot.py` 快照护栏 3 例） |
| 2 | 文档 29 / 30 篇 | `architecture-review:3`、`DEVELOPMENT.md:30` | **36 篇** | 以 36 为准，旧值视为历史快照 |
| 3 | 后端 469 py / 5.7 万行；前端 245 文件 | `architecture-review:3` | **501 / 71,807**；**276 / 57,344** | 以实测为准 |
| 4 | 测试 2797 / 2953 / 2990 / 3190 例 | 各阶段记录 | 最近记录 **3468**（B7 收口，2026-11-22，3465 passed / 3 skipped） | 统一写「当前 3468，exit 0」，历史值仅溯源 |
| 5 | 「README 停留在第一阶段」 | `DEVELOPMENT.md:37` | README 已改写为四层能力面 | 该行登记为文档债，待修 |
| 6 | 「治理 15 表可用」 | `release_v1.4.0.md`、`phase4_ecosystem_summary.md` | 建表在，仓储查名不符（D1） | 统一表述为「建表在、装配断」 |
| 7 | 市场方向「D4 冻结」vs README「生态层含市场」 | `roadmap-dispositions:20` vs README | 代码在、默认关 | 统一表述为「保留、默认关、不推广」 |
| 8 | 「下一待实施批次 = B3 生态」 | `.workbuddy/memory/2026-11-20.md:7` | 本规划主线为 R1 | 主动改道，已在 §7 注明；B3 顺延不删 |

---

## 附录 B：度量基线（后续复盘用）

| 指标 | 当前值 | 采集方式 |
| --- | --- | --- |
| 后端代码 | 501 文件 / 71,807 行 | `find` + `wc -l` |
| 前端代码 | 276 文件 / 57,344 行 | 同上 |
| API 端点 | 127 | `grep -rh "@router\." api/routers \| wc -l` |
| 测试用例 | 3468 collected（3465 passed / 3 skipped，B7 收口 2026-11-22） | `pytest -q` |
| 前端测试 | 123 文件（vitest 1007 passed + 13 skipped，B5 出口） | `find web/src -name "*.test.ts*"` |
| 治理表 | 17 张（v1_2 16 + v1_5 `governance_permission`），4 张（`table_definitions.sql`），RLS 24 策略（`v1_7`） | `grep -c "CREATE TABLE"` |
| 未完成标记 | 44 TODO（2026-11-22 实测；B0 销项 3）/ 12 NotImplementedError | `grep -rc "TODO(tech-debt)"` |
| 范例 | 13 套 / 16 YAML（B6 增 `parallel_execution/parallel_fanout.yaml`） | `ls examples/` |
| 加权完成度 | ≈47%（2026-11-20 快照，待 B7 收割批后复测） | 本文 §4 方法 |

---

## 附：与其他文档的关系

- `docs/architecture-review-2026-11.md`：能力矩阵与代码证据（本文 §4 的上游）
- `docs/differentiation-strategy-2026-11.md`：路线论证与竞品基线（本文 §1、§6 的上游）
- `docs/roadmap-dispositions-2026-11.md`：77 条处置台账（本文 §2.1 的上游）
- `.codeartsdoer/specs/arch_review_v2_tasks*/`：Wave1 / Wave2 执行记录
- `docs/tech-debt-ledger.md`：47 个待接线文件清单
