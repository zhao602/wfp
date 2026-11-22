# 范例总览（examples）

本目录收录平台全部预置工作流范例：**10 个基础编排范例 + 2 个数据科学范例，共 12 个**。
文档条目数 = 实际范例目录数 = 12（G7/T237 全量盘点登记）。

## 一、总览表

| # | 范例 | 目录 | 编排模式 | 覆盖要点 |
| --- | --- | --- | --- | --- |
| 1 | 顺序流水线 | `sequential_pipeline/` | trigger → data_source → agent（经工具选项产出报告） | 最小编排链路入门 |
| 2 | 并行执行 | `parallel_execution/` | trigger → agent×2 → parallel_join → output_display | 并行分支与汇聚 |
| 3 | 监督者路由 | `supervisor_routing/` | trigger → agent → conditional_branch → agent×2 → output_display | 监督者决策与条件路由 |
| 4 | 层级委派 | `hierarchical_delegation/` | trigger → agent → subworkflow×2 → agent（含子定义 team_lead_subworkflow.yaml） | 层级委派与子工作流嵌套 |
| 5 | 反思迭代 | `reflection_iteration/` | trigger → agent×2 → conditional_branch + loop → output_display | 反思回路与循环收敛 |
| 6 | 数据工程 Agent | `data_engineering/` | trigger → data_source → data_transform → agent → output_display | 大数据连接器 + 数据转换 + Agent 分析 |
| 7 | 数据转换流水线 | `data_transform_pipeline/` | trigger → data_source → data_transform×2 → output_display | 纯数据转换（无 Agent） |
| 8 | 条件循环流水线 | `conditional_loop_pipeline/` | trigger → agent → loop → agent → conditional_branch → output_display | 循环 + 条件分支组合 |
| 9 | 子工作流嵌套 | `subworkflow_nesting/` | trigger → subworkflow → output_display | 最小子工作流复用 |
| 10 | 实时数据订阅 | `realtime_subscription/` | trigger → agent → conditional_branch → agent×2 → output_display | 事件驱动触发与订阅 |
| 11 | 特征工程流水线 | `feature_engineering/` | trigger → data_source → data_transform×2 → agent → output_display | 机器学习特征构造（G4 新增） |
| 12 | 数据漂移检测 | `drift_detection/` | trigger → data_source×2 → conditional_branch → agent×2 → output_display | 漂移判定 + 基线缺失不误报（G4 新增） |

> 节点口径（merge_tool_into_agent_node DC-E / CHG-23）：全部范例已无独立
> tool 节点——工具能力经 Agent 节点 `available_tools` 选项演示，结论输出由
> 输出展示节点（output_display）承接。

## 二、逐范例说明

### 1. sequential_pipeline（顺序流水线）

- **定义文件**：`sequential_pipeline/sequential_pipeline.yaml`
- **演示模式**：顺序节点的最小编排链路（trigger → data_source → agent → tool）
- **依赖变量**：`LLM_PRIMARY_BASE_URL` / `LLM_PRIMARY_API_KEY` / `LLM_PRIMARY_MODEL` / `DATA_SOURCE_CONNECTION_STRING`
- **运行方式**：已在范例注册表登记，`ExampleLibraryService().load_example("sequential_pipeline")`
- **核心验证点**：引擎全链路加载与顺序执行

### 2. parallel_execution（并行执行）

- **定义文件**：`parallel_execution/parallel_execution.yaml`
- **演示模式**：`control_flow(parallel_join)` 并行分支与汇聚；两分析分支以顺序边串联保证都执行（调度器按声明顺序取首条出边），parallel_join 以两条入边承接汇聚
- **依赖变量**：LLM 主模型三变量
- **运行方式**：已在范例注册表登记，`ExampleLibraryService().load_example("parallel_execution")`

### 3. supervisor_routing（监督者路由）

- **定义文件**：`supervisor_routing/supervisor_routing.yaml`
- **演示模式**：监督者 Agent 输出路由决策，`conditional_branch` 按决策分发至财务/运营分析 Agent
- **依赖变量**：LLM 主模型三变量
- **运行方式**：`ExampleLibraryService().load_example("supervisor_routing")`
- **核心验证点**：条件分支唯一路径选择与默认分支策略

### 4. hierarchical_delegation（层级委派）

- **定义文件**：`hierarchical_delegation/hierarchical_delegation.yaml` + `hierarchical_delegation/team_lead_subworkflow.yaml`
- **演示模式**：总监 Agent 分解任务 → 前端/后端两个 subworkflow 节点顺序串联委派组长（子定义 agent×4）→ 总监汇总；两次委派以顺序边串联保证都执行
- **依赖变量**：LLM 主模型三变量
- **运行方式**：已在范例注册表登记，`ExampleLibraryService().load_example("hierarchical_delegation")`（子定义 team_lead_subworkflow.yaml 运行期经定义库解析）

### 5. reflection_iteration（反思迭代）

- **定义文件**：`reflection_iteration/reflection_iteration.yaml`
- **演示模式**：生成 Agent → 评审 Agent → 质量分支 → 循环节点迭代至收敛或达最大次数；终止条件满足时经非回边出边进入输出展示
- **依赖变量**：LLM 主模型三变量
- **运行方式**：已在范例注册表登记，`ExampleLibraryService().load_example("reflection_iteration")`

### 6. data_engineering（数据工程 Agent）

- **定义文件**：`data_engineering/data_engineering_agent.yaml`
- **演示模式**：Hologres 读取 → 清洗聚合操作链（handle_nulls + aggregate）→ Agent 分析 → 输出展示，完整数据工程链
- **依赖变量**：LLM 主模型三变量 + `DATA_SOURCE_CONNECTION_STRING`（查询取数与 sequential_pipeline 同款口径）
- **运行方式**：已在范例注册表登记，`ExampleLibraryService().load_example("data_engineering")`

