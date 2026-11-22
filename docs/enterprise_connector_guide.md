# 企业连接器指南（CRM/ERP/OA）

> 本文档说明 CRM/ERP/OA 企业连接器的配置、凭证管理与热加载、数据范围声明、连接测试与诊断。

## 1. 概览

企业连接器（enterprise_connector）为平台接入结构化业务系统提供标准化适配，支持 CRM、ERP、OA 三类系统。默认关闭，需显式启用。

### 1.1 治理目标

- **结构化集成**：统一适配常见企业系统接口（REST/SOAP/GraphQL）。
- **凭证安全**：集中管理凭证，支持轮换与热加载。
- **数据范围可控**：连接器声明可访问数据范围，防止越权读取。
- **可观测诊断**：连接状态、延迟、错误率可监控。

### 1.2 配置项清单（默认值）

```yaml
enterprise_connector:
  enabled: false
  connectors: [crm, erp, oa]
  credential_rotation_min: 60       # 凭证轮换检查间隔
  hot_reload: true                  # 凭证变更热加载
  timeout_ms: 10000
  retry:
    max_attempts: 3
    backoff_ms: 500
  circuit_breaker:
    enabled: true
    failure_threshold: 5
    reset_ms: 30000
  audit_log: ./logs/connector.audit.jsonl
```

## 2. 连接器配置

### 2.1 CRM 连接器（以 Salesforce 为例）

```yaml
connectors:
  crm:
    - id: salesforce_prod
      type: salesforce
      base_url: https://myorg.my.salesforce.com
      api_version: "58.0"
      auth:
        type: oauth2
        credential_ref: vault:crm/salesforce_prod   # 密钥库引用
      data_scope:
        objects: [Account, Contact, Opportunity]
        fields:
          Account: [Id, Name, Industry, Revenue]
        filter: "Industry IN ('Finance','Tech')"
      rate_limit:
        requests_per_min: 100
```

### 2.2 ERP 连接器（以 SAP 为例）

```yaml
connectors:
  erp:
    - id: sap_prod
      type: sap
      base_url: https://sap.example.com/api
      auth:
        type: basic
        credential_ref: vault:erp/sap_prod
      data_scope:
        modules: [MM, SD, FI]
        entities: [Material, SalesOrder, Invoice]
      timeout_ms: 15000
```

### 2.3 OA 连接器（以钉钉为例）

```yaml
connectors:
  oa:
    - id: dingtalk
      type: dingtalk
      base_url: https://oapi.dingtalk.com
      auth:
        type: app_key
        credential_ref: vault:oa/dingtalk
      data_scope:
        apis: [getuser, getdept, getapproval]
```

### 2.4 通用字段说明

| 字段 | 必填 | 说明 |
| ---- | ---- | ---- |
| `id` | 是 | 连接器实例唯一标识 |
| `type` | 是 | 连接器类型 |
| `base_url` | 是 | 系统接口基址 |
| `auth.type` | 是 | 认证方式（oauth2/basic/api_key/app_key） |
| `auth.credential_ref` | 是 | 凭证在密钥库中的引用 |
| `data_scope` | 是 | 可访问数据范围声明 |
| `rate_limit` | 否 | 限流配置 |

## 3. 凭证管理与热加载

### 3.1 凭证存储

凭证不直接写入配置文件，通过 `credential_ref` 引用密钥库（如 Vault、KMS）。

```yaml
auth:
  credential_ref: vault:crm/salesforce_prod
```

### 3.2 凭证轮换

```yaml
enterprise_connector:
  credential_rotation_min: 60
  rotation:
    auto_reload: true               # 凭证变更后自动热加载
    notify_before_days: 7           # 凭证将过期前告警
    on_failure: degrade             # degrade | retry | fail
```

| 策略 | 行为 |
| ---- | ---- |
| `degrade` | 标记连接器为 `degraded`，相关节点返回空数据 |
| `retry` | 使用旧凭证重试，直至新凭证可用 |
| `fail` | 直接失败，节点报错 |

### 3.3 热加载流程

```
密钥库凭证变更 → 轮换检查器感知 → 拉取新凭证 → 原子替换 → 连接器重建客户端
```

