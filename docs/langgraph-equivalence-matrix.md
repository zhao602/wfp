# LangGraph 等价矩阵与边界（双运行时子集等价）

> 交付物：`arch_review_gap_b4_architecture`（B4-C1，硬证据③）——「同一份 YAML 在内置引擎与 LangGraph 两种运行时跑出等价结果」的可复核证明。
> 断言载体：`tests/integration/test_langgraph_equivalence_matrix.py`（13 例 = 8 IN 参数化 + 1 E4 专项 + 4 N/A）。
> 开关：`langgraph_runtime_enabled`（默认 `False`，HC-07/GK-11）；生产 `start_instance` 链路零改（INV-B4-05）。

## 0. 等价边界

### 0.1 等价子集（三类拓扑）

| 拓扑 | adapter → StateGraph 映射 | 语义母本（scheduler.py 零改动） |
| --- | --- | --- |
| 顺序 | 声明后继 `add_edge` | `_determine_next_node`（首声明出边） |
| 条件分支（单层） | `add_conditional_edges`（router 读瞬态键 `next_node_id`，不出 adapter） | `_eval_branch`（声明序首个真 → 默认回退 → 无匹配 `WF_VALIDATION_FAILED`） |
| 单层循环 | `add_conditional_edges` over {循环体入口, 非回边出口} | `_eval_loop`（终止条件 → 非回边出口 → `counter ≥ max_iterations` 抛 `WF_LOOP_NOT_CONVERGED` → 回边计数 +1） |
| 普通多出边（非控制流） | 取首声明出边（子集内范例实测无此形态，出现即按首边执行，矩阵守卫） | scheduler 首声明边口径（B6 注记 2026-11-22：严格扇出形状——分叉源出边目标集 == `parallel_branches` 声明——在内置引擎激活并行区域，属排除①不进子集；子集内首边口径不变） |

**节点语义单源（INV-B4-01）**：adapter 节点函数经 `executor_registry.get_executor(node_type)` 调用与内置引擎**同一注册表实例**（12 类节点执行器）；`NodeExecutionContext` 构造口径（timeout / round_index / node_definitions 索引）与 `Scheduler._execute_node` 一致；失败映射同码回退链（`result.error_code or WF_NODE_EXECUTION_FAILED`）；`RestrictedExpressionEvaluator` 自 `engine.scheduler` 类级复用 import（非复制求值器）。

### 0.2 子集外排除清单（五项，硬边界）

| # | 排除项 | 说明 |
| --- | --- | --- |
| ① | 并行扇出/汇聚 | `CONTROL_FLOW kind=parallel_join` 等并行拓扑不映射；B6 注记（2026-11-22，roadmap_b6_parallel_fanout）：并行扇出已在内置引擎落地（编译期激活 + 双路径调度 + wait_all/wait_any/wait_n_of_m 等待计数 + `branch_id` 检查点 + 前沿恢复），本排除指 LangGraph 第二运行时不映射并行区域，硬边界不变 |
| ② | HITL 挂起 | 人工审核挂起/恢复链路不经第二运行时 |
| ③ | 回滚重放 | rollback/replay 检查点恢复链路不经第二运行时 |
| ④ | 多租户运行时 | 租户级运行时隔离不在等价断言范围 |
| ⑤ | 子工作流嵌套（含 SUPERVISOR 监督者子编排） | `SUBWORKFLOW` 节点与 `SUPERVISOR` 动态派生子编排（DC-B4-02 裁决 3 归界：supervisor 触发 sub_orchestrator 动态派生，与子工作流嵌套同类语义） |

**运行期防线**：`LangGraphAdapter._validate_subset` 在图构建前执行子集校验——命中排除项即抛 `WF_VALIDATION_FAILED`（不静默降级、不伪造等价）。子集外声明的对外文档见 [docs/dual-runtime-boundaries.md](dual-runtime-boundaries.md)（B8e，A5）。

## 1. 矩阵主表（12 范例 × E1~E5）

**双引擎口径**（两列共用）：

- 内置引擎：`YamlWorkflowParser → GraphCompiler → Scheduler`（生产语义母本）
- LangGraph：同 Parser/Compiler → `LangGraphAdapter`（StateGraph 编译，§0.1 映射；全仓唯一 `import langgraph` 位置，HC-03/GK-13）
- 双引擎**共享同一执行器注册表实例**：真实执行器（trigger / control_flow / output_display / data_transform——纯内存确定性）+ 确定性桩（agent / data_source——按 node_id 固定输出）

