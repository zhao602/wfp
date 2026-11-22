# 发布说明 — v1.4.0

**发布日期**：2026-09-24  
**版本**：1.4.0  
**代号**：Ecosystem Governance（生态治理）

---

## 1. 版本概述

v1.4.0 是「企业级多Agent工作流编排平台」的第四个里程碑版本，新增 **74 个任务**（T130-T203）的交付物，覆盖 7 项偏差修正：

| 里程碑 | 范围 | 任务编号 |
|--------|------|----------|
| M25 | Agent 执行沙箱 | T130-T143 |
| M26 | 插件 SDK 与节点市场 | T144-T161 |
| M27 | SSO 与企业连接器 | T162-T181 |
| M28 | 评估框架 | T182-T191 |
| M29 | 语义索引发现与收尾 | T192-T203 |

---

## 2. 功能清单

### 2.1 Agent 执行沙箱（M25）

为 Agent 工具调用提供三层隔离与资源限制：

- **逻辑隔离**：同进程内路径/网络/资源三守卫，零外部依赖
- **子进程隔离**：独立进程级资源限制与超时控制
- **容器隔离**：容器级物理隔离（接口预留）
- 违规事件异步发布，支持订阅告警
- 透明包装：现有 Agent 工具调用无需修改即可沙箱化

### 2.2 插件 SDK 与节点市场（M26）

开放第三方开发者贡献自定义节点：

- **插件 SDK**：`PluginNodeBase` 基类、manifest 校验、打包校验、本地调试运行器
- **插件节点加载**：动态加载插件包，注册为 `NodeType.PLUGIN` 节点
- **贡献市场**：提交→签名校验→安全扫描→审核→发布 全流程
  - HMAC-SHA256 签名校验
  - 静态安全扫描（危险导入/网络访问/文件写入检测）
  - 状态机驱动审核流程

### 2.3 SSO 与企业连接器（M27）

企业身份集成与企业系统连接：

- **SSO**：SAML 2.0 + OIDC 双协议支持
  - IdP 配置管理（凭据 AES-GCM 加密存储）
  - 防重放攻击（TTL 缓存）
  - JIT 自动供给（用户/角色自动创建与映射）
  - 降级模式：SSO 不可用时回退本地登录
- **企业连接器**：CRM / ERP / OA 三类系统连接骨架
  - 版本化凭据管理
  - 数据范围守卫（读/写资源粒度控制）
  - 连接测试器

### 2.4 评估框架（M28）

Agent 效能评估与回归检测：

- **场景集管理**：可重用评估场景集
- **轨迹评分**：
  - 规则评分：基于执行轨迹的结构化规则
  - LLM 评分：大模型语义评分（可配置开关）
- **CLEAR 四维评分**：
  - **C**orrectness（正确性）
  - **L**atency（延迟）
  - **E**fficiency（效率）
  - **A**rchitecture（架构质量）
  - **R**obustness（鲁棒性）
- **基线与回归**：基线存储 + 回归报告 + 退化阈值告警

### 2.5 语义索引 Agent 发现（M29）

基于语义索引自动发现可用 Agent：

- **关键词倒排索引**：BM25 评分
- **向量索引**：可插拔后端（本地内存 / 外部向量数据库）
- **混合检索**：RRF（Reciprocal Rank Fusion）融合排序
- **发现服务**：统一查询入口，支持自然语言意图描述

### 2.6 前端管理后台

新增 `AdminConsole` 管理后台入口，聚合 phase3/phase4 全部 12 个治理与管理面板：

| 分组 | 面板 |
|------|------|
| 画布与编排 | 治理总览、NL 自然语言编排 |
| 安全与合规 | ABAC 属性策略、墓碑与降级管理、Agent 执行沙箱、SSO 企业身份、企业连接器 |
| 生态与扩展 | 插件节点管理、贡献提交向导、贡献审核台 |
| 评估与发现 | Agent 效能评估、语义索引 Agent 发现 |

---

## 3. 技术架构

### 3.1 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.10+ / FastAPI / LangGraph（经抽象隔离层封装） |
| 前端 | React 18 + TypeScript |
| 数据库 | PostgreSQL（可选，降级为内存实现） |
| 缓存 | Redis（可选，降级为内存实现） |
| 消息 | Kafka（可选，降级为内存实现） |
| 声明 | YAML 声明式工作流定义 |

### 3.2 架构原则

