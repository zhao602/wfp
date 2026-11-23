# SSO 企业身份集成指南

> 本文档说明 SSO 与企业身份提供方（IdP）的集成配置（SAML 2.0 / OIDC）、属性与角色映射、JIT 自动开户、降级行为与本地登录回退。
>
> **experimental（roadmap S2）**：SAML/OIDC 断言验签未实现（适配器为「简化实现」，仅基本校验）；防重放骨架已在（`replay_guard.py`）。企业 SSO 接入前须完成 S2 裁决（验签落地 vs 正式降级），详见 [docs/dual-runtime-boundaries.md](dual-runtime-boundaries.md) §4。

## 1. 概览

SSO 集成（sso）使平台对接企业 IdP，实现统一身份认证与角色映射。支持 SAML 2.0 与 OIDC 两种协议，默认关闭，需显式启用。

### 1.1 治理目标

- **统一身份**：员工使用企业账号登录，无需平台独立账号。
- **角色联动**：IdP 属性映射为平台角色，驱动权限决策。
- **自动开户**：首次登录自动创建账号（JIT provisioning）。
- **安全回退**：IdP 不可达时回退本地登录，保障可用性。

### 1.2 配置项清单（默认值）

```yaml
sso:
  enabled: false
  protocols: [saml2, oidc]          # 启用的协议
  default_protocol: saml2
  idp_metadata_url: ""              # SAML metadata URL
  oidc_discovery_url: ""            # OIDC discovery endpoint
  client_id: ""
  client_secret: ""                 # secret，建议从密钥库读取
  jit_provisioning: true            # 首次登录自动开户
  fallback_local_login: true        # IdP 不可达时回退本地登录
  session_timeout_min: 60
  role_mapping:
    source_attribute: groups
    mapping:
      "platform-admin": admin
      "platform-editor": editor
      "platform-viewer": viewer
  attribute_mapping:
    email: email
    display_name: name
    department: department
  saml:
    entity_id: https://agent.example.com/saml
    acs_url: https://agent.example.com/saml/acs
    name_id_format: email
    want_assertions_signed: true
  oidc:
    scope: [openid, profile, email]
    prompt: login
```

## 2. IdP 配置

### 2.1 SAML 2.0

#### 平台侧配置

```yaml
sso:
  default_protocol: saml2
  idp_metadata_url: https://idp.example.com/metadata.xml
  saml:
    entity_id: https://agent.example.com/saml
    acs_url: https://agent.example.com/saml/acs
    name_id_format: email
    want_assertions_signed: true
```

#### IdP 侧配置要点

- **Entity ID**：填入平台 `saml.entity_id`。
- **ACS URL**：填入平台 `saml.acs_url`。
- **NameID Format**：与 `saml.name_id_format` 一致。
- **签名**：IdP 断言签名，平台侧 `want_assertions_signed: true`。
- **属性声明**：至少输出 `email`、`name`、`groups`。

### 2.2 OIDC

#### 平台侧配置

```yaml
sso:
  default_protocol: oidc
  oidc_discovery_url: https://idp.example.com/.well-known/openid-configuration
  client_id: agent-platform
  client_secret: ${OIDC_CLIENT_SECRET}    # 从环境变量/密钥库
  oidc:
    scope: [openid, profile, email, groups]
    prompt: login
```

#### IdP 侧配置要点

- 注册客户端，回调 URL 填 `https://agent.example.com/oidc/callback`。
- 授权类型：`authorization_code`。
- 令牌刷新：启用 `refresh_token`。
- 声明 `groups` claim 以支持角色映射。

## 3. 属性映射与角色映射

### 3.1 属性映射

将 IdP 返回的属性映射为平台用户字段。

```yaml
sso:
  attribute_mapping:
    email: email              # IdP 属性 → 平台字段
    display_name: name
    department: department
    employee_id: employeeNumber
```

未映射的 IdP 属性将被忽略。必填字段（`email`）缺失时拒绝登录。

### 3.2 角色映射

将 IdP 的组/角色属性映射为平台角色。

```yaml
sso:
  role_mapping:
    source_attribute: groups          # 从哪个属性读取组
    mapping:
      "platform-admin": admin         # IdP 组 → 平台角色
      "platform-editor": editor
      "platform-viewer": viewer
    default_role: viewer              # 未匹配时的默认角色
    unmapped_policy: deny             # deny | default_role
```

| 策略 | 行为 |
| ---- | ---- |
| `deny` | IdP 组未匹配任何映射则拒绝登录 |
| `default_role` | 赋予 `default_role` |

### 3.3 映射校验

登录成功后记录映射结果审计事件：

