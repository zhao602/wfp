# roadmap-dispositions-2026-11.md — 既有 SDD 目录处置主台账

> **生成**：2026-11-18（arch_review_v2_tasks 任务 1.2，CHG-40 / DC-02）
> **权威关系**：本台账为「处置维度」唯一权威汇总（77 进 77 出，AC-07 / GK-10）；`docs/tech-debt-ledger.md` 为 B 档孤儿唯一权威清单，两者并存、互相引用、不合并（DC-02）。
> **计数口径**：77 = `.codeartsdoer/specs/` 现存全部非归档目录（含本轮任务目录 `arch_review_v2_tasks` 自身条目）；`archived/`（9 子目录）单独标注、不计入 77（spec §6.1）。
> **判定优先级**：代码实测现状 > tasks 勾选 > 目录自述 > 目录命名（INV-08）；勾选统计为递归全 .md 口径（2026-11-18 实测，`done/todo`）。
> **基线佐证**：`PYTHONPATH=src python -m pytest -q` 收集 **2797 例 exit 0**（0 失败 / 3 skipped，2026-11-18 P0 首轮实测）。
> **档位定义**：D1 已交付保留 / D2 立即排期 / D3 引用立约 / D4 冻结 / D5 裁剪挂起（spec §4.1）。
> **存量处置默认**：保留不回滚；归档/删除须用户逐项确认（§8-D1，spec §6.1）。

## 1. 处置台账（77 条）

