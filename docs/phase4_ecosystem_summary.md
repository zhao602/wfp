# Phase 4 生态治理里程碑总览

> 本文档为企业多 Agent 工作流编排平台第四阶段（生态治理）的总体说明，涵盖 7 项偏差治理能力、与构建计划的对应关系、降级矩阵摘要以及配置项清单（含默认值）。

## 1. 概览

Phase 4 聚焦"生态治理"，目标是在多 Agent 协同、插件化扩展、企业集成、质量评估与能力发现五个维度建立可观测、可降级、可审计的治理基线。本阶段共交付 **7 项偏差治理能力**，每项能力均遵循"默认安全、显式开启、优雅降级"三原则。

### 1.1 七项能力清单

| 编号 | 能力名称 | 能力代号 | 主要治理目标 | 默认状态 |
| ---- | -------- | -------- | ------------ | -------- |
| C1 | Agent 执行沙箱 | `sandbox` | 限制 Agent 执行边界，防止越权访问 | 关闭（需显式启用） |
| C2 | 插件/节点 SDK | `plugin_sdk` | 标准化第三方扩展开发契约 | 关闭（需显式启用） |
| C3 | 节点市场第三方贡献 | `node_market` | 受控接入社区节点，保障供应链安全 | 关闭（需显式启用） |
| C4 | SSO 企业身份集成 | `sso` | 对接企业 IdP，统一身份与角色 | 关闭（需显式启用） |
| C5 | CRM/ERP/OA 企业连接器 | `enterprise_connector` | 结构化业务系统集成 | 关闭（需显式启用） |
| C6 | Agent 效率评估框架 | `evaluation` | 量化 Agent 能力，支撑回归与选型 | 关闭（需显式启用） |
| C7 | 语义索引 Agent 发现 | `agent_discovery` | 基于语义检索复用已有 Agent | 关闭（需显式启用） |

### 1.2 与构建计划的对应关系

| 构建计划条目 | 对应能力 | 交付物 | 验收标准 |
| ------------ | -------- | ------ | -------- |
| BP-4.1 执行隔离 | C1 | sandbox runtime + 三类守卫 | 越权访问 100% 拦截 |
| BP-4.2 扩展契约 | C2 | SDK base class + manifest 规范 | 示例插件可打包安装 |
| BP-4.3 供应链治理 | C3 | 贡献状态机 + 签名 + 扫描 | 7 态状态机全覆盖 |
| BP-4.4 身份联邦 | C4 | SAML/OIDC 适配器 + JIT | IdP 断言可映射角色 |
| BP-4.5 业务集成 | C5 | CRM/ERP/OA 适配器 + 热加载 | 凭证轮换零停机 |
| BP-4.6 质量基线 | C6 | 场景集 + CLEAR 指标 | 回归可复现（seed 固定） |
| BP-4.7 能力发现 | C7 | 语义索引 + 向量引擎 | 召回 Top3 命中率 ≥ 0.8 |

## 2. 降级矩阵摘要

降级矩阵描述当某项能力不可用时，平台的回退策略与影响范围。所有降级均自动触发并记录审计日志。

| 能力 | 触发降级条件 | 降级策略 | 影响范围 | 审计事件 |
| ---- | ------------ | -------- | -------- | -------- |
| C1 sandbox | 守卫模块加载失败 | 回退至"仅工作目录隔离" | 路径/网络守卫失效 | `sandbox.degraded` |
| C2 plugin_sdk | SDK 版本不兼容 | 拒绝加载并提示升级 | 该插件不可用 | `plugin.sdk_mismatch` |
| C3 node_market | 签名校验失败 | 拒绝安装，保留本地缓存 | 该节点不可安装 | `market.signature_invalid` |
| C4 sso | IdP 不可达 | 回退本地账号登录 | SSO 用户无法登录 | `sso.fallback_local` |
| C5 enterprise_connector | 凭证失效/接口超时 | 标记连接器为 `degraded` | 相关节点返回空数据 | `connector.degraded` |
| C6 evaluation | 场景集为空 | 跳过回归，仅记录告警 | 无回归报告 | `evaluation.skipped` |
| C7 agent_discovery | 向量引擎不可用 | 回退关键词检索 | 召回精度下降 | `discovery.fallback_keyword` |

