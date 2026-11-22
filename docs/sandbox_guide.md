# Agent 执行沙箱指南

> 本文档说明 Agent 执行沙箱的策略配置、三类守卫、隔离模式选择与降级、以及违规排查流程。

## 1. 概览

Agent 执行沙箱（sandbox）为每个 Agent 调用提供受限运行环境，防止越权访问文件系统、网络与系统资源。沙箱是 Phase 4 的安全基线，默认以宽松模式启用。

### 1.1 设计目标

- **边界隔离**：限制 Agent 可访问的工作目录、网络与资源配额。
- **违规可观测**：所有越权尝试被拦截并记录审计日志。
- **优雅降级**：守卫模块失效时回退至最小隔离，不中断平台。

### 1.2 架构

```
Agent 调用 → 调度器 → 沙箱运行时
                       ├─ 路径守卫 (PathGuard)
                       ├─ 网络守卫 (NetworkGuard)
                       └─ 资源守卫 (ResourceGuard)
```

## 2. 策略配置

### 2.1 配置项清单（默认值）

```yaml
sandbox:
  enabled: true
  mode: permissive              # strict | permissive | disabled
  workdir: ./runtime/sandbox    # 沙箱工作目录
  timeout_ms: 30000             # 单次执行超时
  path_whitelist:               # 允许访问的路径
    - ./workspace
    - ./tmp
  path_blacklist:               # 禁止访问的路径（优先级高于白名单）
    - /etc
    - ~/.ssh
  network_whitelist:            # 允许访问的网络地址
    - 127.0.0.1
  network_blacklist: []
  cpu_limit: "1.0"              # CPU 核数上限
  memory_limit_mb: 512          # 内存上限
  disk_limit_mb: 1024           # 磁盘写入上限
  env_sanitize: true            # 清理敏感环境变量
  audit_log: ./logs/sandbox.audit.jsonl
```

### 2.2 模式说明

| 模式 | 行为 | 适用场景 |
| ---- | ---- | -------- |
| `strict` | 三类守卫全部启用，违规即终止 | 生产环境、不可信 Agent |
| `permissive` | 守卫启用，违规仅告警不终止 | 开发环境、可信 Agent |
| `disabled` | 仅工作目录隔离，关闭守卫 | 调试场景，禁止生产使用 |

## 3. 三类守卫

### 3.1 路径守卫（PathGuard）

- **职责**：拦截工作目录外的文件读写。
- **策略**：先匹配黑名单，再匹配白名单；均未命中则拒绝。
- **实现**：基于路径规范化（防 `../` 穿越）后的前缀匹配。
- **违规事件**：`sandbox.path.violation`，含目标路径与操作类型。

### 3.2 网络守卫（NetworkGuard）

- **职责**：拦截白名单外的网络出站请求。
- **策略**：按 IP/CIDR 匹配；域名先解析再匹配。
- **实现**：hook socket 连接层，连接前校验目标地址。
- **违规事件**：`sandbox.network.violation`，含目标地址与端口。

### 3.3 资源守卫（ResourceGuard）

- **职责**：限制 CPU、内存、磁盘、执行时长。
- **策略**：软限制（告警）+ 硬限制（终止）。
- **实现**：基于 cgroup（Linux）/ Job Object（Windows）。
- **违规事件**：`sandbox.resource.violation`，含超限维度与实际值。

## 4. 隔离模式选择与降级

### 4.1 模式选择决策

```
是否运行不可信 Agent？
├─ 是 → strict
└─ 否 → 是否生产环境？
        ├─ 是 → strict 或 permissive
        └─ 否 → permissive
```

### 4.2 降级链

| 降级层级 | 触发条件 | 生效策略 | 可用守卫 |
| -------- | -------- | -------- | -------- |
| L0 完整 | 无 | 三类守卫全启用 | 路径+网络+资源 |
| L1 资源降级 | cgroup 不可用 | 资源守卫关闭，仅软告警 | 路径+网络 |
| L2 网络降级 | 网络 hook 加载失败 | 网络守卫关闭 | 路径 |
| L3 最小隔离 | 守卫模块全部失败 | 仅工作目录隔离 | 无 |
| L4 关闭 | `enabled: false` | 无隔离 | 无 |

> 降级至 L3/L4 时，平台发出 `sandbox.degraded` 告警，建议立即排查并恢复。

### 4.3 降级行为配置

```yaml
sandbox:
  degradation:
    auto_fallback: true         # 自动降级
    alert_on_degrade: true      # 降级时告警
    min_level: L2               # 最低允许降级层级，低于则拒绝执行
```

## 5. 使用流程

### 5.1 为 Agent 配置沙箱策略

```yaml
agents:
  - id: data_analyst
    sandbox:
      mode: strict
      workdir: ./workspace/analyst
      network_whitelist:
        - 10.0.0.0/8
      memory_limit_mb: 1024
```

### 5.2 运行时观测

- 审计日志路径：`./logs/sandbox.audit.jsonl`
- 每条记录包含：`timestamp`、`agent_id`、`guard`、`action`、`target`、`verdict`。
- 可通过平台审计面板按 `agent_id` 过滤查看。

### 5.3 临时放宽（需审批）

在调试场景下，可临时为某 Agent 放宽策略，需记录审批单号：

```yaml
sandbox:
  override:
    agent_id: data_analyst
    ticket: OPS-2026-0412
    expires_in_min: 30
    allow_network: ["0.0.0.0/0"]
```

## 6. 故障排查

### 6.1 常见问题

| 现象 | 可能原因 | 处置 |
| ---- | -------- | ---- |
| Agent 执行被终止 | 触发资源硬限制 | 调高对应配额或优化 Agent |
| 文件读写被拒 | 路径不在白名单 | 将路径加入 `path_whitelist` |
| 网络请求被拒 | 目标不在白名单 | 加入 `network_whitelist` |
| 降级告警频繁 | 守卫模块加载失败 | 检查 cgroup/hook 依赖 |
| 审计日志缺失 | 日志路径不可写 | 检查 `audit_log` 路径权限 |

### 6.2 诊断命令

```bash
# 查看沙箱状态
agentctl sandbox status

# 查看某 Agent 的违规记录
agentctl sandbox violations --agent data_analyst

# 验证策略配置
agentctl sandbox validate --config config/phase4.yaml
```

### 6.3 违规事件示例

```json
{
  "timestamp": "2026-09-24T10:12:33Z",
  "agent_id": "data_analyst",
  "guard": "network",
  "action": "connect",
  "target": "203.0.113.5:443",
  "verdict": "denied",
  "mode": "strict",
  "level": "L0"
}
```

## 7. 最佳实践

- 生产环境统一使用 `strict` 模式。
- `path_blacklist` 始终包含敏感目录（`/etc`、`~/.ssh`、`~/.aws`）。
- 网络白名单优先使用 CIDR，避免域名解析绕过。
- 定期审查审计日志，关注 `denied` 事件聚集的 Agent。
- 降级至 L3 以下应触发告警并暂停相关 Agent 调度。
