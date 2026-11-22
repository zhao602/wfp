# 特征工程流水线范例（feature_engineering）

数据科学范例：从上游训练数据构造机器学习特征，演示
「trigger → data_source → data_transform → agent → output_display」编排模式。

## 目录内容

| 文件 | 说明 |
| --- | --- |
| `feature_engineering.yaml` | 流水线编排定义（spec_version 1.1） |
| `feature_engineering_agents.yaml` | 预置 Agent 定义清单（职责/输入输出契约） |
| `README.md` | 本说明文档 |

## 编排模式

```
trigger_start → read_upstream_data → filter_invalid_records → enrich_metadata
             → feature_engineer_agent → display_result
```

1. **trigger**（manual）：接收 `dataset_identifier` 与 `feature_requirements` 输入
2. **data_source**：读取上游训练数据（`training_data_connection`）
3. **data_transform**：
   - `filter_invalid_records`（handle_nulls）：剔除 `user_id` 缺失/为空的记录
     （OP-08 口径：行级过滤表达式经受限求值器求值时变量引用不成立，
     清洗改用字段级操作表达）
   - `enrich_metadata`（sort）：按 `user_id` 整序，同用户记录相邻，
     便于 Agent 按用户维度归纳行为特征
4. **agent**：特征工程 Agent 设计候选特征清单并分类标注
5. **output_display**：展示特征清单结果

> 迁移说明（merge_tool_into_agent_node DC-E / CHG-23）：原产物输出
> （artifact_publisher）与结果汇聚 tool 节点已移除，由输出展示节点承接。

## 运行方式

1. 配置 LLM 主模型三变量（见下）
2. 在 `examples/` 根目录通过范例库服务加载：

```python
from orchestration_platform.application.example_library_service import ExampleLibraryService

service = ExampleLibraryService()
definition = service.load_example("feature_engineering")
```

## 依赖变量

| 环境变量 | 用途 |
| --- | --- |
| `LLM_PRIMARY_BASE_URL` | 主模型服务地址 |
| `LLM_PRIMARY_API_KEY` | 主模型密钥 |
| `LLM_PRIMARY_MODEL` | 主模型标识 |

缺失任一变量时，`list_examples` 将该范例标记为不可用并列出缺失项
（EXAMPLE_DEPENDENCY_MISSING 语义）。

## 异常场景

- **上游数据不可得**（异常场景 1）：`read_upstream_data` 节点按节点失败语义终止，错误信息明确携带 `dataset_identifier` 与缺数原因，不模糊报错。
- **依赖缺失**：LLM 主模型变量缺失时范例标记不可用，不产生半成品加载。