- 热加载为原子操作，不中断进行中的请求。
- 加载失败时保留旧凭证并告警。
- 热加载事件记录审计日志：`connector.credential_reloaded`。

## 4. 数据范围声明

### 4.1 作用

`data_scope` 声明连接器可访问的数据范围，平台据此校验节点请求是否越权。

### 4.2 声明维度

| 维度 | 说明 | 示例 |
| ---- | ---- | ---- |
| `objects` / `entities` / `apis` | 可访问的业务对象 | `[Account, Contact]` |
| `fields` | 每个对象可访问字段 | `Account: [Id, Name]` |
| `filter` | 数据过滤条件 | `Industry IN ('Finance')` |
| `write` | 是否允许写操作 | `true` / `false` |

### 4.3 越权校验

节点通过连接器请求数据时，运行时校验请求是否在 `data_scope` 内：

- 请求的对象未声明 → 拒绝。
- 请求的字段未声明 → 裁剪或拒绝（按配置）。
- 写操作未声明 `write: true` → 拒绝。

违规事件：`connector.scope.violation`。

## 5. 连接测试与诊断

### 5.1 连接测试

```bash
# 测试连接器连通
agentctl connector test crm:salesforce_prod

# 测试数据范围
agentctl connector test-scope crm:salesforce_prod --object Account
```

测试输出包含：连通状态、认证状态、延迟、可访问对象列表。

### 5.2 健康检查

```yaml
enterprise_connector:
  health_check:
    enabled: true
    interval_min: 5
    timeout_ms: 3000
```

健康检查定期探测所有连接器，结果上报监控面板。

### 5.3 诊断指标

| 指标 | 说明 |
| ---- | ---- |
| `connector.up` | 连接器是否可用 |
| `connector.latency_ms` | 最近请求延迟 |
| `connector.error_rate` | 错误率 |
| `connector.circuit_state` | 熔断器状态（closed/open/half_open） |

## 6. 降级行为

### 6.1 降级触发

| 条件 | 降级动作 |
| ---- | -------- |
| 接口超时 | 触发重试，超限后熔断 |
| 凭证失效 | 按 `rotation.on_failure` 策略处理 |
| 熔断器打开 | 连接器标记 `degraded`，节点返回空数据 |
| 数据范围校验失败 | 拒绝请求，记录违规 |

### 6.2 熔断器

```yaml
circuit_breaker:
  enabled: true
  failure_threshold: 5         # 连续失败次数阈值
  reset_ms: 30000              # 熔断后恢复探测间隔
```

- `closed`：正常请求。
- `open`：拒绝所有请求，直接降级。
- `half_open`：放行一个探测请求，成功则恢复。

### 6.3 降级对工作流的影响

连接器降级时，依赖该连接器的节点按工作流错误策略处理（跳过/默认值/失败）。降级事件 `connector.degraded` 触发平台告警。

## 7. 故障排查

| 现象 | 可能原因 | 处置 |
| ---- | -------- | ---- |
| 连接测试失败 | `base_url` 或凭证错误 | 核对配置与密钥库引用 |
| 认证失败 | 凭证过期或权限不足 | 轮换凭证，检查账号权限 |
| 频繁熔断 | 目标系统不稳定或限流 | 调整 `rate_limit` 与熔断阈值 |
| 数据范围拒绝 | 节点请求超出 `data_scope` | 扩展声明或收敛节点请求 |
| 热加载未生效 | 轮换检查器未运行 | 检查 `credential_rotation_min` 与密钥库连通 |

### 7.1 诊断命令

```bash
# 查看所有连接器状态
agentctl connector list --status

# 查看某连接器详情
agentctl connector info crm:salesforce_prod

# 查看熔断器状态
agentctl connector circuit crm:salesforce_prod

# 手动重置熔断器
agentctl connector reset-circuit crm:salesforce_prod
```

## 8. 最佳实践

- 凭证一律通过密钥库引用，禁止明文写入配置。
- `data_scope` 遵循最小范围原则，按需逐步放开。
- 写操作连接器单独配置并加强审计。
- 生产环境启用熔断器与健康检查。
- 凭证轮换前 7 天告警，预留更换窗口。
- 连接器降级应联动工作流通知，避免静默失败。