```json
{
  "event": "sso.login",
  "user": "user@example.com",
  "idp_groups": ["platform-editor", "team-data"],
  "platform_role": "editor",
  "unmapped": ["team-data"]
}
```

## 4. JIT 自动开户

### 4.1 流程

```
用户登录 → IdP 认证成功 → 查询本地账号
                                ├─ 存在 → 更新属性与角色
                                └─ 不存在 → jit_provisioning?
                                            ├─ 是 → 创建账号
                                            └─ 否 → 拒绝登录
```

### 4.2 配置

```yaml
sso:
  jit_provisioning: true
  jit:
    default_status: active           # active | pending
    require_attributes: [email]      # 必须存在的属性
    assign_default_role: true
```

### 4.3 账号更新策略

每次登录同步更新用户属性与角色，保持与 IdP 一致。

| 场景 | 行为 |
| ---- | ---- |
| IdP 属性变更 | 下次登录更新本地属性 |
| IdP 组变更 | 下次登录重新映射角色 |
| IdP 账号禁用 | 登录失败（IdP 拒绝） |

## 5. 降级行为与本地登录回退

### 5.1 降级触发条件

| 条件 | 检测方式 | 降级动作 |
| ---- | -------- | -------- |
| IdP 不可达 | metadata 拉取超时 | 回退本地登录 |
| IdP 响应超时 | 认证请求超时 | 回退本地登录 |
| 断言签名校验失败 | 签名不匹配 | 拒绝登录，不回退 |
| 属性映射失败 | 必填属性缺失 | 拒绝登录 |

### 5.2 本地登录回退

```yaml
sso:
  fallback_local_login: true
  fallback:
    show_banner: true               # 登录页显示回退提示
    allowed_users: []               # 空=所有本地账号；非空=白名单
    require_mfa: true               # 回退时强制 MFA
```

回退时发出 `sso.fallback_local` 审计事件，并建议管理员排查 IdP。

### 5.3 降级矩阵

| 场景 | SSO 用户 | 本地用户 | 影响 |
| ---- | -------- | -------- | ---- |
| IdP 正常 | 正常登录 | 正常登录 | 无 |
| IdP 不可达 + 回退开启 | 无法 SSO，可用本地账号 | 正常登录 | SSO 用户需有本地账号 |
| IdP 不可达 + 回退关闭 | 无法登录 | 正常登录 | SSO 用户被锁出 |
| 断言签名失败 | 拒绝登录 | 正常登录 | 安全优先，不回退 |

## 6. 使用流程

### 6.1 启用 SSO

1. 在 IdP 注册平台为应用（SAML/OIDC）。
2. 填写 `config/phase4.yaml` 的 `sso` 段。
3. 将 `sso.enabled` 设为 `true`。
4. 测试登录，确认属性与角色映射正确。
5. 逐步将用户迁移至 SSO（保留本地账号作为回退）。

### 6.2 切换协议

```yaml
sso:
  default_protocol: oidc    # 切换为 OIDC
```

切换后旧 SAML 会话失效，用户需重新登录。

### 6.3 会话管理

- 会话超时：`session_timeout_min`（默认 60 分钟）。
- 续期：活跃用户自动续期，闲置超时后强制重新认证。
- 登出：调用平台登出，同时通知 IdP 单点登出（SAML SingleLogout / OIDC end_session）。

## 7. 故障排查

| 现象 | 可能原因 | 处置 |
| ---- | -------- | ---- |
| 登录跳转 IdP 失败 | `idp_metadata_url` 错误或不可达 | 核对 URL 与网络连通 |
| 断言校验失败 | 时钟偏移或证书变更 | 同步 NTP，更新 IdP 证书 |
| 角色为空 | `groups` 属性未输出或映射缺失 | 检查 IdP 属性声明与 mapping |
| JIT 开户失败 | 必填属性缺失 | 检查 `require_attributes` 与 IdP 输出 |
| 频繁回退本地 | IdP 不稳定 | 排查 IdP 健康，考虑临时关闭 SSO |

### 7.1 诊断命令

```bash
# 测试 IdP 连通
agentctl sso test-idp --protocol saml2

# 查看最近登录事件
agentctl sso logins --tail 20

# 验证角色映射
agentctl sso validate-mapping --user user@example.com
```

## 8. 最佳实践

- 生产环境 `want_assertions_signed: true`，禁止接受未签名断言。
- `client_secret` 从密钥库读取，不硬编码于配置文件。
- 保留至少一个本地管理员账号，防止 IdP 故障时无法管理。
- 定期审查 `unmapped` 日志，补充角色映射。
- 回退本地登录时强制 MFA，降低凭据风险。