- **LangGraph 隔离**：业务代码禁止 `import langgraph`，经抽象层封装
- **降级模式**：所有可选依赖未配置时自动降级为内存/本地实现
- **条件注册**：治理路由按功能开关条件注册，未启用时不加载
- **Protocol 抽象**：所有抽象接口使用 `typing.Protocol` + `@runtime_checkable`
- **枚举扩展**：新增枚举成员以追加方式扩展，不修改既有取值

### 3.3 配置开关

| 开关 | 默认 | 说明 |
|------|------|------|
| `SANDBOX_ENABLED` | false | Agent 执行沙箱 |
| `PLUGIN_SDK_ENABLED` | false | 插件节点 SDK |
| `NODE_CONTRIBUTION_ENABLED` | false | 节点市场第三方贡献 |
| `SSO_ENABLED` | false | SSO 企业身份集成 |
| `ENTERPRISE_CONNECTOR_ENABLED` | false | 企业连接器 |
| `EVALUATION_ENABLED` | false | 评估框架 |
| `AGENT_DISCOVERY_ENABLED` | false | 语义索引 Agent 发现 |

---

## 4. 回归验证

### 4.1 Python 后端

| 检查项 | 结果 |
|--------|------|
| 语法编译（357 个文件） | ✅ 全部通过 |
| 导入路径一致性（357 个文件） | ✅ 0 个问题 |
| 裸 except | ✅ 0 个 |
| print 语句（业务代码） | ✅ 0 个（仅 CLI 工具） |

### 4.2 配置与枚举

| 检查项 | 结果 |
|--------|------|
| 配置开关完整性（7 个） | ✅ 全部存在 |
| .env.example 占位符（45 个） | ✅ 全部存在 |
| 新增枚举完整性（10 个） | ✅ 全部存在 |
| 接口签名兼容性（4 项） | ✅ 全部兼容 |

### 4.3 前端

| 检查项 | 结果 |
|--------|------|
| 路由注册完整性（19 个路由） | ✅ 全部注册 |
| 前端面板聚合（12 个面板） | ✅ 已聚合到 AdminConsole |

### 4.4 测试

| 检查项 | 结果 |
|--------|------|
| phase4 测试文件（11 个） | ✅ 全部存在 |
| 全项目测试文件（45+ 个） | ✅ 全部存在 |

---

## 5. 升级指南

### 5.1 从 v1.3.5 升级

1. 拉取最新代码
2. 更新依赖：`pip install -r requirements.txt`
3. 复制 `.env.example` 中的新配置项到 `.env`
4. 按需启用功能开关（默认全部关闭，零影响升级）
5. 重启服务

### 5.2 启用新功能

```bash
# 启用沙箱
SANDBOX_ENABLED=true

# 启用插件 SDK
PLUGIN_SDK_ENABLED=true

# 启用 SSO
SSO_ENABLED=true
SSO_SESSION_TTL_SECONDS=3600

# 启用评估框架
EVALUATION_ENABLED=true

# 启用 Agent 发现
AGENT_DISCOVERY_ENABLED=true
```

---

## 6. 已知限制

- **测试运行**：沙箱环境无法安装 pytest，测试文件作为交付物编写
- **重依赖降级**：asyncpg/redis/aiokafka/httpx 未安装时降级为内存/本地实现
- **企业连接器**：CRM/ERP/OA 为骨架实现，需按实际系统适配
- **容器隔离**：容器隔离运行时为接口预留，需对接实际容器运行时
- **向量索引**：外部向量后端为接口预留，需对接实际向量数据库
- **LLM 评分**：评估框架的 LLM 评分需配置 LLM API 密钥

---

## 7. 文档索引

| 文档 | 路径 |
|------|------|
| 更新日志 | `CHANGELOG.md` |
| 沙箱指南 | `docs/sandbox_guide.md` |
| 插件 SDK 开发指南 | `docs/plugin_sdk_development_guide.md` |
| 节点贡献指南 | `docs/node_contribution_guide.md` |
| SSO 集成指南 | `docs/sso_integration_guide.md` |
| 企业连接器指南 | `docs/enterprise_connector_guide.md` |
| 评估框架指南 | `docs/evaluation_framework_guide.md` |
| Agent 发现指南 | `docs/agent_discovery_guide.md` |
| 阶段总结 | `docs/phase4_ecosystem_summary.md` |
