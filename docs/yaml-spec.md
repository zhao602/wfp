# 工作流定义 YAML 规范说明

## 五要素结构

工作流定义 YAML 包含五个必备要素：

```yaml
spec_version: "1.0"          # 1. 规范版本号
definition_id: my_workflow    # 2. 定义标识
name: 我的工作流              # 3. 名称
definition_version: 1         # 4. 版本号

inputs: []                    # 5a. 输入参数声明
outputs: []                   # 5b. 输出参数声明
nodes: []                     # 5c. 节点定义
edges: []                     # 5d. 连线定义
```

## 1. 规范版本号（spec_version）

当前支持版本：`"1.0"`、`"1.1"`（详见「版本与兼容性承诺」章）。不支持降级解析。

## 2. 定义标识（definition_id）

全局唯一的字符串标识，用于 API 引用与持久化索引。

## 3. 名称（name）

人类可读的中文名称，用于画布展示。

## 4. 版本号（definition_version）

正整数，同一 definition_id 可有多个版本。

## 5. 输入输出声明

```yaml
inputs:
  - name: topic          # 参数名
    type: string         # 类型
    required: true       # 是否必填
    description: 分析主题  # 说明

outputs:
  - name: report
    type: object
    description: 分析报告
```

## 节点定义

五类节点以 `node_type` 区分：

### 触发器（trigger）

```yaml
- node_id: trigger_start
  name: 手动触发
  node_type: trigger
  configuration:
    trigger_type: manual        # manual | scheduled | data_event
    # input_template：把触发上下文收敛/重命名/裁剪为触发器节点的显式命名输出契约，供下游节点引用；
    # 恒等映射且下游直接引用启动入参时可省略（定时触发暂无运行时入口，模板不生效）
    input_template:
      topic: topic
```

### Agent（agent）

```yaml
- node_id: analyze
  name: 数据分析
  node_type: agent
  output_name: analysis
  configuration:
    model_identifier: primary-model
    system_prompt: 你是一名数据分析师。
    available_tools: []
```

> **model_identifier 留空语义（2026-11-20 修订）**：agent / llm 节点的
> `model_identifier` 支持空字符串 = 运行时跟随全局主模型（`.env` 的
> `LLM_PRIMARY_MODEL`）；加载期校验对空值降为 WARNING 提示，不算校验
> 失败。`.env` 更换模型后无需逐节点同步修改已落盘工作流。

### Tool（tool）

```yaml
- node_id: generate_report
  name: 生成报告
  node_type: tool
  output_name: report
  configuration:
    tool_identifier: report_builder
    tool_source: local          # local | mcp
```

### 数据源（data_source）

```yaml
- node_id: read_data
  name: 读取数据
  node_type: data_source
  output_name: dataset
  configuration:
    connection_reference: demo_warehouse
    query: "SELECT id, value FROM demo_table"  # 只读查询
```

### 控制流（control_flow）

条件分支：

```yaml
- node_id: branch
  name: 路由分支
  node_type: control_flow
  configuration:
    control_flow_kind: conditional_branch
    conditions:
      - target_node_id: path_a
        expression: "result.score >= 80"
        label: 高分路径
    default_branch_target: path_a
```

循环：

```yaml
- node_id: loop
  name: 评审循环
  node_type: control_flow
  configuration:
    control_flow_kind: loop
    loop_body_entry: review_node
    loop_termination_condition: "score >= 90"
    max_iterations: 5
```

## 连线定义

```yaml
edges:
  - edge_id: e1
    source_node_id: trigger_start
    target_node_id: analyze
```

## 校验规则

1. **结构校验**：五要素齐备、字段类型正确；节点数上限按 spec_version 区分（`"1.0"` ≤ 全局配置 `WORKFLOW_MAX_NODES` 默认 50；`"1.1"` ≤ 200，见兼容矩阵）
2. **拓扑校验**：无非法环（含循环节点的回边为合法环）、图连通
3. **配置校验**：必备字段齐全、数据源查询无写操作、表达式受限

## 版本与兼容性承诺

