# 工作流定义规范 v1.1

## 版本兼容策略

- v1.0 定义无需修改即可在 v1.1 引擎运行
- v1.1 新增节点类型与配置字段，v1.0 引擎忽略不识别的字段
- 规范版本号通过 `spec_version` 字段声明，值为 `"1.0"` 或 `"1.1"`

## v1.1 新增节点类型

### 子工作流节点（subworkflow）

```yaml
- node_id: call_sub
  name: 调用子工作流
  node_type: subworkflow
  output_name: sub_output
  configuration:
    subworkflow_definition_id: target_workflow
    input_mapping:
      param1: parent_value
    output_mapping:
      result: child_result
    context_strategy: inherit  # inherit | isolate
    max_nesting_depth: 5
```

### 动态注入节点（dynamic_injector）

```yaml
- node_id: injector
  name: 动态注入
  node_type: dynamic_injector
  output_name: injection_result
  configuration:
    source_expression: "items | length > 0"
    template_node_id: template_agent
    max_injection_count: 100
    injection_key_field: item_id
```

### 数据转换节点（data_transform）

```yaml
- node_id: transform
  name: 数据转换
  node_type: data_transform
  output_name: transformed_data
  configuration:
    operations:
      - operation: filter
        params:
          expression: "record.value > 0"
      - operation: aggregate
        params:
          group_by: ["category"]
          aggregations:
            - field: amount
              function: sum
              output_field: total
```

## 节点数量上限

- v1.0：50 个节点
- v1.1：200 个节点

## 新增配置字段

| 字段 | 适用节点 | 说明 |
| --- | --- | --- |
| context_strategy | subworkflow | 上下文传递策略：inherit/isolate |
| max_nesting_depth | subworkflow | 最大嵌套深度 |
| max_injection_count | dynamic_injector | 最大注入数量 |
| injection_key_field | dynamic_injector | 注入幂等键字段 |
| operations | data_transform | 转换操作列表 |
