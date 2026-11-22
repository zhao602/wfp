# 范例十：实时数据订阅触发（realtime_subscription）

## 适用场景

事件驱动编排场景：Kafka 数据流事件（订单事件）触发工作流，
事件分类 Agent 判定类别与优先级，条件分支按优先级分发至
紧急/常规处理 Agent，结果统一展示。
验证 data_event 触发器与实时订阅事件驱动的工作流执行。

## 节点结构

```
trigger_on_event(数据事件触发) → classify_event(事件分类 Agent) → route_by_category(条件分支)
    ├─→ process_high_priority(高优先级处理 Agent) ─┐
    └─→ process_normal(常规处理 Agent) ────────────┴→ display_result(输出展示)
```

| 节点标识 | 节点类型 | 职责 |
| --- | --- | --- |
| trigger_on_event | trigger | data_event 触发器，事件源 `kafka:order_events_topic`，事件数据经输入模板注入 |
| classify_event | agent | 事件分类：输出 category / priority（high/normal/low）/ requires_action |
| route_by_category | control_flow | 按 priority 分发：high → 高优先级处理，normal → 常规处理，其余走默认分支 |
| process_high_priority | agent | 紧急响应处理，输出 action_taken / result / alert_sent |
| process_normal | agent | 标准响应处理，输出 action_taken / result |
| display_result | output_display | 展示事件分类与处理结果 |

覆盖节点类型：触发器、Agent、控制流（条件分支）、输出展示（四类）。

> 路由口径：priority 取值约束为 high / normal / low 三值，路由分支按
> high / normal 分发，未覆盖的取值（如 low）由默认分支进入常规处理。

## 核心验证点

1. **事件驱动触发**：订阅投递的事件数据经输入模板注入实例输入。
2. **分类路由**：同一实例中高优先级与常规处理 Agent 有且仅有一个被执行。
3. **默认分支**：priority 未被条件覆盖时进入常规处理，不因"无匹配分支"终止。

## 运行方式

范例定义本身经范例库服务加载：
`ExampleLibraryService().load_example("realtime_subscription")`。

事件触发链路需先创建订阅（`POST /api/v1/subscriptions`），将目标事件源
的事件投递至本工作流；Kafka 不可用时经内存降级通道同语义投递。

## 依赖变量

| 环境变量 | 用途 |
| --- | --- |
| `LLM_PRIMARY_BASE_URL` / `LLM_PRIMARY_API_KEY` / `LLM_PRIMARY_MODEL` | 三个 Agent 节点模型调用 |

## 预期结果

- 事件分类 Agent 输出合法 priority 取值，路由仅激活一条处理分支；
- 被激活分支的命名输出（`high_priority_result` 或 `normal_result`）非空，
  与分类结果一并进入输出展示。