| # | 目录 | 勾选实测 | 档位 | 判定依据（代码实测/评审引用） | 增量标记 | 存量处置 | 解冻条件 | 落盘复核状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | add_edge_delete | 68/1 | D1 | 前端边编辑已交付（评审 §2.2 画布可用；勾选 68/69） | — | 保留 | — | 预判一致 |
| 2 | add_output_display_node | 70/0 | D1 | 输出展示节点已交付（评审 §2.2；70/70） | — | 保留 | — | 预判一致 |
| 3 | admin_entry_button | 22/2 | D1 | Admin 入口已交付（web/src/admin/AdminConsole.tsx:96 MENU_ITEMS 实测；22/24） | — | 保留 | — | 预判一致 |
| 4 | agent_creation_feature_audit | 0/0 | D1 | 审计类历史档案（无勾选清单；v2 无冲突，spec B.2 分析类） | — | 保留 | — | 预判一致 |
| 5 | agent_market_404_triage | 0/0 | D1 | 排查类历史档案（无勾选清单；triage 记录，v2 无冲突） | — | 保留 | — | 预判一致 |
| 6 | agent_market_user_development | 60/1 | **D4 冻结** | OP-02：评审 §4（L183）/§8（L325）删除模板/插件市场方向；实测 60/61 勾选基本完成、双市场开关默认 False（configuration/settings.py:94/:100） | frozen(ZF-05)；剩余 1 任务作废登记 | 保留不回滚 | ZF-05：出现低成本冷启动证据（原则上不解冻，需用户拍板） | 预判一致 |
| 7 | agent_model_selector | 13/1 | D1 | 模型选择器已交付（web/src Agent 配置面板；13/14） | — | 保留 | — | 预判一致 |
| 8 | agent_tool_marketplace | 19/1 | D1 | 市场底座已交付（src/orchestration_platform/market/ 11 py 实测存在；评审 §2.4 底座全量交付）；v2「市场不做」=不新增投入（Z-7） | 存量保留（Z-7） | 保留不回滚 | — | 预判一致 |
| 9 | arch_review_v2_tasks | 0/57 | **D2 立即排期** | 本轮任务链自身（W1–4 立约，52 子任务实施中；本文档所在目录） | 自身条目（active） | N/A（活跃任务） | — | 预判一致（自身） |
| 10 | cleanup_dead_code | 65/3 | D1 | 死代码清理已交付（65/68；基线 2797 exit 0 无回归佐证） | — | 保留 | — | 预判一致 |
| 11 | data_transform_preset_expressions | 5/0 | D1 | 数据变换预设表达式已交付（21 算子线，评审 §2 引擎现状；5/5） | — | 保留 | — | 预判一致 |
| 12 | datasource_config_panel_e2e | 39/0 | D1 | 数据源配置面板已交付（8 数据源连接器资产，spec 附录 A F17 引用；39/39） | — | 保留 | — | 预判一致 |
| 13 | examples_api_and_dev_auth | 0/19 | D1 | examples API 已交付（api/routers/example_library_router.py 实测存在；examples/ 12 套范例）；勾选未维护，以代码为准（INV-08） | — | 保留 | — | 预判一致 |
| 14 | expand_data_transform_ops | 11/0 | D1 | 21 算子已交付（B.2 引擎/节点类；评审 §2 引擎现状；11/11） | — | 保留 | — | 预判一致 |
| 15 | fix_admin_console_blank | 33/0 | D1 | 修复已交付（B.2 fix 类；33/33） | — | 保留 | — | 预判一致 |
| 16 | fix_agent_executor_llm_client | 84/0 | D1 | 修复已交付（84/84；基线无回归） | — | 保留 | — | 预判一致 |
| 17 | fix_agent_null_length | 0/0 | D1 | 文档型目录（无勾选清单）；agent_node_executor.py:64 fallback_llm_client Optional 守卫实测存在，修复随节点线交付 | — | 保留 | — | 预判一致 |
| 18 | fix_backend_test_failures | 27/7 | D1 | 测试修复已交付（基线 2797 exit 0 全绿佐证；27/34） | — | 保留 | — | 预判一致 |
| 19 | fix_backend_test_failures_batch2 | 0/46 | D1 | 勾选未维护（0/46）但基线 2797 exit 0 实测全绿（INV-08 代码实测优先） | — | 保留 | — | 预判一致 |
| 20 | fix_baseline_test_failures | 0/9 | D1 | 同上（0/9；基线 exit 0 佐证） | — | 保留 | — | 预判一致 |
| 21 | fix_canvas_clear_and_trigger_node_loss | 0/34 | D1 | 前端修复勾选未维护；画布功能实测可用（评审 §2.2 执行链路可用） | — | 保留 | — | 预判一致 |
| 22 | fix_canvas_drop_node | 0/0 | D1 | 文档型；画布拖拽已交付（评审 §2.2） | — | 保留 | — | 预判一致 |
| 23 | fix_datasource_executor_connector_missing | 131/0 | D1 | 数据源类修复已交付（B.2；131/131） | — | 保留 | — | 预判一致 |
| 24 | fix_datasource_pagination_semicolon | 79/0 | D1 | 同上（79/79） | — | 保留 | — | 预判一致 |
| 25 | fix_datasource_source_type_null_guard | 40/0 | D1 | 同上（40/40） | — | 保留 | — | 预判一致 |
| 26 | fix_datasource_test_connection | 0/0 | D1 | 文档型；测试连接功能已交付（B.2 数据源类） | — | 保留 | — | 预判一致 |
| 27 | fix_datasource_test_false_positive | 25/0 | D1 | 同上（25/25） | — | 保留 | — | 预判一致 |
| 28 | fix_empty_bearer_auth_header | 18/0 | D1 | 空 Bearer 守卫已交付（18/18） | — | 保留 | — | 预判一致 |
| 29 | fix_raw_not_iterable | 25/3 | D1 | 修复已交付（25/28；基线无回归） | — | 保留 | — | 预判一致 |
| 30 | fix_resource_pool_table_overflow | 38/0 | D1 | 修复已交付（38/38） | — | 保留 | — | 预判一致 |
| 31 | fix_sso_404_frontend_guard | 52/0 | D1 | 前端 404 守卫已交付（52/52）；非 ZF-07——该冻结项指服务端 python3-saml 验签，本目录为前端 404 页守卫 | — | 保留 | — | 预判一致 |
| 32 | fix_status_all_zero | 0/52 | D1 | 前端修复勾选未维护；状态显示已交付（评审 §2.2 画布运行状态链路可用） | — | 保留 | — | 预判一致 |
| 33 | fix_trigger_display | 22/2 | D1 | 触发显示修复已交付（B.2 运行/触发类；22/24） | — | 保留 | — | 预判一致 |
| 34 | fix_yaml_export_backend_align | 0/72 | D1 | 导出对齐先例已交付（勾选未维护；spec 附录 A F12「导出对齐先例」复用引用） | — | 保留 | — | 预判一致 |
| 35 | fix_yaml_export_button | 15/0 | D1 | 导出按钮已交付（15/15） | — | 保留 | — | 预判一致 |
| 36 | footer_export_button | 0/32 | D1 | 前端导出按钮已交付（B.2 杂项；勾选未维护，评审 §2.2 佐证） | — | 保留 | — | 预判一致 |
| 37 | frontend_debt_cleanup | 0/0 | D1 | 文档型目录（无勾选清单）；前端债务清理历史档案 | — | 保留 | — | 预判一致 |
| 38 | layout_resize | 0/36 | D1 | 布局调整已交付（B.2 UI 类；勾选未维护，画布可用佐证） | — | 保留 | — | 预判一致 |
| 39 | llm_and_code_nodes | 0/42 | D1 | LLM/代码节点已交付（engine/nodes/ llm_node_executor、code_node_executor 实测存在；节点 12 类口径）；勾选未维护 | — | 保留 | — | 预判一致 |
| 40 | llm_code_nodes_v2 | 1/24 | D1 | LLM 节点 v2 已交付（开关 llm_node_enabled settings.py:278 / code_node_enabled :279 默认 False 实测）；v2 评审对 LLM 节点无否定 | — | 保留 | — | 预判一致 |
| 41 | local_service_log_triage | 0/0 | D1 | 排查类历史档案（无勾选清单） | — | 保留 | — | 预判一致 |
| 42 | local_vector_database | 79/0 | D1 | 本地向量库已交付（integrations/local_vector_database 模块实测存在；79/79）；v2 评审 §6.4-5：pgvector 换伪向量=技术债清理口径、§9 RAG 不作产品方向 | 产品化冻结 | 保留不回滚 | 产品化解冻随 R1/R2 新证据（用户拍板） | 预判一致 |
| 43 | merge_tool_into_agent_node | 0/17 | D1 | 工具并入 Agent 节点已交付（agent_node_executor.py:65 tool_invoker 参数实测；勾选未维护） | — | 保留 | — | 预判一致 |
| 44 | minimal_runtime_env_evaluation | 0/0 | D1 | V/S/M/N 验证范式来源（spec FR-07 / tasks 0.3 引用） | — | 保留 | — | 预判一致 |
| 45 | ncp_independent_scroll | 8/0 | D1 | NCP 独立滚动已交付（B.2 UI 类；8/8） | — | 保留 | — | 预判一致 |
| 46 | node_io_variables_client_ops | 0/112 | D1 | 节点 IO 变量客户端操作已交付（B.2 引擎/节点类；勾选未维护） | — | 保留 | — | 预判一致 |
| 47 | node_name_edit | 20/0 | D1 | 节点名编辑已交付（20/20） | — | 保留 | — | 预判一致 |
| 48 | node_palette_cards | 18/2 | D1 | 节点面板卡片已交付（B.2 UI 类；18/20） | — | 保留 | — | 预判一致 |
| 49 | optimize_output_display_node | 101/0 | D1 | 输出节点优化已交付（101/101） | — | 保留 | — | 预判一致 |
| 50 | per_node_agent_template | 6/0 | D1 | 节点级 Agent 模板已交付（6/6） | — | 保留 | — | 预判一致 |
| 51 | phase1_engine | 0/75 | D1 | 引擎一阶段已交付（勾选未维护实证；phase3_5_deviation_fixes 头部声明「第一/二/三阶段已交付 T1-T79」；engine/scheduler.py:544-563 检查点写点实测存在）；T11 设计供 F1/F3/F4 引用 | merged-into arch_review_v2_tasks（F1 引用 T11） | 保留 | — | 预判一致 |
| 52 | phase2_engine_enhancement | 0/108 | D1 | 引擎增强已交付（勾选未维护；21 算子/触发器实测存在）；设计供 F13/F15 引用 | merged-into arch_review_v2_tasks（F13/F15 引用） | 保留 | — | 预判一致 |
| 53 | phase3_5_deviation_fixes | 0/0 | D1 | 阶段交付声明载体（头部声明 T1-T79 已交付；spec Pre-check B 引用） | — | 保留 | — | 预判一致 |
| 54 | phase3_production_governance | 0/211 | **D5 裁剪挂起 + D3 引用** | OP-04 拆解处置：RBAC/多租户/计量章节 → D3 供本轮 G3（复用 v1_2 表：schema_migrations/v1_2_phase3_governance.sql:57,72,84 实测）与 W11–13 裁剪引用；全局调度/资源池方向 → D5 挂起（不在 v2 路线）；实测 0/211 全未实施 | RBAC 部分 merged-into arch_review_v2_tasks；多租户/计量 reference-pending（W11–13） | 保留不回滚 | —（D5 部分如需归档由用户逐项确认，§8-D1） | 预判一致 |
| 55 | phase4_ecosystem_governance | 0/0 | D1 | 生态治理已交付（评审 §2.4：governance/phase4 336 例存在且通过；基线 2797 含之） | — | 保留 | — | 预判一致 |
| 56 | phase5_plan_alignment | 0/0 | D1 | 文档型目录（计划对齐历史档案） | — | 保留 | — | 预判一致 |
| 57 | plan_execution_audit | 15/1 | D1 | 分析类历史档案（B.2；15/16） | — | 保留 | — | 预判一致 |
| 58 | practical_workflow_test_cases | 0/46 | D1 | 实用工作流用例已交付（tests/ 集成用例实测存在；基线 2797 例含集成集）；勾选未维护 | — | 保留 | — | 预判一致 |
| 59 | project_dev_roadmap | 24/0 | D1 | L0 分析报告已产出（roadmap_report.md 918 行/MD5 D6B51471…冻结口径）；OP-05 对照参照：方向卡与 v2 筛子映射登记，报告不修订 | 对照参照 | 保留 | — | 预判一致 |
| 60 | project_ext_analysis | 0/0 | **D5 裁剪挂起** | OP-05：扩展分析报告已产出（ext_analysis_report.md 316 行实测）；与 v2 形成平行建议有漂移风险；如需续做须以 v2 筛子结果为输入重估范围 | pruned-suspended | 保留 | — | 预判一致 |
| 61 | project_todo_inventory | 41/0 | D1 | 分析类历史档案（41/41） | — | 保留 | — | 预判一致 |
| 62 | run_workflow_error_feedback | 64/0 | D1 | 运行错误反馈已交付（B.2 运行/触发类；64/64） | — | 保留 | — | 预判一致 |
| 63 | run_workflow_feature | 0/0 | D1 | 画布运行已交付（api/routers/execution_instance_router.py 实测存在；评审 §2.2） | — | 保留 | — | 预判一致 |
| 64 | run_workflow_from_canvas | 0/60 | D1 | 画布运行已交付（勾选未维护；评审 §2.2 执行链路可用） | — | 保留 | — | 预判一致 |
| 65 | theme_mode_dark_light | 0/107 | D1 | 深浅主题已交付（勾选未维护；spec 附录 A F10「前端主题令牌已具备」引用资产） | F10 OEM 引用（W11–13 登记） | 保留 | — | 预判一致 |
| 66 | theme_toggle_button | 31/1 | D1 | 主题切换按钮已交付（31/32） | — | 保留 | — | 预判一致 |
| 67 | trigger_input_template_resolution | 25/0 | D1 | 触发输入模板解析已交付（25/25）；TD-01 关联：已知限制声明三处，F14 衔接（W5–7 路线登记） | — | 保留 | — | 预判一致 |
| 68 | trigger_schedule_frequency_selector | 0/76 | D1 | 调度频率选择器已交付（web/src/canvas frequency/schedule 引用实测命中；spec 附录 A F14 前端选择器引用）；勾选未维护 | — | 保留 | — | 预判一致 |
| 69 | unified_node_input | 18/8 | D1 | 统一节点输入已交付（B.2 引擎/节点类；18/26） | — | 保留 | — | 预判一致 |
| 70 | unify_frontend_ui_style | 79/3 | D1 | UI 统一已交付（B.2 UI 类；79/82） | — | 保留 | — | 预判一致 |
| 71 | update_node_io_strategy | 0/68 | D1 | 节点 IO 策略更新已交付（B.2 引擎/节点类；勾选未维护） | — | 保留 | — | 预判一致 |
| 72 | upstream_input_ref | 16/7 | D1 | 上游输入引用已交付（16/23） | — | 保留 | — | 预判一致 |
| 73 | vector_kb_management | 225/54 | D1 | 向量库管理已交付（225/279；B.2 KB/向量类）；产品化冻结同 local_vector_database 口径（v2 §6.4-5/§9） | 产品化冻结 | 保留不回滚 | 同 local_vector_database（R1/R2 新证据用户拍板） | 预判一致 |
| 74 | vector_knowledge_base_node | 64/3 | D1 | 向量知识库节点已交付（64/67）；产品化冻结同上 | 产品化冻结 | 保留不回滚 | 同上 | 预判一致 |
| 75 | vkb_doc_annotation | 78/0 | D1 | VKB 文档标注已交付（78/78）；产品化冻结同上 | 产品化冻结 | 保留不回滚 | 同上 | 预判一致 |
| 76 | vkb_write_mode | 133/0 | D1 | VKB 写模式已交付（133/133）；产品化冻结同上 | 产品化冻结 | 保留不回滚 | 同上 | 预判一致 |
| 77 | workflow_run_logging | 17/0 | D1 | 运行日志已交付（17/17；run_log 记录器范式供 F1 引用） | merged-into arch_review_v2_tasks（F1 引用注入范式） | 保留 | — | 预判一致 |