### 7. data_transform_pipeline（数据转换流水线）

- **定义文件**：`data_transform_pipeline/data_transform_pipeline.yaml`
- **演示模式**：纯数据转换链（无 Agent 节点）：trigger → data_source → data_transform×2（handle_nulls 清洗 + aggregate 聚合）→ output_display
- **依赖变量**：`DATA_SOURCE_CONNECTION_STRING`（无 LLM 依赖——不含 Agent 节点）
- **运行方式**：已在范例注册表登记，`ExampleLibraryService().load_example("data_transform_pipeline")`

### 8. conditional_loop_pipeline（条件循环流水线）

- **定义文件**：`conditional_loop_pipeline/conditional_loop_pipeline.yaml`
- **演示模式**：`loop` 循环 + `conditional_branch` 条件分支组合使用
- **依赖变量**：LLM 主模型三变量
- **运行方式**：`ExampleLibraryService().load_example("conditional_loop_pipeline")`
- **核心验证点**：循环终止条件退出与 `max_iterations` 上限

### 9. subworkflow_nesting（子工作流嵌套）

- **定义文件**：`subworkflow_nesting/subworkflow_nesting.yaml`
- **演示模式**：最小子工作流复用结构（trigger → subworkflow → output_display），ISOLATE 上下文策略
- **依赖变量**：LLM 主模型三变量 + `DATA_SOURCE_CONNECTION_STRING`（传递依赖：被嵌套的 sequential_pipeline 需要）
- **运行方式**：已在范例注册表登记，`ExampleLibraryService().load_example("subworkflow_nesting")`（sequential_pipeline 须已登记到平台定义库，运行期按 subworkflow_definition_id 取最新版本）

### 10. realtime_subscription（实时数据订阅）

- **定义文件**：`realtime_subscription/realtime_subscription_workflow.yaml`
- **演示模式**：事件驱动编排——data_event 触发器订阅 Kafka 数据流事件，事件分类 Agent + 高/常规优先级条件分支 + 输出展示
- **依赖变量**：LLM 主模型三变量；运行时事件经订阅管理器投递（Kafka 不可用时内存降级通道同语义可用）
- **运行方式**：已在范例注册表登记，`ExampleLibraryService().load_example("realtime_subscription")`

### 11. feature_engineering（特征工程流水线，G4 新增）

- **定义文件**：`feature_engineering/feature_engineering.yaml` + `feature_engineering/feature_engineering_agents.yaml`
- **演示模式**：trigger → data_source → data_transform×2（handle_nulls 清洗 + sort 整序）→ 特征工程 Agent → 输出展示，机器学习特征构造链
- **依赖变量**：LLM 主模型三变量
- **运行方式**：`ExampleLibraryService().load_example("feature_engineering")`
- **异常语义**：上游数据不可得时 data_source 节点按节点失败语义终止并给出明确缺数原因

### 12. drift_detection（数据漂移检测，G4 新增）

- **定义文件**：`drift_detection/drift_detection.yaml` + `drift_detection/drift_detection_agents.yaml` + `drift_detection/drift_alert_subscription.example.json`
- **演示模式**：trigger → data_source → conditional_branch（基线存在性，默认分支承接"基线未建立"）→ 漂移检测 Agent / 基线未建立结论 Agent → 输出展示
- **依赖变量**：LLM 主模型三变量
- **运行方式**：`ExampleLibraryService().load_example("drift_detection")`
- **异常语义**：基线缺失走"基线未建立"分支产出 `baseline_established: false` 结论（不误报）；漂移事件经既有事件通道发布（内存降级通道照常发布），告警侧经 G6 阈值模板订阅 `drift_score` 联动（见范例 README）

## 三、加载方式说明

范例库服务（`ExampleLibraryService`）登记全部 12 个范例（T16.5 全量登记，
`_EXAMPLE_REGISTRY` 以追加方式登记、不修改既有条目）。依赖变量缺失时范例
标记为不可用并列出缺失项（EXAMPLE_DEPENDENCY_MISSING 语义），不产生半成品加载。

依赖声明口径：
- 含 Agent 节点的范例 → LLM 主模型三变量；
- 数据源以查询语句取数（demo_warehouse / hologres_connection 模式）
  → 追加 `DATA_SOURCE_CONNECTION_STRING`；
- 子工作流嵌套按传递依赖声明（subworkflow_nesting 调用 sequential_pipeline）。

除范例库服务外，任意范例也可读取 YAML 文本后调用
`orchestration_platform.workflow.yaml_workflow_parser.YamlWorkflowParser().parse(yaml_text)`
解析加载。

## 四、新增范例登记指引

向本目录新增范例时，请同步完成以下步骤（保持文档条目数 = 实际范例数）：

1. 在 `examples/<example_id>/` 下创建编排定义 YAML（spec_version/definition_id/inputs/outputs/nodes/edges 完整）；
2. 含预置 Agent 的范例追加 `<example_id>_agents.yaml` 与 `README.md`（名称/职责/输入输出契约完整）；
3. 需要范例库服务加载的，在
   `src/orchestration_platform/application/example_library_service.py` 的
   `_EXAMPLE_REGISTRY` 中以 dict 追加方式登记（目录名 → (YAML 文件名, 所需环境变量)），
   不修改既有条目；
4. 在本 README 总览表与逐范例说明中同步登记；
5. 在 `tests/verify_g4_data_science_examples.py` 或新测试中补充解析合规断言。
