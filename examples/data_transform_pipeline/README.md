# 范例七：数据转换流水线（data_transform_pipeline）

## 适用场景

纯数据转换场景（无 Agent 节点）：从数据仓库读取数据集，
经两步数据转换完成缺失清洗与分类聚合，结果直接展示。
验证数据转换节点在无 LLM 依赖下的独立编排能力。

## 节点结构

```
trigger_start(触发器) → read_data(数据源) → filter_data(清洗缺失指标)
    → aggregate_data(聚合统计) → display_result(输出展示)
```

| 节点标识 | 节点类型 | 职责 |
| --- | --- | --- |
| trigger_start | trigger | 手动触发（无输入参数） |
| read_data | data_source | 从 `demo_warehouse` 读取示例表 |
| filter_data | data_transform | handle_nulls：剔除 category/value 缺失的记录 |
| aggregate_data | data_transform | aggregate：按 category 分组对 value 求和 |
| display_result | output_display | 展示分类聚合结果 |

覆盖节点类型：触发器、数据源、数据转换、输出展示（四类）。

> 表达式口径（OP-08）：filter 的行级条件（如 value >= min_value）在受限
> 求值器下变量引用必失败，会把数据集剔空；阈值比较暂不可表达，
> 故清洗步骤改用字段级 handle_nulls，阈值类过滤待求值器支持后再恢复。

## 运行方式

```bash
curl -X POST http://localhost:8000/api/v1/instances \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"definition_id": "data_transform_pipeline", "inputs": {}}'
```

或经范例库服务加载：`ExampleLibraryService().load_example("data_transform_pipeline")`。

## 依赖变量

| 环境变量 | 用途 |
| --- | --- |
| `DATA_SOURCE_CONNECTION_STRING` | 数据源查询取数（无 LLM 依赖——不含 Agent 节点） |

## 预期结果

- 清洗剔除 category/value 缺失记录，避免聚合出现空分组与空求和；
- 聚合按 category 分组求和，`aggregate_result` 字段非空并进入输出展示。
