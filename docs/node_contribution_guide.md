# 节点市场第三方贡献指南

> 本文档说明第三方节点贡献的 7 态状态机、签名要求（HMAC/RSA）、扫描规则、审核流程及驳回处理。

## 1. 概览

节点市场（node_market）受控接入社区贡献的节点，保障供应链安全。所有贡献须经过签名校验、静态扫描与人工审核方可发布。

### 1.1 治理目标

- **来源可验**：贡献者身份与包签名可校验。
- **内容可扫**：自动检测危险代码与可疑依赖。
- **流程可溯**：状态机记录每次状态流转与审核意见。

### 1.2 配置项清单（默认值）

```yaml
node_market:
  enabled: false
  registry_url: https://market.example.com/api
  signature_algo: RSA-SHA256       # RSA-SHA256 | HMAC-SHA256
  signature_required: true
  scan_rules:
    - no_shell_exec
    - no_network_egress
    - no_dynamic_import
    - no_hardcoded_secret
  scan_on_submit: true
  review_required: true
  auto_publish: false
  max_package_size_mb: 50
```

## 2. 贡献状态机（7 态）

贡献包从提交到发布经历 7 个状态，流转由事件驱动。

### 2.1 状态定义

| 状态 | 代号 | 说明 | 可流转至 |
| ---- | ---- | ---- | -------- |
| 已提交 | `submitted` | 贡献者已上传包与签名 | `scanning` |
| 扫描中 | `scanning` | 自动扫描规则执行中 | `scan_passed` / `scan_failed` |
| 扫描通过 | `scan_passed` | 自动扫描全部通过 | `under_review` |
| 审核中 | `under_review` | 人工审核进行中 | `approved` / `rejected` |
| 已批准 | `approved` | 审核通过，可发布 | `published` |
| 已发布 | `published` | 已上架至市场索引 | （终态） |
| 已驳回 | `rejected` | 审核未通过或扫描失败 | `submitted`（修订后重提） |

### 2.2 状态流转图

```
submitted → scanning → scan_passed → under_review → approved → published
                ↓                          ↓
            scan_failed ─────────────→ rejected
```

### 2.3 状态事件

每次流转记录 `event`、`actor`、`timestamp`、`reason`，不可篡改。

```json
{
  "event": "scan_passed",
  "package": "my-node-1.0.0",
  "actor": "system",
  "timestamp": "2026-09-24T08:00:00Z",
  "reason": "all rules passed"
}
```

## 3. 签名要求

### 3.1 签名算法

| 算法 | 适用场景 | 密钥管理 |
| ---- | -------- | -------- |
| RSA-SHA256 | 个人/组织贡献（默认） | 贡献者持有私钥，公钥注册至市场 |
| HMAC-SHA256 | 内部 CI 自动发布 | 共享密钥存于 CI 密钥库 |

### 3.2 签名生成

```bash
# RSA 签名
openssl dgst -sha256 -sign private.pem -out package.sig my-node-1.0.0.ppkg

# HMAC 签名
openssl dgst -sha256 -hmac "$(cat hmac.key)" -out package.sig my-node-1.0.0.ppkg
```

### 3.3 签名校验

- 市场在 `submitted` 阶段校验签名。
- `signature_required: true` 时，无签名或校验失败直接拒绝。
- 公钥须在市场预先注册并与贡献者账号绑定。

### 3.4 公钥注册

```bash
# 贡献者注册公钥
marketctl register-key --key-id my-key-2026 --public-key public.pem
```

## 4. 扫描规则

### 4.1 内置规则

| 规则 | 检测内容 | 严重级别 |
| ---- | -------- | -------- |
| `no_shell_exec` | 调用 shell 命令执行 | high |
| `no_network_egress` | 未声明的外部网络请求 | high |
| `no_dynamic_import` | 运行时动态导入模块 | medium |
| `no_hardcoded_secret` | 硬编码密钥/令牌 | high |
| `no_file_write_outside` | 写入工作目录外路径 | high |

### 4.2 自定义规则

在 `scan_rules` 中追加规则名，规则定义存放于 `./scanners/` 目录。

```yaml
scan_rules:
  - no_shell_exec
  - custom:my_org_no_pii_log   # 组织自定义规则
```

### 4.3 扫描结果

扫描完成后生成报告，含每条规则的 `verdict`（pass/fail）与 `detail`。

```json
{
  "package": "my-node-1.0.0",
  "rules": [
    {"name": "no_shell_exec", "verdict": "pass"},
    {"name": "no_hardcoded_secret", "verdict": "fail", "detail": "line 42: hardcoded token"}
  ],
  "overall": "scan_failed"
}
```

## 5. 审核流程与驳回处理

### 5.1 审核流程

1. 贡献者提交包 + 签名 → `submitted`。
2. 系统自动校验签名，失败则直接拒绝。
3. 进入 `scanning`，执行所有 `scan_rules`。
4. 全部通过 → `scan_passed`；任一失败 → `scan_failed` → `rejected`。
5. `scan_passed` 后进入 `under_review`，由审核人人工复核。
6. 审核人批准 → `approved` → `published`；驳回 → `rejected`。

### 5.2 审核要点

- 功能正确性：核心逻辑是否与描述一致。
- 权限最小化：`permissions` 是否超出必要范围。
- 依赖安全性：第三方依赖是否有已知漏洞。
- 文档完整性：README 与参数说明是否齐全。

### 5.3 驳回处理

驳回时须填写 `reason`，贡献者可据此修订后重新提交。

```bash
# 审核人驳回
marketctl reject --package my-node-1.0.0 --reason "权限声明过大，请收敛 network 范围"
```

贡献者修订后重新提交，状态从 `rejected` 回到 `submitted`，重新走完整流程。历史记录保留。

### 5.4 申诉机制

贡献者对驳回有异议，可发起申诉，由高级审核人复核。

```bash
marketctl appeal --package my-node-1.0.0 --comment "network 范围已收敛至 api.example.com"
```

## 6. 降级行为

| 场景 | 降级策略 |
| ---- | -------- |
| 市场不可达 | 贡献提交排队，本地缓存待重试 |
| 签名校验服务异常 | 暂停提交，已提交包保留状态 |
| 扫描引擎异常 | 包停留在 `scanning`，超时后标记 `scan_failed` |
| 审核人不可用 | `under_review` 超时告警，可指派候补审核人 |

## 7. 故障排查

| 现象 | 可能原因 | 处置 |
| ---- | -------- | ---- |
| 提交被拒"签名无效" | 私钥/公钥不匹配或算法不一致 | 重新生成签名并核对 key-id |
| 卡在 `scanning` | 扫描引擎异常 | 查看扫描服务日志，必要时手动重扫 |
| `scan_failed` 但无 detail | 规则执行异常 | 查看扫描器日志，检查规则定义 |
| 审核超时 | 审核人未处理 | 指派候补或设置超时自动升级 |

### 7.1 诊断命令

```bash
# 查看包状态
marketctl status my-node-1.0.0

# 查看状态历史
marketctl history my-node-1.0.0

# 手动触发重扫
marketctl rescan my-node-1.0.0
```

## 8. 最佳实践

- 贡献者应使用独立 key-id，定期轮换密钥。
- `permissions` 声明遵循最小权限原则。
- 包内附带测试用例，便于审核人复核。
- 驳回 `reason` 应具体可执行，避免模糊表述。
- 市场管理员定期审查 `rejected` 堆积，优化规则与文档。
