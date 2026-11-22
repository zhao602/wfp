# 工作流节点使用指南

> 本文档涵盖工作流编排平台全部 8 种节点类型的配置说明、参数释义与添加方式。
> 信息来源：前端表单 `web/src/canvas/NodeConfigurationPanel.tsx`、后端配置模型 `src/orchestration_platform/domain/models/node_configurations.py`、枚举定义 `enums.py`、提示文案 `nodeConfigHints.ts`。

---

## 目录

1. [通用说明](#1-通用说明)
2. [触发器节点 Trigger](#2-触发器节点-trigger)
3. [智能体节点 Agent](#3-智能体节点-agent)
4. [工具节点 Tool](#4-工具节点-tool)
5. [数据源节点 Data Source](#5-数据源节点-data-source)
6. [控制流节点 Control Flow](#6-控制流节点-control-flow)
7. [子工作流节点 SubWorkflow](#7-子工作流节点-subworkflow)
8. [动态注入节点 Dynamic Injector](#8-动态注入节点-dynamic-injector)
9. [数据转换节点 Data Transform](#9-数据转换节点-data-transform)
10. [附录：枚举值速查表](#10-附录枚举值速查表)

---

## 1. 通用说明

### 1.1 节点类型总览

| 节点类型 | 标识 | 用途一句话 | 前端可配参数数 |
|----------|------|-------------|----------------|
| 触发器 | `trigger` | 工作流入口，定义启动方式 | 3 |
| 智能体 | `agent` | 大模型驱动的推理/生成/决策 | 2 |
| 工具 | `tool` | 确定性工具调用 | 2 |
| 数据源 | `data_source` | 从外部数据源只读读取数据 | 2 |
| 控制流 | `control_flow` | 分支/循环/并行/人工审核 | 1~4 |
| 子工作流 | `subworkflow` | 嵌套调用另一个已保存的工作流 | 3 |
| 动态注入 | `dynamic_injector` | 运行时按数据动态生成节点 | 4 |
| 数据转换 | `data_transform` | 映射/过滤/聚合/连接 | 1+ |

### 1.2 节点名称

每种节点都有一个 **节点名称**（`name`）字段，用于在画布上标识该节点，由创建时确定，**暂不支持修改**（前端以只读输入框呈现）。

### 1.3 前端可配 vs 后端完整参数

前端配置面板（`NodeConfigurationPanel.tsx`）仅暴露了**核心参数**供用户在画布上编辑。后端配置模型（`node_configurations.py`）包含更多参数，需通过工作流定义 YAML/JSON 或 API 直接配置。下文每节均以两张表区分标注。

### 1.4 如何在画布上添加节点

在画布左侧的**节点库/示例库**面板中，选择所需节点类型，拖拽到画布区域即可创建该类型节点。选中节点后，右侧弹出**节点配置面板**，可在其中填写参数。

---

## 2. 触发器节点 Trigger

### 2.1 用途

工作流的起始节点，定义流程以何种方式被触发。每个工作流必须有且仅有一个触发器节点。

### 2.2 前端可配置参数

| 参数 | 标签 | 控件 | 说明 |
|------|------|------|------|
| `trigger_type` | 触发方式 | 下拉选择 | 选择启动方式：手动 / 定时 / 事件 |
| `schedule` | 定时频率 | 频率选择 | **仅当触发方式为"定时"时显示**。选择预设频率并设置参数，系统自动生成定时表达式 |
| `input_template` | 输入模板 | 键值对编辑 | 定义触发时的输入参数映射：键为参数名，值为触发上下文字段引用（语义详见 2.5） |

**前端提示文案**：
- 节点级说明：*"工作流的起始节点，定义流程以何种方式被触发。"*
- `trigger_type`：*"选择该流程的启动方式：手动触发、按计划定时执行或由数据事件驱动。"*
- `schedule`：*"选择预设频率并设置参数，系统自动生成定时表达式。仅在触发方式为定时时生效。"* placeholder：`请选择频率`
- `input_template`：*"定义触发时的输入参数映射：键为参数名，值为触发上下文字段引用。手动与数据事件触发下生效（触发上下文初值为实例启动入参）；定时触发暂无运行时入口、模板不生效。任一引用字段缺失将中止启动并报错。"*

### 2.3 后端完整参数

| 参数 | 类型 | 必备 | 说明 | 前端可配 |
|------|------|------|------|----------|
| `trigger_type` | `TriggerType` | 是 | 触发方式 | ✅ |
| `schedule` | `str` | scheduled 时必填 | 定时表达式 | ✅ |
| `event_source` | `str` | data_event 时必填 | 事件源标识 | ❌ |
| `input_template` | `Dict[str, str]` | 否 | 启动参数模板：输入参数名 → 触发上下文字段引用 | ✅ |

### 2.4 触发方式枚举

| 值 | 标签 | 说明 |
|----|------|------|
| `manual` | 手动 | 由接口调用驱动 |
| `scheduled` | 定时 | 由定时调度器驱动，需填 `schedule` |
| `data_event` | 事件 | 由数据源事件信号驱动，需填 `event_source` |

### 2.5 输入模板（input_template）详解

`input_template` 定义**触发时的输入参数映射**：键为输出参数名，值为**触发上下文字段引用**。触发器节点作为起始节点没有上游产出，但触发上下文并非空集——模板的真实价值在于**把触发上下文收敛 / 重命名 / 裁剪为触发器节点的显式命名输出契约**，供下游节点在字段映射中引用。

**用途**：

- **收敛**：把动态的触发上下文固化为明确的输出字段集合，下游统一引用触发器输出而非散落的入参；
- **重命名**：参数名与触发上下文字段名不同时完成改名（如 `subject: topic`）；
- **裁剪**：仅暴露下游真正需要的字段。

**适用场景（按触发方式）**：

| 触发方式 | 是否生效 | 触发上下文初值 |
|----------|----------|----------------|
| `manual`（手动） | ✅ 生效 | 实例启动入参（API 调用传入的 inputs） |
| `data_event`（事件） | ✅ 生效 | 数据事件负载（事件信号携带的字段） |
| `scheduled`（定时） | ❌ 不生效 | 无（见下方已知限制） |

**已知限制**：定时触发（`scheduled`）调度能力缺失——当前无定时调度运行时入口，实例不会因定时被自动启动，模板在定时场景下无实际触发路径（技术债登记见 `docs/tech-debt-ledger.md`）。

**失败行为**：

- 工作流定义校验：模板**键 / 值为空字符串**即报 `WF_VALIDATION_FAILED`（阻断保存）；
- 运行时：非空模板映射**全有或全无**——触发上下文中任一被引用字段缺失，工作流启动即中止，报 `TRIGGER_PAYLOAD_MAPPING_FAILED`（错误信息含缺失字段名清单）。

**可省略性**：恒等映射（如 `topic: topic`）且下游直接引用启动入参时，模板可省略（触发上下文已含同名字段）。

---

## 3. 智能体节点 Agent

### 3.1 用途

由大模型驱动的智能节点，依据提示词完成推理、生成或决策任务。属于**概率性节点**（相同输入可能产生不同输出）。

### 3.2 前端可配置参数

| 参数 | 标签 | 控件 | 说明 |
|------|------|------|------|
| `model_identifier` | 模型标识 | 文本输入 | 填写使用的模型名称，需与平台已接入的模型一致 |
| `system_prompt` | 系统提示词 | 多行文本 | 描述角色、目标与行为约束 |

**前端提示文案**：
- 节点级说明：*"由大模型驱动的智能节点，依据提示词完成推理、生成或决策任务。"*
- `model_identifier`：*"填写该节点使用的模型名称，需与平台已接入的模型保持一致。"* placeholder：`例如：通用对话模型`
- `system_prompt`：*"描述该智能体的角色、目标与行为约束，作为其始终遵循的系统指令。"* placeholder：`例如：你是一名严谨的数据分析助手`

### 3.3 后端完整参数

| 参数 | 类型 | 必备 | 说明 | 前端可配 |
|------|------|------|------|----------|
| `model_identifier` | `str` | 是 | 模型标识 | ✅ |
| `system_prompt` | `str` | 否 | 系统提示词 | ✅ |
| `available_tools` | `List[str]` | 否 | 可用工具标识集合 | ❌ |
| `output_schema` | `Dict[str, str]` | 否 | 输出结构约束（字段名 → 类型），用于结构化输出校验 | ❌ |
| `max_tool_rounds` | `int` (≥1) | 否 | 工具调用最大轮次（缺省用全局配置） | ❌ |
| `output_max_retries` | `int` (≥0) | 否 | 输出重生成最大次数（缺省用全局配置） | ❌ |
| `sandbox_policy_id` | `str` | 否 | 沙箱策略标识（None 则不启用沙箱隔离） | ❌ |

---

## 4. 工具节点 Tool

### 4.1 用途

调用外部工具或本地能力，完成一项具体操作。属于**确定性节点**（相同入参返回相同结果）。

### 4.2 前端可配置参数

| 参数 | 标签 | 控件 | 说明 |
|------|------|------|------|
| `tool_identifier` | 工具标识 | 文本输入 | 填写要调用的工具名称，需与已注册工具标识一致 |
| `tool_source` | 工具来源 | 下拉选择 | 本地（local）/ MCP |

**前端提示文案**：
- 节点级说明：*"调用外部工具或本地能力，完成一项具体操作的节点。"*
- `tool_identifier`：*"填写要调用的工具名称，需与已注册的工具标识保持一致。"* placeholder：`例如：天气查询`
- `tool_source`：*"选择工具的来源：本地内置工具或通过 MCP 接入的外部工具。"*

### 4.3 后端完整参数

| 参数 | 类型 | 必备 | 说明 | 前端可配 |
|------|------|------|------|----------|
| `tool_identifier` | `str` | 是 | 工具标识 | ✅ |
| `tool_source` | `"local" \| "mcp"` | 否（默认 local） | 工具来源 | ✅ |
| `argument_mapping` | `Dict[str, str]` | 否 | 入参映射：工具参数名 → 共享状态字段引用 | ❌ |
| `static_arguments` | `Dict[str, object]` | 否 | 静态入参（常量） | ❌ |

### 4.4 怎么添加工具

工具节点的 `tool_identifier` 引用的是**已注册工具**。添加新工具有两种途径：

#### 4.4.1 添加本地工具（local）

本地工具通过**代码层面注册**，无前端入口：

```python
# 在引擎初始化时注册工具调用器
from orchestration_platform.engine.nodes.tool_node_executor import ToolNodeExecutor

executor = ToolNodeExecutor()
executor.register_tool(invoker)  # invoker 须实现 IToolInvoker 接口
```

- 需实现 `IToolInvoker` 接口（定义 `tool_identifier` 属性和 `invoke()` 异步方法）
- 注册后，工具节点的 `tool_identifier` 填写该工具的标识即可调用
- 调用机制：`ToolNodeExecutor` 维护 `tool_identifier → IToolInvoker` 映射，执行时按标识查找

#### 4.4.2 添加 MCP 工具（mcp）

MCP 工具通过**贡献发布流程**添加，平台有完整的 MCP 工具市场（`src/orchestration_platform/mcp_tool_market/`）：

1. **提交**：通过贡献 API（`contribution_router`）提交 MCP 工具包
2. **校验**：签名校验（`signature_verifier`）→ 静态安全扫描（`static_security_scanner`）→ 状态机审核（`state_machine`）
3. **入库**：审核通过后持久化到 `mcp_tool_registrations` 表（PostgreSQL）或本地文件仓储
4. **查询**：`GET /api/v1/mcp/server/tools` 返回平台暴露的 MCP 工具清单
5. **版本管理**：`version_manager.py` 管理工具版本
6. **权限校验**：`permission_checker.py` 校验调用权限

MCP 工具权限级别（`ToolPermission`）：

| 值 | 说明 |
|----|------|
| `readonly` | 只读：仅查询不产生副作用 |
| `readwrite` | 读写：可修改状态 |
| `admin` | 管理：可注册/注销工具 |

---

## 5. 数据源节点 Data Source

### 5.1 用途

从外部数据源读取数据，为后续处理提供输入。查询语句**只读**，禁止写/删/结构变更操作。

### 5.2 前端可配置参数

| 参数 | 标签 | 控件 | 说明 |
|------|------|------|------|
| `connection_reference` | 连接引用 | 文本输入 | 填写已配置的数据连接名称 |
| `query` | 只读查询 | 多行文本 | 填写只读查询语句 |

**前端提示文案**：
- 节点级说明：*"从外部数据源读取数据，为后续处理提供输入的节点。"*
- `connection_reference`：*"填写已配置的数据连接名称，用于定位要访问的数据源。"* placeholder：`例如：生产数据仓库`
- `query`：*"填写只读查询语句，用于从数据源中读取所需数据。"* placeholder：`例如：查询近七天的订单`

### 5.3 后端完整参数

| 参数 | 类型 | 必备 | 说明 | 前端可配 |
|------|------|------|------|----------|
| `connection_reference` | `str` | 是 | 数据源连接引用 | ✅ |
| `query` | `str` | 否（默认空） | 只读查询语句（禁止写操作） | ✅ |
| `dataset_identifier` | `str` | 否 | 数据集/数据流标识 | ❌ |

### 5.4 怎么添加数据源连接

`connection_reference` 引用的是**已配置的数据连接**。数据连接通过 `DataSourceConnectionConfig`（`integrations/data_source_connectors/models.py`）定义，支持 11 种数据源类型：

| 数据源类型 | 标识 | 关键配置参数 |
|------------|------|-------------|
| CSV 文件 | `csv` | `file_path`, `encoding` |
| JSON 文件 | `json` | `file_path`, `encoding` |
| REST API | `rest_api` | `url`, `method`, `headers` |
| 数据库 | `database` | `connection_string`, `query` |
| Hologres 实时数仓 | `hologres` | `hologres_endpoint`, `hologres_port`, `hologres_database`, `hologres_access_key` |
| Kafka 消息流 | `kafka` | `kafka_brokers`, `kafka_topic`, `kafka_group_id`, `kafka_offset` |
| Flink 流计算 | `flink` | `flink_jobmanager_url`, `flink_jar_id`, `flink_program_args` |
| Spark 批计算 | `spark` | `spark_master_url`, `spark_submit_args`, `spark_app_name` |
| CRM 系统 | `crm` | 第四阶段新增 |
| ERP 系统 | `erp` | 第四阶段新增 |
| OA 系统 | `oa` | 第四阶段新增 |

**连接配置通用字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `source_type` | `DataSourceType` | 数据源类型 |
| `connection_reference` | `str` | 连接引用标识（与节点配置中的 `connection_reference` 对应） |
| `file_path` | `str` | 文件路径（CSV/JSON） |
| `url` | `str` | REST API 端点 URL |
| `method` | `str` | HTTP 方法（默认 GET） |
| `headers` | `Dict[str,str]` | 请求头 |
| `connection_string` | `str` | 数据库连接串 |
| `encoding` | `str` | 文件编码（默认 utf-8） |

> **注意**：凭据（如 `hologres_access_key`）不出现在日志中。数据连接持久化到 `data_source_connections` 表。

---

## 6. 控制流节点 Control Flow

### 6.1 用途

控制流程走向的节点，可实现条件分支、循环、并行汇聚与人工审核。**控制流节点禁止包含业务动作字段**（模型/工具/数据源），以结构约束落实职责分离。

### 6.2 前端可配置参数

| 参数 | 标签 | 控件 | 显示条件 | 说明 |
|------|------|------|----------|------|
| `control_flow_kind` | 控制流子类型 | 下拉选择 | 始终 | 选择子类型 |
| `loop_body_entry` | 循环体入口 | 文本输入 | kind=loop | 循环体入口节点标识 |
| `loop_termination_condition` | 终止条件 | 文本输入 | kind=loop | 循环终止条件表达式 |
| `max_iterations` | 最大迭代次数 | 数字输入 | kind=loop | 循环最大迭代次数 |

**前端提示文案**：
- 节点级说明：*"控制流程走向的节点，可实现条件分支、循环、并行汇聚与人工审核。"*
- `control_flow_kind`：*"选择控制流的类型：条件分支、循环、并行汇聚或人工审核。"*
- `loop_body_entry`：*"（可选）填写循环体的入口节点，循环将从该节点开始执行。"* placeholder：`例如：处理单条记录`
- `loop_termination_condition`：*"（可选）填写循环结束条件，满足该条件时退出循环。"* placeholder：`例如：已处理全部记录`
- `max_iterations`：*"（可选）限制循环的最大执行次数，避免陷入无限循环。"*

### 6.3 后端完整参数

| 参数 | 类型 | 适用子类型 | 必备 | 说明 | 前端可配 |
|------|------|-----------|------|------|----------|
| `control_flow_kind` | `ControlFlowKind` | 全部 | 是 | 控制流子类型 | ✅ |
| `conditions` | `List[BranchCondition]` | 条件分支 | 否 | 分支条件列表（按声明顺序求值） | ❌ |
| `default_branch_target` | `str` | 条件分支 | 否 | 默认分支目标节点（全不命中时使用） | ❌ |
| `loop_body_entry` | `str` | 循环 | 否 | 循环体入口节点标识 | ✅ |
| `loop_termination_condition` | `str` | 循环 | 否 | 循环终止条件表达式 | ✅ |
| `max_iterations` | `int` (≥1) | 循环 | 否 | 循环最大迭代次数 | ✅ |
| `parallel_failure_strategy` | `ParallelFailureStrategy` | 并行汇聚 | 否（默认 fast_fail） | 并行失败策略 | ❌ |
| `parallel_branches` | `List[str]` | 并行汇聚 | 否 | 并行分支入口节点标识列表 | ❌ |
| `review_instructions` | `str` | 人工审核 | 否 | 审核说明（供人工决策参考） | ❌ |
| `review_timeout_action` | `"reject" \| "wait"` | 人工审核 | 否（默认 reject） | 审核超时动作 | ❌ |

### 6.4 子类型枚举

| 值 | 标签 | 说明 |
|----|------|------|
| `conditional_branch` | 条件分支 | 依据表达式求值选择唯一后继 |
| `loop` | 循环 | 按终止条件重复执行循环体，设最大迭代上限 |
| `parallel_join` | 并行汇聚 | 等待全部上游分支完成或快速失败 |
| `human_review` | 人工审核 | 挂起实例等待人工决策 |

### 6.5 条件分支参数详解

`BranchCondition` 结构（后端 `conditions` 列表中每项）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `target_node_id` | `str` | 条件为真时跳转的目标节点标识 |
| `expression` | `str` | 受限条件表达式，如 `output.score >= 80`（仅允许读取共享状态字段的比较与逻辑运算，禁止函数调用） |
| `label` | `str` | 分支标签（画布展示用，可选） |

### 6.6 并行失败策略枚举

| 值 | 说明 |
|----|------|
| `fast_fail` | 快速失败：任一分支失败即终止，其余分支置已取消 |
| `wait_all` | 等待全部：等待全部分支结束后统一汇总失败 |

---

## 7. 子工作流节点 SubWorkflow

### 7.1 用途

在当前流程中嵌套调用另一个已保存的工作流。

### 7.2 前端可配置参数

| 参数 | 标签 | 控件 | 说明 |
|------|------|------|------|
| `subworkflow_definition_id` | 子工作流定义 ID | 文本输入 | 填写要调用的子工作流标识 |
| `context_strategy` | 上下文策略 | 下拉选择 | 继承（inherit）/ 隔离（isolate） |
| `max_nesting_depth` | 最大嵌套深度 | 数字输入 | 限制嵌套层数，防止递归溢出 |

**前端提示文案**：
- 节点级说明：*"在当前流程中嵌套调用另一个已保存的工作流。"*
- `subworkflow_definition_id`：*"填写要调用的子工作流标识，需为已存在的工作流。"* placeholder：`例如：数据清洗流程`
- `context_strategy`：*"选择上下文的传递方式：继承上游上下文，或隔离后独立执行。"*
- `max_nesting_depth`：*"限制子工作流的最大嵌套层数，防止调用层级过深。"*

### 7.3 后端完整参数

| 参数 | 类型 | 必备 | 说明 | 前端可配 |
|------|------|------|------|----------|
| `subworkflow_definition_id` | `str` | 是 | 子工作流定义标识 | ✅ |
| `subworkflow_version` | `int` (≥1) | 否 | 子工作流版本（缺省取最新） | ❌ |
| `input_mapping` | `Dict[str, str]` | 否 | 输入映射：子工作流参数名 → 父共享状态字段引用 | ❌ |
| `output_mapping` | `Dict[str, str]` | 否 | 输出映射：父状态字段名 → 子工作流输出参数名 | ❌ |
| `context_strategy` | `SubworkflowContextStrategy` | 否（默认 isolate） | 上下文传递策略 | ✅ |
| `max_nesting_depth` | `int` (≥1) | 否（默认 5） | 最大嵌套深度 | ✅ |

### 7.4 上下文策略枚举

| 值 | 标签 | 说明 |
|----|------|------|
| `inherit` | 继承 | 子工作流共享父工作流只读状态视图 |
| `isolate` | 隔离 | 深拷贝父状态作为初始值，修改不影响父状态 |

### 7.5 怎么添加子工作流

1. 先独立创建并保存另一个工作流定义，获得其工作流定义 ID
2. 在当前工作流中添加子工作流节点，将 `subworkflow_definition_id` 填写为该 ID
3. 如需固定版本，在后端配置中指定 `subworkflow_version`

---

## 8. 动态注入节点 Dynamic Injector

### 8.1 用途

在运行期按数据动态生成并注入节点，用于处理数量不定的任务。

### 8.2 前端可配置参数

| 参数 | 标签 | 控件 | 说明 |
|------|------|------|------|
| `source_expression` | 数据源表达式 | 文本输入 | 填写数据来源表达式，确定需注入的数据 |
| `template_node_id` | 模板节点 ID | 文本输入 | 填写作为模板的节点标识 |
| `max_injection_count` | 最大注入数量 | 数字输入 | 限制单次最多注入的节点数量 |
| `injection_key_field` | 注入键字段 | 文本输入 | 填写用于区分各注入节点的字段名 |

**前端提示文案**：
- 节点级说明：*"在运行期按数据动态生成并注入节点，用于处理数量不定的任务。"*
- `source_expression`：*"填写数据来源表达式，用于确定需要注入的数据。"* placeholder：`例如：上游输出列表`
- `template_node_id`：*"填写作为模板的节点标识，新节点将依据该模板生成。"* placeholder：`例如：处理单条记录`
- `max_injection_count`：*"限制单次最多注入的节点数量，用于控制资源占用。"*
- `injection_key_field`：*"填写用于区分各个注入节点的字段名。"* placeholder：`例如：编号`

### 8.3 后端完整参数

| 参数 | 类型 | 必备 | 说明 | 前端可配 |
|------|------|------|------|----------|
| `injection_condition` | `str` | 是 | 注入条件表达式（受限求值） | ❌（前端用 `source_expression`） |
| `node_templates` | `List[NodeTemplate]` | 否 | 待注入节点模板列表 | ❌（前端用 `template_node_id`） |
| `max_injection_count` | `int` (≥1) | 否（默认 100） | 单次执行最大注入数量 | ✅ |
| `target_position` | `str` | 否 | 注入位置：注入节点后接的目标节点标识 | ❌ |
| `injection_key_field` | `str` | — | 注入键字段 | ✅ |

> **注意**：前端字段名（`source_expression`、`template_node_id`）与后端字段名（`injection_condition`、`node_templates`）存在差异，前端为简化配置视图。

### 8.4 节点模板结构（NodeTemplate）

后端 `node_templates` 列表中每项的结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `template_id` | `str` | 模板标识 |
| `node_type` | `NodeType` | 模板节点类型 |
| `name` | `str` | 模板节点名称 |
| `configuration` | `Dict[str, object]` | 模板节点配置（键值对） |

---

## 9. 数据转换节点 Data Transform

### 9.1 用途

对上游数据进行映射、过滤、聚合或连接等转换处理。

### 9.2 前端可配置参数

| 参数 | 标签 | 控件 | 说明 |
|------|------|------|------|
| `operations` | 操作数量 | 只读显示 | 显示当前已配置的操作数量（如"3 个操作"） |
| `operation`（每个操作） | 操作 N | 下拉选择 | 选择该条转换的操作类型 |

**前端提示文案**：
- 节点级说明：*"对上游数据进行映射、过滤、聚合或连接等转换处理。"*
- `operations`：*"当前已配置的数据转换操作数量，仅供查看。"*
- `operation`：*"选择本条转换的操作类型：映射、过滤、聚合或连接。"*

### 9.3 后端完整参数

| 参数 | 类型 | 必备 | 说明 | 前端可配 |
|------|------|------|------|----------|
| `operation` | `DataTransformOperation` | 是 | 转换操作类型 | ✅ |
| `input_dataset_reference` | `str` | 是 | 输入数据集引用（共享状态字段名） | ❌ |
| `params` | `DataTransformOperationParams` | 否 | 操作参数（按操作类型不同） | ❌ |

### 9.4 操作类型枚举

| 值 | 标签 | 说明 |
|----|------|------|
| `map` | 映射 | 按表达式逐行变换 |
| `filter` | 过滤 | 按条件保留子集 |
| `aggregate` | 聚合 | 按分组键与聚合函数汇总 |
| `join` | 连接 | 按键连接两个数据集 |

### 9.5 操作参数详解（DataTransformOperationParams）

不同操作类型使用不同参数子集：

#### 映射（map）

| 参数 | 类型 | 说明 |
|------|------|------|
| `map_expression` | `str` | 映射表达式（逐行变换） |

#### 过滤（filter）

| 参数 | 类型 | 说明 |
|------|------|------|
| `filter_condition` | `str` | 过滤条件表达式 |

#### 聚合（aggregate）

| 参数 | 类型 | 说明 |
|------|------|------|
| `group_by` | `List[str]` | 分组字段列表 |
| `aggregate_function` | `AggregateFunction` | 聚合函数 |
| `aggregate_field` | `str` | 聚合目标字段 |

**聚合函数枚举**：

| 值 | 说明 |
|----|------|
| `sum` | 求和 |
| `count` | 计数 |
| `avg` | 平均 |
| `max` | 最大 |
| `min` | 最小 |

#### 连接（join）

| 参数 | 类型 | 说明 |
|------|------|------|
| `join_type` | `JoinType` | 连接类型 |
| `join_key` | `str` | 连接键字段名 |
| `right_dataset_reference` | `str` | 右数据集引用（共享状态字段名） |

**连接类型枚举**：

| 值 | 说明 |
|----|------|
| `inner` | 内连接 |
| `left` | 左外连接 |
| `right` | 右外连接 |
| `full` | 全外连接 |

### 9.6 怎么添加/编辑转换操作

**前端现状**：当前配置面板仅**只读展示**已有操作数量和每个操作的类型选择，**没有添加/删除操作的按钮**。操作的详细参数（`params`、`input_dataset_reference`）需通过工作流定义 YAML/JSON 或 API 直接配置。

**通过工作流定义配置操作示例**：

```yaml
node_type: data_transform
operation: aggregate
input_dataset_reference: upstream.orders
params:
  group_by: ["region"]
  aggregate_function: sum
  aggregate_field: "amount"
```

如需在前端增加操作添加/删除/编辑能力，属于功能增强需求，需走研发流程。

---

## 10. 附录：枚举值速查表

### 10.1 NodeType（节点类型）

| 值 | 说明 |
|----|------|
| `agent` | 概率性推理节点 |
| `tool` | 确定性工具调用节点 |
| `data_source` | 只读数据源读取节点 |
| `trigger` | 工作流入口触发器节点 |
| `control_flow` | 控制流节点 |
| `subworkflow` | 子工作流嵌套节点 |
| `dynamic_injector` | 动态注入节点 |
| `data_transform` | 数据转换节点 |
| `plugin` | 插件节点（第三方） |
| `supervisor` | 监督者编排节点 |

### 10.2 TriggerType（触发方式）

| 值 | 标签 | 说明 |
|----|------|------|
| `manual` | 手动 | 接口调用驱动 |
| `scheduled` | 定时 | 定时调度器驱动 |
| `data_event` | 事件 | 数据源事件信号驱动 |

### 10.3 ControlFlowKind（控制流子类型）

| 值 | 标签 |
|----|------|
| `conditional_branch` | 条件分支 |
| `loop` | 循环 |
| `parallel_join` | 并行汇聚 |
| `human_review` | 人工审核 |

### 10.4 ParallelFailureStrategy（并行失败策略）

| 值 | 说明 |
|----|------|
| `fast_fail` | 快速失败 |
| `wait_all` | 等待全部 |

### 10.5 SubworkflowContextStrategy（上下文策略）

| 值 | 标签 |
|----|------|
| `inherit` | 继承 |
| `isolate` | 隔离 |

### 10.6 DataTransformOperation（转换操作类型）

| 值 | 标签 |
|----|------|
| `map` | 映射 |
| `filter` | 过滤 |
| `aggregate` | 聚合 |
| `join` | 连接 |

### 10.7 JoinType（连接类型）

| 值 | 说明 |
|----|------|
| `inner` | 内连接 |
| `left` | 左外连接 |
| `right` | 右外连接 |
| `full` | 全外连接 |

### 10.8 AggregateFunction（聚合函数）

| 值 | 说明 |
|----|------|
| `sum` | 求和 |
| `count` | 计数 |
| `avg` | 平均 |
| `max` | 最大 |
| `min` | 最小 |

### 10.9 ToolSource（工具来源）

| 值 | 标签 | 说明 |
|----|------|------|
| `local` | 本地 | 平台内置函数工具 |
| `mcp` | MCP | 通过 MCP 协议接入的外部工具 |

### 10.10 ToolPermission（MCP 工具权限）

| 值 | 说明 |
|----|------|
| `readonly` | 只读 |
| `readwrite` | 读写 |
| `admin` | 管理 |

### 10.11 DataSourceType（数据源类型）

| 值 | 说明 |
|----|------|
| `csv` | CSV 文件 |
| `json` | JSON 文件 |
| `rest_api` | REST API 端点 |
| `database` | 数据库（SQL 查询） |
| `hologres` | Hologres 实时数仓 |
| `kafka` | Kafka 消息流 |
| `flink` | Flink 流计算引擎 |
| `spark` | Spark 批计算引擎 |
| `crm` | CRM 系统 |
| `erp` | ERP 系统 |
| `oa` | OA 系统 |

---

## 11. 前端 vs 后端参数差异汇总

以下参数在后端配置模型中存在，但**前端配置面板未暴露**，需通过工作流定义 YAML/JSON 或 API 配置：

| 节点类型 | 未暴露参数 | 说明 |
|----------|-----------|------|
| Trigger | `event_source` | 事件源标识 |
| Agent | `available_tools`, `output_schema`, `max_tool_rounds`, `output_max_retries`, `sandbox_policy_id` | 可用工具集、输出约束、轮次/重试限制、沙箱策略 |
| Tool | `argument_mapping`, `static_arguments` | 入参映射、静态入参 |
| Data Source | `dataset_identifier` | 数据集标识 |
| Control Flow | `conditions`, `default_branch_target`, `parallel_failure_strategy`, `parallel_branches`, `review_instructions`, `review_timeout_action` | 分支条件、默认目标、并行策略/分支、审核说明/超时动作 |
| SubWorkflow | `subworkflow_version`, `input_mapping`, `output_mapping` | 版本固定、输入输出映射 |
| Dynamic Injector | `injection_condition`, `node_templates`, `target_position` | 注入条件、模板列表、注入位置（前端用简化字段名） |
| Data Transform | `input_dataset_reference`, `params` | 输入数据集引用、操作详细参数 |

> 如需在前端暴露更多参数或增加操作编辑能力，属于功能增强需求，需走研发流程。