<!-- 生成区块起：scripts/generate_dual_runtime_matrix.py 生成（源 tests/integration/test_langgraph_equivalence_matrix.py），勿手改；重跑幂等 -->
| # | 范例 | 拓扑（子集内） | 执行步数 | E1 | E2 | E3 | E4 | E5 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `sequential_pipeline` | 顺序 | 3 | ✓ | ✓ | ✓ | ✓① | ✓ | 成功收敛 |
| 2 | `conditional_loop_pipeline` | 条件 + 单层循环 | 7 | ✓ | ✓ | ✓ | ✓（专项注入） | ✓ | M2 默认回退 + M3 回边计数 + 终止退出 |
| 3 | `reflection_iteration` | 条件 + 单层循环 | 25 | ✓ | ✓ | ✓（失败态：错误码对比） | ✓① | ✓ | 失败态等价——双引擎同抛 `WF_LOOP_NOT_CONVERGED`（§3） |
| 4 | `drift_detection` | 条件分支 | 5 | ✓ | ✓ | ✓ | ✓① | ✓ | 受限求值器不支持 `is not` → 条件求值抛错落默认分支（双引擎同径） |
| 5 | `realtime_subscription_workflow` | 条件分支 | 5 | ✓ | ✓ | ✓ | ✓① | ✓ | 事件分类路由（高优先级路径） |
| 6 | `feature_engineering` | 顺序 + 数据变换 | 6 | ✓ | ✓ | ✓ | ✓① | ✓ | data_transform 操作链（handle_nulls 确定性剔除） |
| 7 | `data_engineering_agent` | 顺序 + 数据变换 | 5 | ✓ | ✓ | ✓ | ✓① | ✓ | 同上 |
| 8 | `data_transform_pipeline` | 顺序 + 数据变换 | 5 | ✓ | ✓ | ✓ | ✓① | ✓ | 同上 |
| 9 | `parallel_execution` | — | — | N/A | N/A | N/A | N/A | N/A | 排除①：并行扇出/汇聚（CONTROL_FLOW kind=parallel_join）；硬边界：`_validate_subset` 运行期拒绝 `WF_VALIDATION_FAILED`；B6 注记（2026-11-22）：该范例为串行化兼容形态（非严格扇出，编译警告 + 单游标语义不变），严格扇出激活形态见 `examples/parallel_execution/parallel_fanout.yaml`（编译 14 例 + 执行 9 例覆盖），LangGraph 侧 N/A 结论不变 |
| 10 | `subworkflow_nesting` | — | — | N/A | N/A | N/A | N/A | N/A | 排除⑤：子工作流嵌套（SUBWORKFLOW 节点）；硬边界：`_validate_subset` 运行期拒绝 `WF_VALIDATION_FAILED` |
| 11 | `hierarchical_delegation` | — | — | N/A | N/A | N/A | N/A | N/A | 排除⑤：子工作流嵌套（SUBWORKFLOW 节点 ×2）；硬边界：`_validate_subset` 运行期拒绝 `WF_VALIDATION_FAILED` |
| 12 | `supervisor_routing` | — | — | N/A | N/A | N/A | N/A | N/A | 排除⑤：监督者动态路由模式（DC-B4-02 裁决 3 归界；拓扑为分支，语义为 agent 驱动监督者路由）；静态标注，不经矩阵执行 |

① 错误传播链为节点类型无关的同一映射（executor 失败 → `NodeExecutionError` 同码回退链），专项注入测试于 `conditional_loop_pipeline` 执行（`success=False` + `MATRIX_E4_INJECTED`）。
<!-- 生成区块止 -->

## 2. 断言口径说明（E1~E5 定义与白名单）

- **E1 结构等价**：`compiled_builtin == compiled_langgraph`（`CompiledWorkflow` pydantic 字段级相等：节点集 / 边集 / 条件边元数据 / 入口 / 终点集 / 节点序）。
- **E2 执行等价**：触发序列（`_RecordingExecutor.node_sequence`）+ 逐节点输出快照（每节点执行前的 `SharedState.node_outputs` 视图）双侧逐项对齐；另设**第三参照源**——8 套 IN 范例各自预登记的确定性预期序列（独立于两实现推导，同时锚定双引擎）。
- **E3 终态等价**：成功态——终态 `node_outputs` 逐字段对比；失败态——退化为**错误码对比**（`expected_error_code` 承载，等价性以双引擎同码失败证明）。
- **E4 错误等价**：注入节点失败（`success=False` + 注入错误码）→ 内置 `Scheduler` 抛 `NodeExecutionError.code` vs adapter `instance.error["code"]` 一致。
- **E5 幂等等价**：同输入重复执行输出一致；非确定性字段白名单：时间戳 / `trace_id` / `instance_id`。
- **依赖不可用**：`requires_langgraph` skipif 守卫——langgraph 不可用时全矩阵 skip 并登记（不伪造等价结论）；N/A 断言不设 langgraph 门（静态/运行期子集校验独立于依赖在位与否）。

## 3. 结论与边界声明

- **内部验证完成**：矩阵 13/13 通过（B7 时点实测 langgraph 1.0.10；B8e 复核 langgraph 1.2.12 亦 13/13，2026-11-23；兼容区间 `langgraph>=0.2`，不使用 0.2 外高级 API——checkpointer / 子图零触碰）；随全量回归零失败。
- **失败态等价口径**：等价性含「同码失败」——`reflection_iteration` 的循环终止表达式引用 `critique_result.pass`，`pass` 为 Python 关键字，受限求值器下分支求值必抛、终止条件永不可满足，双引擎均走满 `max_iterations=5` 后同抛 `WF_LOOP_NOT_CONVERGED`（受限求值器下的真实确定性行为，非测试装置产物）。
- **外部采用属用户环境目标**：本矩阵为内部验证结论；在用户环境（不同 langgraph 版本、拓扑组合、外部依赖）下的采用属用户目标，不在本证明承诺范围内。
- **生产链路**：`start_instance` 零改；开关三态（`False` 零变化 / `True` + 依赖在位可构造第二运行时 / `True` + 依赖缺失降级内置引擎并告警，INV-B4-03）。
- **子集外声明**：resume/rollback/replay/submit_review 的方法级行为与运行时路由见 [docs/dual-runtime-boundaries.md](dual-runtime-boundaries.md)（B8e，A5）。