## 2. 归档区注记（不计 77）

`archived/` 含 9 个子目录：add_delete_node_feature、add_node_config_hints、add_yaml_file_import_button、fix-canvas-drag-clear-and-edge-loop-bugs、fix_canvas_clear_and_example_recurrence、fix_example_library_wrap、fix_first_node_zoom_reset、fix_node_config_persistence、optimize_ncp_select_visual —— C2 历史归档（spec B.2 归档区行），维持原状。

## 3. 档位统计（77 进 77 出）

| 档位 | 数量 | 目录 |
| --- | --- | --- |
| D1 已交付保留 | 73 | 其余全部（含产品化冻结标记 5：local_vector_database/vector_kb_management/vector_knowledge_base_node/vkb_doc_annotation/vkb_write_mode；增量引用 3：phase1_engine/phase2_engine_enhancement/workflow_run_logging；对照参照 1：project_dev_roadmap） |
| D2 立即排期 | 1 | arch_review_v2_tasks（本轮自身） |
| D4 冻结 | 1 | agent_market_user_development（ZF-05） |
| D5 裁剪挂起 | 2 | phase3_production_governance（D5+D3 组合，主档 D5）、project_ext_analysis |

跨档改判：0（全部「预判一致」，无 §8-D2 登记）。

## 4. 与既有留痕机制的关系

