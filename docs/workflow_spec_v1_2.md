# 工作流定义规范 v1.2

## 版本变更

v1.2 在 v1.1 基础上新增可选治理配置字段，不破坏 v1.0/v1.1 兼容性。

## 新增字段

### 实例治理配置

```yaml
spec_version: "1.2"
# ... 既有字段 ...

# v1.2 新增治理配置（可选）
governance:
  priority: normal          # 实例优先级：low/normal/high/urgent
  budget_id: "budget_001"   # 预算标识
  resource_requirements:     # 资源需求
    - pool_type: concurrency_slot
      requested_amount: 1
```

## 兼容性

- v1.0 / v1.1 定义无需修改即可在 v1.2 引擎运行
- v1.2 仅新增可选字段，缺省时使用默认值
- 引擎同时支持 v1.0 / v1.1 / v1.2 三个规范版本
- 既有引擎遇到未知枚举取值时忽略而非报错
