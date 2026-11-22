# 范例六：数据工程 Agent（data_engineering）

## 适用场景

数据工程全链场景：从 Hologres 查询原始数据，经数据转换操作链完成
清洗与聚合，再由分析 Agent 产出结论，最后统一展示。
验证大数据连接器取数、操作链（operations）编排与 Agent 分析的衔接。

## 节点结构

```
trigger_start(触发器) → read_from_hologres(数据源) → clean_and_aggregate(数据转换)
    → analysis_agent(Agent) → display_result(输出展示)
```

| 节点标识 | 节点类型 | 职责 |
| --- | --- | --- |
| trigger_start | trigger | 接收查询 SQL 与分析目标 |
| read_from_hologres | data_source | 从 Hologres 连接读取近 7 日销售记录 |
| clean_and_aggregate | data_transform | 操作链两步：handle_nulls 剔除订单号/金额缺失记录 → aggregate 按地区与品类汇总销售额 |
| analysis_agent | agent | 基于聚合数据分析趋势、异常、洞察与建议 |
| display_result | output_display | 展示分析结论与聚合数据 |

覆盖节点类型：触发器、数据源、数据转换、Agent、输出展示（五类）。

> 配置口径：
> 1. `operation` 与 `input_dataset_reference` 为数据转换节点必备项，
>    缺失会导致定义校验失败；`operations` 非空时以操作链为准，
>    顶层 `operation` 与链首步保持一致，仅为必备项占位。
> 2. 聚合参数为单函数口径（aggregate_function/aggregate_field），
>    不支持 aggregations 列表；均值与订单数由分析 Agent 基于聚合结果补充。
> 3. 行级表达式（record.amount > 0、dict()/len()/now() 等）在受限求值器下
>    不成立（OP-08），清洗改用字段级 handle_nulls。

## 运行方式

```bash
curl -X POST http://localhost:8000/api/v1/instances \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"definition_id": "data_engineering_agent", "inputs": {"query_sql": "SELECT * FROM sales_records", "analysis_goal": "识别销售趋势"}}'
```

或经范例库服务加载：`ExampleLibraryService().load_example("data_engineering")`。

## 依赖变量

| 环境变量 | 用途 |
| --- | --- |
| `LLM_PRIMARY_BASE_URL` / `LLM_PRIMARY_API_KEY` / `LLM_PRIMARY_MODEL` | 分析 Agent 模型调用 |
| `DATA_SOURCE_CONNECTION_STRING` | 数据源查询取数 |

## 预期结果

- 清洗步骤剔除缺失记录后，聚合按地区与品类产出销售额汇总（`clean_data`）；
- 分析 Agent 输出 trends / anomalies / insights / recommendations 四组结论；
- 输出展示并列呈现分析结论与聚合数据。
