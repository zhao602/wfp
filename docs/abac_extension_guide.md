# ABAC权限模型扩展指南

## 1. 概述

基于属性的访问控制（ABAC）在既有RBAC基础上提供更细粒度的权限决策。采用**组合决策**模式：RBAC先校验 + ABAC再细化。

**决策流程**：RBAC认证 → RBAC鉴权 → ABAC细化决策

## 2. 核心概念

### 2.1 ABAC策略（AbacPolicy）

```python
AbacPolicy(
    policy_id="policy_1",       # 策略唯一标识
    tenant_id="tenant_1",       # 租户标识
    conditions=[...],           # 条件集合（合取语义）
    effect=PolicyEffect.ALLOW,  # 策略效果：允许/拒绝
    fail_mode=FailMode.DENY,    # 属性求值失败回退
    priority=0,                 # 优先级（越小越高）
    is_active=True,             # 是否启用
)
```

### 2.2 ABAC条件（AbacCondition）

```python
AbacCondition(
    attribute_name="role",              # 属性名称
    attribute_source=AttributeSource.SUBJECT,  # 属性来源
    operator=ConditionOperator.EQ,      # 比较操作符
    expected_value="admin",             # 期望值
)
```

### 2.3 属性来源

| 来源 | 说明 | 可用属性 |
|------|------|---------|
| `SUBJECT` | 主体属性 | user_id, tenant_id, role, department, 自定义元数据 |
| `RESOURCE` | 资源属性 | resource_type, resource_id, operation, sensitivity_level, 自定义载荷 |
| `ENVIRONMENT` | 环境属性 | current_time, current_hour, request_ip, is_weekend, tenant_label |

### 2.4 比较操作符

| 操作符 | 说明 | 示例期望值 |
|--------|------|-----------|
| `EQ` | 等于 | `"admin"` |
| `NEQ` | 不等于 | `"guest"` |
| `IN` | 属于集合 | `"admin,editor,viewer"` |
| `NOT_IN` | 不属于集合 | `"guest,anonymous"` |
| `RANGE` | 数值范围 | `"0,100"` |
| `IP_IN_RANGE` | IP网段 | `"192.168.1.0/24"` |
| `TIME_IN_RANGE` | 时间范围 | `"09:00-18:00"` |

### 2.5 失败模式

| 模式 | 说明 |
|------|------|
| `DENY` | 属性求值失败时拒绝（fail-closed） |
| `ALLOW` | 属性求值失败时跳过该条件（fail-open） |

## 3. 组合决策顺序

```
请求 → RBAC认证 → RBAC鉴权 → ABAC细化 → 最终决策
         ↓拒绝        ↓拒绝        ↓拒绝
        返回拒绝     返回拒绝     返回拒绝
```

- **RBAC拒绝→直接拒绝**（短路，不执行ABAC）
- **RBAC允许→执行ABAC细化**
- **ABAC无策略→返回允许**（保持纯RBAC行为一致）
- **ABAC允许→返回允许**
- **ABAC拒绝→返回拒绝**

## 4. 策略管理API

### 4.1 创建策略

```json
POST /api/v1/governance/abac/policies
{
    "policy_id": "policy_allow_admin",
    "tenant_id": "tenant_1",
    "conditions": [
        {
            "attribute_name": "role",
            "attribute_source": "subject",
            "operator": "eq",
            "expected_value": "admin"
        }
    ],
    "effect": "allow",
    "fail_mode": "deny",
    "priority": 0
}
```

### 4.2 查询策略

```
GET /api/v1/governance/abac/policies?tenant_id=tenant_1
```

### 4.3 删除策略

```
DELETE /api/v1/governance/abac/policies/{policy_id}?tenant_id=tenant_1
```

删除为软删除（`is_active=False`）。

## 5. 策略配置示例

### 5.1 仅允许管理员执行