- `docs/tech-debt-ledger.md`：B2 role/user/tenant 三族因本轮 G3 接线销项（任务 2.11，先台账后代码）；TD-01 不销项（F14 属 W5–7）；B 档 APM 排查计划不受影响（DC-02/§6.3）。
- 目录级 `DISPOSITION.md`：高影响 10 目录已生成（任务 1.3：phase1_engine、phase2_engine_enhancement、phase3_production_governance、agent_market_user_development、agent_tool_marketplace、project_ext_analysis、project_dev_roadmap、workflow_run_logging、trigger_input_template_resolution、run_workflow_from_canvas）。
- 冻结区登记：ZF-01~ZF-08 见 spec FR-05 / design 附录 B；本台账 D4 条目引用 ZF-05。

---

## 5. Wave2 处置节（arch_review_v2_tasks_wave2 追加，2026-11-18）

> **载体与纪律（C-1，CHG-69）**：本节为 Wave2 追加节；§1 77 条原文区（:11-91）与 §2~§4 零改动（INV-02 / Z-5 / GK-08 / GK-10）；本节每条含「处置动作 / 依据（文件:行号或台账行号）/ 销项状态」三要素（FR-07 规则 5）。追加前全文快照已固化：`arch_review_v2_tasks_wave2/snapshots/`（SHA256 前缀 8D07D451F70795E5，任务 1.5）。
>
> **启动门禁（DC-32）——豁免模式落定**：2026-11-18 用户显式选择「豁免模式（推荐）」：豁免 Wave1 剩余未竟项（P5 wfp CLI / P6 语义 diff+GitHub Action / P7 兼容承诺；P4 CI+容器化已于同日实测完成，Wave1 tasks 4.x 全勾）。豁免依据：Wave2a 四项与 P5~P7 无代码级依赖交集；F3/F4 所依赖的 resume_checkpoint 检查点机制实测在位（engine/scheduler.py:273/:285）。基线以启动复跑实测为准：**BASELINE-START = 2990 collected / 0 failed / 3 skipped / exit 0（2026-11-18，.pytest_cache nodeids 写入 16:48:54）**；§1 头部「基线佐证 2797」为 Wave1 P0 首轮实测历史记录，不覆盖、不修订（INV-02）。陈旧缓存注记：`.pytest_cache/v/cache/lastfailed` 含 9 条残留（16:11:16，含已改名旧测试 test_examples_cover_five_node_types），全通过运行不重写该文件，以 exit code 与 nodeids 为准（佐证归档 batch-records.md）。

