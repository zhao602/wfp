# 审计合规日志与导出说明

## 审计日志查询

通过 `/api/v1/governance/audit-logs` 查询审计日志，支持按时间范围、租户、操作类型、操作人过滤。

## 哈希链校验

审计日志使用哈希链保证不可篡改：

```
current_hash = SHA256(previous_hash + log_content)
```

通过 `HashChainVerifier.verify_chain()` 校验完整性，篡改导致链校验失败并报告受影响日志范围。

## 合规导出

通过 `/api/v1/governance/audit-logs/export` 导出审计日志：

- 支持 JSON 与 CSV 两种格式
- 导出文件中敏感字段按脱敏规则掩码处理
- 导出操作本身产生审计日志
- 仅合规审计员与平台管理员可导出
- 导出数据量超限返回 `AUDIT_EXPORT_TOO_LARGE`

## 脱敏规则配置

脱敏规则按字段路径匹配，支持四种掩码方式：

- `full_mask`：完全掩码为 `****`
- `partial_mask`：保留前 N 后 M 位，中间掩码
- `hash`：SHA256 取前 8 位
- `replace`：常量替换

通过 `/api/v1/governance/mask-rules` 管理脱敏规则。
