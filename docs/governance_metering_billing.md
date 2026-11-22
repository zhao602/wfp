# Token 计量与计费配置说明

## 定价规则配置

定价规则按模型与 Token 类型定义单价，单价精度为小数点后 6 位。

```yaml
pricing_rule:
  rule_id: "rule_001"
  model_identifier: "gpt-4"
  prompt_token_price: 0.03      # 输入 Token 单价（元/Token）
  completion_token_price: 0.06  # 输出 Token 单价（元/Token）
  effective_from: "2026-01-01T00:00:00Z"
  effective_to: null             # null 表示长期有效
```

成本 = 输入token数 × 输入单价/1000 + 输出token数 × 输出单价/1000

## 预算配置与熔断策略

预算按租户/工作流/实例维度设定阈值。

### 熔断策略

- `soft_alert`：仅告警不拒绝
- `hard_stop`：硬停止，拒绝新实例启动

### 配置项

- `BUDGET_CIRCUIT_BREAK_LATENCY_SECONDS=5`：熔断检测延迟
- `BUDGET_FAIL_CLOSED=true`：预算超限时拒绝实例启动

## 告警通道配置

- `ALERT_CHANNEL_TYPE=webhook`：告警通道类型（webhook/email/log）
- `ALERT_WEBHOOK_URL=`：告警 Webhook URL

告警通道不可达时进入重试队列，不影响熔断逻辑。