### C-1 主台账 Wave2 节载体建立

- 处置动作：本节追加即载体建立（CHG-69）；77 进 77 出口径不变（77 条原文区零改动）。
- 依据：spec 附录 C C-1 / INV-02 / tasks 1.3 / 本文件快照（SHA256 前缀 8D07D451F70795E5）。
- 销项状态：**本条落盘即兑现**（2026-11-18）。

### C-2 phase3 审计引用兑现（Wave2a）

- 处置动作：Wave2a 审计落盘消费 phase3 spec §5.3 审计合规日志业务规则——按实测接线口径：v1_2 `audit_log_entries` 表已在位（persistence/postgres/schema_migrations/v1_2_phase3_governance.sql:177），本轮**零新建表、仅共用审计表**（audit 域与计量域分域，OP-05 / DC-24）；F7 五表（tenant_quotas :32 / token_usage_records :113 / pricing_rules :130 / cost_records :142 / budgets :155）不共用；取舍：phase3「全局审计面板 UI」不引用（OP-06 前端零改动）、「审计写入失败重试队列」不引用（降级记日志，Wave2b F7 评估）。
- 依据：台账 §1 #54 行（phase3_production_governance D5+D3，OP-04 拆解）/ phase3 spec §5.3 / design 附录 B.1；落地载体：`.codeartsdoer/specs/phase3_production_governance/DISPOSITION.md` Wave2 声明块（CHG-71，任务 1.4 追加）。
- 销项状态：§5.3 引用**本轮兑现**（P2 审计落盘接线落地）；§5.1/§5.2 计量/配额引用维持 reference-pending（Wave2b F6/F7）。

### C-3 trigger_input_template_resolution TD-01 衔接声明

- 处置动作：F14（Cron 定时触发）交付后，`trigger_input_template_resolution/DISPOSITION.md` 追加 TD-01 衔接声明（任务 5.7 落盘）；「定时触发下 input_template 无实际触发路径」已知限制随 F14 打通，三处声明（模型注释/前端提示/用户文档）同步更新的必要性届时评估（PB-3）。
- 依据：台账 §1 #67 行（trigger_input_template_resolution D1，TD-01 关联三处已知限制声明，F14 衔接 W5–7 路线登记）/ tech-debt-ledger.md:81-88（TD-01 四要素）/ PB-3。
- 销项状态：**待 P4 交付后落盘**（任务 5.7）。

### C-4 TD-01 销项预登记 + B1 族 audit_interceptor 处置预登记

