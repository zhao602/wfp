# 范例一：顺序流水线（sequential_pipeline）

## 适用场景

最基础的工作流形态：按单一链路依次执行三个节点，无分支、无循环。
适合首次验证引擎加载 YAML → 编译 → 调度执行的全链路是否畅通，
也作为后续复杂范例的对照基线。

## 节点结构

```
trigger_start(触发器) → read_data(数据源) → analyze(Agent)
```

| 节点标识 | 节点类型 | 职责 |
| --- | --- | --- |
| trigger_start | trigger | 手动触发，将输入参数 `topic` 注入实例 |
| read_data | data_source | 以只读查询从 `demo_warehouse` 读取数据集 |
| analyze | agent | 调用 `qwen3.8-27b` 分析数据集，经工具选项 `report_builder` 生成报告 |

覆盖节点类型：触发器、数据源、Agent（三类）。

> 迁移说明（merge_tool_into_agent_node DC-E / CHG-23）：原 generate_report
> （tool 节点）已并入 Agent 节点——`report_builder` 并入 `available_tools`，
> 工具入参由 LLM 从共享状态推理；Agent 节点承接上下游连线与最终输出。

## 运行方式

```bash
# 后端已启动的前提下，通过 API 启动实例
curl -X POST http://localhost:8000/api/v1/instances \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"definition_id": "sequential_pipeline", "inputs": {"topic": "季度销售"}}'
```

或直接在代码中加载执行（M1 达成标志）：

```python
from orchestration_platform.workflow.yaml_workflow_parser import parse_yaml_file
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.adapter.langgraph_adapter import LangGraphAdapter

definition = parse_yaml_file("examples/sequential_pipeline/sequential_pipeline.yaml")
compiled = GraphCompiler().compile(definition)
engine = LangGraphAdapter()
engine.load(compiled)
result = engine.run({"topic": "季度销售"})
```

## 依赖变量

| 环境变量 | 用途 |
| --- | --- |
| `LLM_PRIMARY_BASE_URL` / `LLM_PRIMARY_API_KEY` / `LLM_PRIMARY_MODEL` | Agent 节点模型调用 |
| `DATA_SOURCE_CONNECTION_STRING` | 数据源查询取数 |

## 预期结果

- 三个节点按 `e1 → e2` 顺序依次执行成功；
- 共享状态中 `report` 字段为 Agent 节点（含工具调用）输出的报告对象；
- 每个节点产生一条 `NODE_COMPLETED` 事件。