> 版本治理专页（1.x 只增不删承诺 / 2.0 边界 / 护栏机制）：[spec-versioning.md](./spec-versioning.md)。

### 版本策略（语义化版本）

规范采用语义化版本（MAJOR.MINOR）：

- **MINOR（1.x 内递增）**：追加新字段、新节点类型、放宽校验规则。1.x 内变更遵循下方「只增不删」承诺。
- **MAJOR（1.x → 2.0）**：破坏性变更（删除既有字段/节点类型、收窄校验规则、变更既有字段语义）。破坏性变更只能进 2.0，且必须附迁移指南（见「迁移指南」章）。

解析器版本支持现状（事实来源：`workflow/specification_version_registry.py`，精确匹配、不做降级解析）：

- 支持：`"1.0"`、`"1.1"`。
- 未支持：`"1.2"` 及以上——版本高于支持区间时解析终止并报错提示当前支持区间，**不尝试降级解析**（避免用旧规则误读新版本定义引入隐蔽错误）。

### 1.x 内只增不删承诺

1.x 系列内：

1. 既有**字段**不删除、不改名、不变更语义；追加可选字段允许。
2. 既有**节点类型**（`node_type` 取值）不删除、不改名；追加新类型允许（见「NodeType 义务」）。
3. 既有**校验规则**不收窄、不删除；放宽允许（如提升节点数上限、放宽表达式约束）。

存量 YAML（含 `examples/` 12 套范例共 16 个文件，其中 14 个工作流定义、2 个伴随 Agent 清单；2026-11-22 B7 W4 复核实测）在任何 1.x 版本解析器下保持可加载。

### 兼容矩阵（spec_version × 引擎支持状态 × 已知边界）

| spec_version | 解析/结构校验 | 拓扑/配置校验 | 编译（GraphCompiler） | 节点数上限 | 已知边界 |
| --- | --- | --- | --- | --- | --- |
| `"1.0"` | 支持 | 支持 | 支持 | 全局配置 `WORKFLOW_MAX_NODES`（默认 50） | — |
| `"1.1"` | 支持 | 支持 | 支持 | 200（解析器按 spec_version 覆写） | — |
| `"1.2"` 及以上 | 拒绝（精确匹配失败） | — | — | — | 不降级解析；错误信息附当前支持区间 |
| 缺失/非版本号 | 拒绝（必填五要素之一） | — | — | — | — |

节点类型执行边界（照实登记，不以承诺掩盖现状）：

- 当前枚举 14 个节点类型在解析/校验/编译层均受支持（清单见下）。
- **SUPERVISOR**：枚举/解析/编译支持；执行器注册受配置开关 `supervisor_orchestration_enabled` 控制，默认 `False`——**执行器不注册，运行期 SUPERVISOR 节点按未知节点类型拦截**。
- **LLM / CODE**：枚举/解析/静态校验支持；执行受 `llm_node_enabled` / `code_node_enabled` 开关控制，默认 `False`（关闭时分别返回 `LLM_NODE_DISABLED` / `CODE_NODE_DISABLED`，不执行）。

### NodeType 义务（HC-02：枚举仅追加）

- **义务**：`NodeType` 枚举成员仅追加——既有成员不删除、枚举值（字符串字面量）不变更；新增节点类型仅以追加方式进入枚举。
- **现状清单**（14 个，与 `domain/models/enums.py` 逐字符一致）：`agent`、`tool`（已弃用：前端不再新建，存量解析兼容，成员保留不删）、`data_source`、`trigger`、`control_flow`、`subworkflow`、`dynamic_injector`、`data_transform`、`plugin`、`supervisor`、`output_display`、`vector_knowledge_base`、`llm`、`code`。
- 本条为引擎侧对「1.x 只增不删」承诺的对应义务；本轮零节点类型变更（承诺而非新改动）。

## 示例与范例参考

`examples/` 下共 12 套范例（16 个 YAML：14 个工作流定义 + 2 个伴随 Agent 清单；2026-11-22 B7 W4 复核）：