- 处置动作：`docs/tech-debt-ledger.md` TD-01（§7 :81-88，「补齐调度能力另立需求」）销项**预登记**——声明「待 F14 交付后销项」，销项执行遵循「先台账后代码」纪律（任务 5.6）；B1 治理拦截器族之 `audit_interceptor`（ledger :29）按事件总线 P2 接线实测调用点决定销项或维持待接线——本轮事件总线为**新增订阅路径**（AuditEventSubscriber），不承诺接线 B1 既有拦截器；metering/budget/global_scheduler 拦截器维持待接线（Wave2b F7）。
- 依据：tech-debt-ledger.md:81-88（TD-01）/ :29（B1 族 5 文件）/ 台账 §4 第 1 条（「TD-01 不销项（F14 属 W5–7）」——本条为该表述的 Wave2 演进：F14 已进入 Wave2a 实施，销项窗口打开）/ FR-07 规则 3。
- 销项状态：TD-01 **待 P4 门禁通过后销项**（任务 5.6）；B1 audit_interceptor **待 P2 接线后实测结论**（任务 5.6）。
- 销项状态同步（2026-11-19，任务 5.6 落定）：TD-01 **已销项**——P4 门禁通过（BASELINE-P4 = 3147 collected / 0 failed / 3 skipped / exit 0，2026-11-19），销项登记见 `docs/tech-debt-ledger.md` §7 TD-01 销项栏（CHG-70）；B1 audit_interceptor **维持待接线，不销项**——全仓实测（任务 5.6）：`AuditInterceptor` 类零实例化点，唯一引用为 `InterceptorChain` 构造器 Optional 槽位（interceptors/base.py:235，默认 None），生产审计走 P2 事件总线订阅路径（AuditEventSubscriber，DC-25）与 B1 既有拦截器互为独立通道，确认「已设计未接线」，B 档待清理定位维持（APM 专项/Wave2b F7 复核），结论补记见 `docs/tech-debt-ledger.md` §3 B1 行。

### C-5 phase1_engine / phase2_engine_enhancement T11 引用兑现登记

- 处置动作：T11 检查点设计被 F3（HITL resume_checkpoint 唤醒）与 F4（re_execute 真跑）**实际消费**——引用兑现登记（Wave2a 实施期，P3/P5 落地）。
- 依据：台账 §1 #51 行（phase1_engine D1，T11 设计供 F1/F3/F4 引用；F1 已随 Wave1 P3 交付）/ #52 行（phase2_engine_enhancement D1，设计供 F13/F15 引用）/ PB-4 / E-W2-01 实测（scheduler.py:273-296）。
- 销项状态：引用兑现**随 P3/P5 完成落地**（P6 终验复核）。

### C-6 theme_mode_dark_light F10 OEM 引用维持登记

- 处置动作：F10 OEM 构建引用主题令牌资产**维持登记**（Wave2b；OP-06——前端波及届时单独裁决）；本轮 Wave2a 零前端改动（design Z-1 / OP-06）。
- 依据：台账 §1 #65 行（theme_mode_dark_light D1，F10 OEM 引用 W11–13 登记）/ PB-5。
- 销项状态：维持登记（Wave2b 兑现）。

### C-7 本轮 4 项立约 F 项 ↔ 实施目录引用关系

- 处置动作：Wave2a 立约 4 项（25 人日，红线 ≤30）与 `arch_review_v2_tasks_wave2` 目录（spec 591 / design 930 / tasks 400 行）引用关系登记：事件总线改造+审计落盘并入（8 人日）→ 台账 #54（phase3 §5.3 引用）+ #9（任务链自身）；F3 HITL（6 人日）→ #51（T11 引用）；F14 Cron（5 人日）→ #67（TD-01 关联）+ #68（trigger_schedule_frequency_selector 前端选择器产物被 resolve_next_fire 兼容消费，零改造）；F4 重放（6 人日）→ #51（T11 引用）。
- 依据：spec 附录 A.1 / tasks 范围声明 / design §9 批次表。
- 销项状态：**本轮活跃实施**（P2~P5 批次推进，P6 终验收口）。

### C-8 冻结区延续声明

- 处置动作：冻结区 ZF-01~08（表单输入 / NL Copilot / 评估闭环 / 推荐面板 / 市场 / 模型面板与 Prompt IDE / SSO / A2A）与第一轮 spec FR-05 清单**逐项一致**——零新增、零解冻；Wave2a tasks 零冻结实施条目（G-① 扫描门禁，任务 6.2）。
- 依据：INV-01 / 台账 §1 #6 行（agent_market_user_development D4/ZF-05）/ 台账 §4 末条 / design Z-9。
- 销项状态：延续有效（P6 终扫留痕）。

### C-9 Wave2b 全部 F 项登记态