> 原则：降级不应导致平台整体不可用；任何降级必须在 30 秒内完成切换并发出告警。

## 3. 配置项清单（默认值）

所有配置项由 `src/orchestration_platform/configuration/settings.py` 驱动（pydantic-settings：各字段可经同名环境变量大写形式或 `.env` 文件覆盖）。七项能力开关的实测默认值如下（全量平台开关矩阵见 [config.md](./config.md)）：

| 开关项 | 环境变量 | 默认值 |
| ------ | -------- | ------ |
| `sandbox_enabled` | `SANDBOX_ENABLED` | `false` |
| `plugin_sdk_enabled` | `PLUGIN_SDK_ENABLED` | `false` |
| `node_contribution_enabled` | `NODE_CONTRIBUTION_ENABLED` | `false` |
| `sso_enabled` | `SSO_ENABLED` | `false` |
| `enterprise_connector_enabled` | `ENTERPRISE_CONNECTOR_ENABLED` | `false` |
| `evaluation_enabled` | `EVALUATION_ENABLED` | `false` |
| `agent_discovery_enabled` | `AGENT_DISCOVERY_ENABLED` | `false` |

各能力的配套可调项（超时、限额、存储根目录等）同样位于 `settings.py`，按能力分组注释，完整映射见 [config.md](./config.md)。

## 4. 能力间依赖关系

```
C1 sandbox ──┐
             ├─→ C2 plugin_sdk ──→ C3 node_market
C6 evaluation ┘                        │
                                       ↓
C4 sso ──→ C5 enterprise_connector ←── C7 agent_discovery
```

- C1、C6 为 C2 的前置依赖：插件必须在沙箱内执行，并接受评估。
- C3 依赖 C2：贡献节点必须符合 SDK 契约。
- C5 依赖 C4：企业连接器调用需经 SSO 鉴权。
- C7 可独立运行，但建议与 C6 联动以提升发现精度。

## 5. 启用顺序建议

1. 先启用 C1（sandbox）与 C2（plugin_sdk），建立执行与扩展基线。
2. 启用 C6（evaluation）并导入最小场景集，建立质量基线。
3. 按需启用 C4（sso）→ C5（enterprise_connector）。
4. 按需启用 C7（agent_discovery）。
5. 最后开放 C3（node_market），并配置签名与扫描规则。

## 6. 验收里程碑

| 里程碑 | 交付证据 | 负责模块 |
| ------ | -------- | -------- |
| M4.1 | 沙箱越权拦截率报告 | sandbox |
| M4.2 | 示例插件打包安装日志 | plugin_sdk |
| M4.3 | 贡献状态机测试报告 | node_market |
| M4.4 | SSO 登录成功用例 | sso |
| M4.5 | 连接器热加载演示 | enterprise_connector |
| M4.6 | 回归评估可复现报告 | evaluation |
| M4.7 | 语义召回命中率报告 | agent_discovery |

## 7. 相关文档索引

| 主题 | 文档 |
| ---- | ---- |
| Agent 执行沙箱 | [sandbox_guide.md](./sandbox_guide.md) |
| 插件 SDK 开发 | [plugin_sdk_development_guide.md](./plugin_sdk_development_guide.md) |
| 节点贡献 | [node_contribution_guide.md](./node_contribution_guide.md) |
| SSO 集成 | [sso_integration_guide.md](./sso_integration_guide.md) |
| 企业连接器 | [enterprise_connector_guide.md](./enterprise_connector_guide.md) |
| 评估框架 | [evaluation_framework_guide.md](./evaluation_framework_guide.md) |
| Agent 发现 | [agent_discovery_guide.md](./agent_discovery_guide.md) |

## 8. 故障排查入口

- 沙箱拦截异常：参见 `sandbox_guide.md` 第 6 节。
- 插件加载失败：参见 `plugin_sdk_development_guide.md` 第 6 节。
- 贡献审核驳回：参见 `node_contribution_guide.md` 第 5 节。
- SSO 登录回退：参见 `sso_integration_guide.md` 第 5 节。
- 连接器降级：参见 `enterprise_connector_guide.md` 第 6 节。
- 评估不可复现：参见 `evaluation_framework_guide.md` 第 6 节。
- 发现召回偏低：参见 `agent_discovery_guide.md` 第 5 节。
