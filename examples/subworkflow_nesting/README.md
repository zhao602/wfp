# 范例九：子工作流嵌套（subworkflow_nesting）

## 适用场景

最小子工作流复用场景：主工作流经一次子工作流调用复用既有
顺序流水线（sequential_pipeline）的完整能力，并将子工作流报告
映射回父状态统一展示。
验证子工作流输入/输出映射与 ISOLATE 上下文隔离策略。

## 节点结构

```
trigger_start(触发器) → call_subworkflow(子工作流) → display_result(输出展示)
```

| 节点标识 | 节点类型 | 职责 |
| --- | --- | --- |
| trigger_start | trigger | 手动触发，注入分析主题 |
| call_subworkflow | subworkflow | 调用 sequential_pipeline：topic 经 input_mapping 传入，子工作流 report 输出映射为 sub_report |
| display_result | output_display | 展示子工作流回传的分析报告 |

覆盖节点类型：触发器、子工作流、输出展示（三类）。

> 解析前提：被调用的 sequential_pipeline 须已登记到平台定义库，
> 运行期按 subworkflow_definition_id 取最新版本；范例库仅加载主定义文本。
> 上下文策略：ISOLATE——子工作流在隔离状态中执行，仅经
> input_mapping / output_mapping 与父状态交换数据。

## 运行方式

```bash
curl -X POST http://localhost:8000/api/v1/instances \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"definition_id": "subworkflow_nesting", "inputs": {"topic": "季度销售"}}'
```

或经范例库服务加载：`ExampleLibraryService().load_example("subworkflow_nesting")`。

## 依赖变量

| 环境变量 | 用途 |
| --- | --- |
| `LLM_PRIMARY_BASE_URL` / `LLM_PRIMARY_API_KEY` / `LLM_PRIMARY_MODEL` | 传递依赖：被嵌套的 sequential_pipeline 中 Agent 节点需要 |
| `DATA_SOURCE_CONNECTION_STRING` | 传递依赖：sequential_pipeline 数据源节点需要 |

## 预期结果

- 子工作流完整执行（读取数据集 → Agent 分析产出报告）；
- 子工作流 `report` 输出经映射写入父状态 `sub_report`，输出展示非空。