- 处置动作：W8–13（81 人日口径）全部登记为**「Wave2b 登记态」**——零实施任务、零技术选型决定（INV-06）；成本重估口径同步：F7 按 v1_2 五表已建实测重估为「接线既有表 + B1 metering/budget 拦截器记账，非新建三表」（OP-05）；F17 按 OP-04 确认 12 人日口径不含热加载框架（静态注册）；Wave2b 启动前须重新走 spec→design。
- 依据：spec FR-06 / §4.3 / design 附录 C / tasks G-② 扫描（任务 6.2）。
- 销项状态：登记维持（Wave2b 启动前冻结）。

---

## 6. Wave3 处置节（arch_review_gap_* 批次追加，2026-11-19 起）

> **载体与纪律**：本节为 Wave3 批次（architecture-review-2026-11 评审销项四批次：B1 加固 → B4 架构 → B2 可观测性能 → B3 生态，批次序 OP-02）追加节；§1 77 条原文区与 §2~§5 零改动；本节每段含「处置动作 / 依据 / 销项状态」三要素；批次间互不覆盖，逐批追加段。

### B1 段：arch_review_gap_b1_hardening 收口（2026-11-19）

- 处置动作：评审 §6.3 代码质量四件（L2 登记转立约）全部销项收口——**G1 NCP 拆分（B1-C1）**：`web/src/canvas/NodeConfigurationPanel.tsx` 3710→411 行（调度壳），`panels/` 树 16 文件（12 表单目录 + shared 4 件），props 契约四重验证全过；**G2 httpClient 抽取（B1-C2）**：`web/src/api/httpClient.ts` 基元 + 15 客户端四批迁移收口 + SSE 特例（★P-04=A：仅复用 token 注入，留痕对照表 SSE 行）；**G3 ApiError 统一（B1-C3）**：`web/src/api/apiError.ts` 五层分层 + 3 旧异常类薄包装 + 10 catch 点切换；**G4 文档对齐（B1-C4）**：README 能力地图重写 + `docs/config.md`/`docs/api-reference.md` 新增 + `docs/phase4_ecosystem_summary.md` 漂移校正 + `docs/deploy.md` 零 diff 核销（136 行在位）。
- 依据：`.codeartsdoer/specs/arch_review_gap_b1_hardening/` 三件套（spec 附录 A 卡 B1-C1~C4；design §4.3 对照表 15 行 + SSE 特例行；tasks 出口记录区全量留痕）；评审 §6.3 代码质量四件条目。
- 销项状态：**G1~G4 四条全部收口**（出口门禁实测：pytest 3147 执行 exit 0（≥2953）/ 前端 vitest 117 文件 962 passed + 13 skipped 零失败 / 范围禁区扫描零命中 / 批次 ≈13.5 人日 ≤30 且逐条 ≤ 评审 ±20%）；G 条目为 L2 登记转立约，收口后不再挂账。

### B4 段：arch_review_gap_b4_architecture 收口（2026-11-19）

- 处置动作：评审 A.3 架构缺口（L2 登记转立约）批次收口——**G15 DI 容器（B4-C2）**：`api/di/container.py` 手工轻量容器（register/resolve/freeze/has/keys，惰性构造+单例记忆化；零 DI 框架）+ `api/di/__init__.py` + `application_factory.py` 653→731 行装配段迁移（provider P1~P9 + freeze + `app.state.container`；`create_app()` 签名与对外行为零变化）；**G16 LangGraph 第二运行时（B4-C1）**：`engine/adapter/langgraph_adapter.py` 验证期桩（279 行）真实化 579 行（节点语义单源——经 `executor_registry` 与内置引擎同一注册表实例；三类拓扑映射 M1~M4；决策规程与 scheduler `_eval_branch`/`_eval_loop` 同源复刻；`_validate_subset` 子集安全网；HC-03 唯一 `import langgraph` 位置零放宽）+ `langgraph_state_mapping.py` 瞬态键 `next_node_id` 对齐（78 行）+ settings 开关 `langgraph_runtime_enabled`（默认 False，三态降级链，生产 `start_instance` 零改）+ `.env.example` 登记；等价矩阵 `tests/integration/test_langgraph_equivalence_matrix.py` 587 行 13 例（8 IN 参数化 × E1~E5 + E4 错误码专项 + 4 N/A 静态/硬边界标注）+ 等价边界文档 `docs/langgraph-equivalence-matrix.md` 落盘（README 文档索引 7→8 件、`docs/config.md` 216→217 字段同步）。
- 依据：`.codeartsdoer/specs/arch_review_gap_b4_architecture/` 三件套（spec AC-B4-01~05；design DC-B4-01~03 + CHG-B4-01~05；tasks 出口记录区全量留痕）；评审 §6.2-2 L268（LangGraph 由「删」改「留」——第二运行时子集等价）与 §10 L361 硬证据③；母 design §4.2 卡 B4-C1/C2。
- 销项状态：**G15/G16 两条收口**（出口门禁实测：全量 pytest 3190 collected（3187 passed + 3 skipped）exit 0 零失败（≥ B1 出口锚 3147）/ 矩阵 13/13 + 守护复跑 72 passed（15 YAML 回归含于 `test_example_workflows_runnable.py` 37 例、隔离守护与文本守护零放宽、开关三态复核）/ 前端 vitest 抽样 7/7 passed（B4 纯后端批次前端零触碰）/ 零改动登记核对全过（scheduler.py SHA 一致 + engine abstractions 4 文件/nodes 15 文件/node_executor_registry/execution_instance_router 1124 行/test_langgraph_isolation 101 行批次窗口零写入 + pyproject/enums.py SHA 一致）/ 批次 11 人日 ≤30 且逐卡 ×1.0 ±20% 内）；硬证据③「同一份 YAML 双运行时等价（子集等价，边界文档化）」内部验证完成——外部采用属用户环境目标（边界声明见 `docs/langgraph-equivalence-matrix.md` §3）。