```python
AbacPolicy(
    policy_id="only_admin_execute",
    tenant_id="tenant_1",
    conditions=[
        AbacCondition(
            attribute_name="role",
            attribute_source=AttributeSource.SUBJECT,
            operator=ConditionOperator.EQ,
            expected_value="admin",
        ),
    ],
    effect=PolicyEffect.ALLOW,
)
```

### 5.2 工作时间允许访问

```python
AbacPolicy(
    policy_id="business_hours_only",
    tenant_id="tenant_1",
    conditions=[
        AbacCondition(
            attribute_name="current_hour",
            attribute_source=AttributeSource.ENVIRONMENT,
            operator=ConditionOperator.RANGE,
            expected_value="9,18",
        ),
    ],
    effect=PolicyEffect.ALLOW,
)
```

### 5.3 IP网段限制

```python
AbacPolicy(
    policy_id="internal_ip_only",
    tenant_id="tenant_1",
    conditions=[
        AbacCondition(
            attribute_name="request_ip",
            attribute_source=AttributeSource.ENVIRONMENT,
            operator=ConditionOperator.IP_IN_RANGE,
            expected_value="10.0.0.0/8",
        ),
    ],
    effect=PolicyEffect.ALLOW,
)
```

### 5.4 拒绝承包商访问高敏感资源

```python
AbacPolicy(
    policy_id="deny_contractor_high_sensitivity",
    tenant_id="tenant_1",
    conditions=[
        AbacCondition(
            attribute_name="role",
            attribute_source=AttributeSource.SUBJECT,
            operator=ConditionOperator.EQ,
            expected_value="contractor",
        ),
        AbacCondition(
            attribute_name="sensitivity_level",
            attribute_source=AttributeSource.RESOURCE,
            operator=ConditionOperator.EQ,
            expected_value="high",
        ),
    ],
    effect=PolicyEffect.DENY,
    priority=1,  # 高优先级
)
```

## 6. 拦截器链集成

ABAC拦截器在拦截器链中的位置：**鉴权 → ABAC → 租户隔离 → 审计 → 配额 → 预算 → 全局调度**

```python
chain = InterceptorChain(
    auth=auth_interceptor,
    abac=abac_interceptor,  # 新增参数，默认None向后兼容
    tenant_isolation=tenant_interceptor,
    ...
)
```

不传 `abac` 参数时，拦截器链行为与既有版本完全一致。

## 7. 降级模式

- `abac_enabled=False` 时，ABAC拦截器不注册
- 无ABAC策略时，`AbacPolicyEngine.decide()` 返回 `is_no_policy=True`，保持RBAC允许结果
- 不影响既有RBAC行为

## 8. 模块结构

```
governance/rbac/abac/
├── __init__.py
├── models.py                    # ABAC领域模型
├── policy_repository_local.py   # 本地策略仓储
├── attribute_evaluator.py       # 属性求值器
├── abac_policy_engine.py        # ABAC策略引擎
├── combined_decision_engine.py  # 组合决策引擎
├── abac_interceptor.py          # ABAC拦截器
└── abstractions/
    ├── policy_repository.py     # IAbacPolicyRepository 接口
    ├── attribute_evaluator.py   # IAttributeEvaluator 接口
    └── abac_policy_engine.py    # IAbacPolicyEngine 接口
```

## 9. 验收对照

| 验收编号 | 内容 | 对应任务 |
|---------|------|---------|
| A81 | ABAC模型与策略存储决策 | T89, T90, T92, T97 |
| A82 | ABAC属性维度 | T91, T97 |
| A83 | ABAC组合决策顺序与动态求值 | T91, T92, T93, T94, T97 |
| A84 | ABAC无策略默认允许 | T92, T93, T97 |
| A85 | ABAC策略管理API与前端 | T95, T96, T97 |
| B86 | ABAC策略格式校验 | T90, T95, T97 |
| B87 | ABAC属性求值失败处理 | T92, T97 |
| B88 | ABAC组合决策RBAC短路 | T93, T94, T97 |