| 范例目录 | 定义文件 | spec_version | 主题 |
| --- | --- | --- | --- |
| sequential_pipeline | sequential_pipeline.yaml | `"1.0"` | 顺序流水线 |
| supervisor_routing | supervisor_routing.yaml | `"1.0"` | 监督者路由 |
| conditional_loop_pipeline | conditional_loop_pipeline.yaml | `"1.0"` | 条件分支 + 评审循环 |
| feature_engineering | feature_engineering.yaml（+ feature_engineering_agents.yaml 清单） | `"1.1"` | 特征工程 |
| drift_detection | drift_detection.yaml（+ drift_detection_agents.yaml 清单） | `"1.1"` | 漂移检测 |
| parallel_execution | parallel_execution.yaml（另含 parallel_fanout.yaml 严格扇出范例，B6 增） | `"1.1"` | 并行汇聚 / 严格扇出 |
| hierarchical_delegation | hierarchical_delegation.yaml（另含 team_lead_subworkflow.yaml 被嵌套定义） | `"1.1"` | 层级委派 + 子工作流 |
| reflection_iteration | reflection_iteration.yaml | `"1.1"` | 反思迭代 |
| data_engineering | data_engineering_agent.yaml | `"1.1"` | 数据工程 |
| data_transform_pipeline | data_transform_pipeline.yaml | `"1.1"` | 数据转换流水线 |
| subworkflow_nesting | subworkflow_nesting.yaml | `"1.1"` | 子工作流嵌套 |
| realtime_subscription | realtime_subscription_workflow.yaml | `"1.1"` | 实时订阅 |

### 使用 wfp CLI 校验范例

```bash
wfp validate examples/                                # 目录递归校验全部 *.yaml
wfp validate examples/sequential_pipeline/sequential_pipeline.yaml
wfp graph examples/conditional_loop_pipeline/conditional_loop_pipeline.yaml   # Mermaid flowchart TD
wfp diff base.yaml head.yaml                          # 语义对比（拓扑/参数/布局）
wfp run --local examples/sequential_pipeline/sequential_pipeline.yaml         # 本地单进程执行
```

四命令公共支持 `--json` 机器读输出。exit code 约定（单一权威 `cli/output.py`）：

| 命令 | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `validate` | 全部通过 | 任一校验失败 | 任一加载异常（优先级 2 > 1 > 0） |
| `diff` | 对比完成（有变更也是 0） | 编译/校验失败 | 双侧任一解析失败 |
| `graph` | 成功输出 | — | 失败（加载或编译） |
| `run --local` | 执行成功 | 校验/执行失败（含执行器禁用快速失败） | 解析失败 |

`validate` 文本汇总行格式：「共 N 个文件 —— X 通过 / Y 失败 / Z 跳过 / W 加载异常」；范例库伴随 Agent 清单（顶层 `agents` 键且无 `spec_version`）识别为 SKIP，不计失败。

## 迁移指南

### spec_version `"1.0"` → `"1.1"`（可选升级，纯追加）

- `"1.1"` 相对 `"1.0"` 仅追加：新增节点类型（`subworkflow` / `dynamic_injector` / `data_transform`）与节点数上限放宽（50 → 200）。
- 既有 `"1.0"` 定义无需迁移：可继续以 `"1.0"` 使用（「1.x 只增不删」承诺）。
- 仅当需要上述新节点类型或节点数超过 50 时，将 `spec_version` 字段值改为 `"1.1"`（无其他字段结构变化）。

### 存量 tool 节点（已弃用，兼容保留）

- `tool` 节点已弃用：前端不再新建，解析兼容保留（存量定义无需强制迁移，枚举成员不删除）。
- 新建定义请改用 `agent` 节点承载工具调用（`configuration.available_tools` 组合）。

### 2.0 迁移指南（承诺占位，当前无 2.0）

- 破坏性变更（删除既有字段/节点类型、收窄校验规则）只能随 2.0 发布，且必须附具体迁移步骤（届时在本章补齐）。
- 1.x 期间不存在需要迁移的破坏性变更。