### B2 段：arch_review_gap_b2_observability_perf 收口（2026-11-20）

- 处置动作：评审 B2 可观测+性能组批次收口（C1~C5 实施 / C6~C7 ★P-02=B 延后）——**C1 前端路由（B2-C1）**：`web/src/router/` 四件（hashRouter 纯函数 + useHashRoute + RouteLink + index），合法 id 集合运行时读 MENU_ITEMS，App/AdminConsole 接线，react-router 零引入；**C2 懒加载（B2-C2）**：自定义惰性槽 panelLazy（LazySlot 状态机，React.lazy 首渲异步不满足既有同步断言金标准的实现偏差登记），18 面板分包（vite build 实证 18 chunk 合计约 192 kB 移出主包），MENU_ITEMS 18 项 id 零变化；**C3 防抖（B2-C3）**：persistenceSaver（300ms trailing + 三处 flush + 白名单 `{nodes, edges}` 类型层保证）；**C4 LLM 扩展（B2-C4）**：http_llm_client 连接池实例级单例（既有单测 mock 隔离的实现偏差登记）+ 指数退避重试（`llm_retry_enabled` 默认 False，关闭态逐字节等价断言）+ `infer_stream` 流式（协议契约零改动 + 错误帧脱敏延伸）；**C5 向量诚实化（B2-C5 / G8）**：settings 新开关 `vector_real_embedding_enabled=False`（关闭态与现状等价）+ `local_vector_database_storage=sqlite`（pg_array 可选路径），`vector_real_embedding_enabled=True` 时 hash_fallback 新写入以可辨识错误拒绝（`VECTOR_KB_REAL_EMBEDDING_REQUIRED`），记录级 `embedding_kind`（real|hash）+ 存量缺省 hash 代际识别 + 检索两代真实计分 + hash 代际 degraded_annotation 出口守护，`PgArrayVectorStore`（`vector_kb_records`：embedding `DOUBLE PRECISION[]` + embedding_kind，persistence/postgres 驱动复用零新增依赖 + 迁移文件 v1_4）+ `PgVectorExtensionProbe` 探测器（`pg_extension` 三态，未命中可辨识降级回退应用层距离）。
- 依据：`.codeartsdoer/specs/arch_review_gap_b2_observability_perf/` 三件套（spec FR-B2-01~07 + AC-B2-01~08；design DC-B2-01~05；tasks 出口记录区全量留痕）；母 tasks 4.3~4.10；★P-02 拍板 **B——登记延后不实施（2026-11-19，用户裁定）**：T6 OTel / T7 Prometheus 保持未勾并登记延后（R1/R2 落地后凭进展证据另行放行），★P-06 人日监控承接（批次贴线 ≤30）。
- 销项状态：**C1~C5 收口 + C6/C7 延后登记**（出口门禁实测：全量 pytest **3250 collected（3247 passed + 3 skipped）exit 0 零失败**（≥ B4 出口锚 3190）/ 前端 vitest **121 files / 998 passed + 13 skipped** exit 0（与 T3 后基线持平零回退）/ `tsc --noEmit` **13 项既有基线零新增** / 关闭态等价与代际守护单测在位 / 前缀字节零 diff 校验过（GK-03，追加前 29726 字节 SHA `e3cd80af…` 前缀比对；台账终值 199 行））。**G8 核销登记**：Wave1 `arch_review_v2_tasks/spec.md` FR-06 §5.6（:345 路线登记；:357 另登记行「pgvector 换伪向量（技术债清理口径）」；:532 台账行「local_vector_database | D1 存量保留 + 产品化冻结」）+ Wave2b `arch_review_v2_tasks_wave2/spec.md` §4（:252 另登记行）——原登记文本零改动（GK-04），核销口径 = **「已实施 → `arch_review_gap_b2_observability_perf/`」**（C5 向量诚实化落地：伪向量代际可识别、真实 embedding 强制开关、pg_array 存储与探测回退路径在位；产品化冻结口径不变，RAG 仍不作产品方向）。
