# 更新日志

本文件记录「企业级多Agent工作流编排平台」全部版本的变更历史。 遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式。

---

## [0.2.0] - 2026-11-22

> **版本编号说明**：自 B 网格批次线（2026-11 平台重构）起，版本号以 `pyproject.toml`
> 的 0.x 线为准；本段之下的 1.x 历史为早期单体阶段存档，与当前版本线不连续。

### 对外基线（roadmap-v3 B8a，N1/N2/N6）

- **许可证（N1）**：全仓采用 Apache-2.0——`LICENSE` 标准全文落库；根 / SDK
  `pyproject.toml` license 声明与 classifier。
- **仓库基线（N2）**：首次提交落 `main` 分支；annotated tag `v0.2.0`；版本三处升位
  （根 / SDK / web 均 `0.2.0`）；发布与推送 runbook（`docs/repo-release.md`，
  remote 凭据后补执行）。
- **README（N6）**：嵌入视角改写——SDK 30 行示例首屏；修正三处口径漂移
  （「单游标调度」→ 单游标确定性调度 + 并行扇出区域；spec_version「v1.0/1.1/1.2」
  → v1.0/1.1；范例「15 个 YAML」→ 16 个）；SSO experimental 标注；
  LangGraph 双运行时严格子集声明；差异化与文档索引链接。

### 平台能力收口（B0–B7 批次线摘要）

- **B0–B1 基线与存储治理**：全量测试门禁体系（精确闭包 exit 0 口径）；
  PostgreSQL 仓储形态（asyncpg，`[postgres]` extra 显式安装，缺失报错不静默降级）
  与 local_file 零依赖形态并存。
- **B2 嵌入 SDK**：`wfp-sdk` 同步客户端——9 方法面、SSE 事件订阅、同构错误家族、
  零平台内部依赖（隔离 venv 可独立安装）。
- **B3 双运行时**：LangGraph 适配层——单源 node 语义、`langgraph_runtime_enabled`
  开关（默认关闭）、13 例等价矩阵与严格子集防线（`_validate_subset`）。
- **B4/B6 扇出与 HITL**：并行区域 `wait_all / wait_any / n_of_m`、失败游标恢复、
  严格区域语义、SSE 前沿上报（`examples/parallel_execution/parallel_fanout.yaml`）；
  HITL 人工审批挂起/恢复；执行使用量上报。
- **B5 OEM 白标**：品牌注入（`scripts/build_oem.py` + `oem/brands/`）与品牌 API 面。
- **B7 收割批**：usage 汇总端点、节点级 token 记账、tenant_id 口径统一、
  调度器租户传播、事件订阅持久化；版本治理文档（`docs/spec-versioning.md`：
  HC-02 兼容承诺 + 1.x 只增不删三承诺）。

### 门禁口径（v0.2.0 实测）

- 后端 pytest：**3468 collected（3465 passed + 3 skipped），exit 0**。
- 前端 typecheck：13 基线错误（历史存量，口径为零新增）；
  vitest：**1007 passed + 13 skipped**。

---

## [1.4.0] - 2026-09-24

### 新增

#### M25 Agent 执行沙箱（T130-T143）

- 沙箱策略模型与枚举（`IsolationMode`、`NetworkPolicyMode`、`ViolationType`、`SandboxErrorCode`）
- 三守卫机制：路径守卫（`PathGuard`）、网络过滤（`NetworkFilter`）、资源计量（`ResourceMeter`）
- 逻辑隔离运行时（`LogicalIsolationRuntime`）——同进程隔离，零外部依赖
- 子进程执行器（`SubprocessExecutor`）——进程级隔离
- 容器隔离运行时（`ContainerIsolationRuntime`）——容器级隔离（接口预留）
- 违规发布器（`ViolationPublisher`）——异步发布沙箱违规事件
- 沙箱化工具调用器（`SandboxedToolInvoker`）——透明包装 Agent 工具调用
- REST API（`/api/v1/sandbox/policies`、`/api/v1/sandbox/sessions`）
- 前端面板（`SandboxPolicyPanel`）

#### M26 插件 SDK 与节点市场（T144-T161）

- 插件 SDK 基础设施：`PluginNodeBase` 基类、`PluginManifest` 模型、manifest 校验器、打包校验器、本地调试运行器
- 插件节点加载器（`PluginNodeLoader`）与执行器（`PluginNodeExecutor`）
- `NodeType.PLUGIN` 枚举成员与 `PluginNodeConfiguration` 模型
- `GraphCompiler` 扩展 `plugin_schema_provider` 可选参数（向后兼容）
- 贡献市场：模型、状态机（`ContributionStateMachine`）、签名校验（`SignatureVerifier`）、静态安全扫描（`StaticSecurityScanner`）、发布服务
- REST API（`/api/v1/plugin-nodes`、`/api/v1/contributions`）
- 前端面板（`PluginNodeManager`、`ContributionSubmitWizard`、`ContributionReviewBoard`）

#### M27 SSO 与企业连接器（T162-T181）

- SSO 模型与枚举（`SsoProtocolType`）
- 凭据加密器（`CredentialCipher`）——AES-GCM 对称加密
- IdP 配置管理器（`IdpConfigManager`）
- SAML 适配器（`SamlAdapter`）与 OIDC 适配器（`OidcAdapter`）
- 防重放守卫（`ReplayGuard`）——基于 TTL 缓存
- JIT 供给（`JitProvisioner`）——自动创建/更新用户与角色映射
- SSO 服务（`SsoService`）——统一发起/回调/降级
- 企业连接器模型与枚举（`DataSourceType.CRM/ERP/OA`）
- 凭据仓库（`EnterpriseCredentialStore`）——版本化凭据管理
- 数据范围守卫（`DataScopeGuard`）与连接测试器（`ConnectionTester`）
- CRM/ERP/OA 连接器骨架
- REST API（`/api/v1/auth/sso/**`、`/api/v1/connectors/enterprise/**`）
- 前端面板（`SsoSettingsPanel`、`EnterpriseConnectorPanel`）

#### M28 评估框架（T182-T191）

- 评估模型与枚举（`EvaluationType`、`EvaluationTaskState`）
- 场景集管理（`ScenarioSetService`）
- 轨迹评分器：规则评分（`RuleBasedTrajectoryScorer`）+ LLM 评分（`LlmTrajectoryScorer`）
- CLEAR 四维评分（`ClearScorer`）——Correctness / Latency / Efficiency / Architecture / Robustness
- 评估执行器（`EvaluationRunner`）——异步执行场景集
- 基线存储（`BaselineStore`）与回归报告（`RegressionReporter`）
- REST API（`/api/v1/evaluation/**`）
- 前端面板（`EvaluationDashboard`）

#### M29 语义索引发现与收尾（T192-T203）

- 语义发现模型与枚举（`DescriptionSource`）
- 关键词倒排索引（`KeywordIndex`）——BM25 评分
- 向量索引（`VectorIndex`）——可插拔后端（本地 / 外部）
- 混合检索（`HybridRetriever`）——RRF 融合排序
- 发现服务（`DiscoveryService`）——统一查询入口
- REST API（`/api/v1/agent-discovery/**`）
- 前端面板（`AgentDiscoveryPanel`）
- 端到端集成测试与兼容性回归测试
- 8 份阶段文档

#### 前端管理后台入口

- `AdminConsole` 管理后台根组件，聚合 phase3/phase4 全部 12 个治理与管理面板
- `App.tsx` 添加画布编辑器 ↔ 管理后台视图切换

### 修复

- `.env.example` 中 `TOKEN_SIGNING_SECRET` 占位符笔误（`change-me-token-secretF` → `change-me-token-secret`）
- `debug_runner.py` 移除不优雅的 `if False else` 条件表达式
- `sso_router.py` 移除未使用的 `HTTPException` 导入

---

## [1.3.5] - 2026-09-24

### 新增

#### 偏差修正（T80-T129）

- ABAC 属性策略引擎扩展（属性求值器、策略仓储、策略引擎、拦截器）
- 墓碑与被遗忘权（GDPR 合规删除、墓碑服务、降级响应）
- 语义缓存（缓存后端抽象、LRU 内存实现、Redis 实现）
- 事件总线（事件总线抽象、内存实现、Redis Stream 实现）
- NL 自然语言编排（意图解析器、NL 编排服务、模板生成器）
- 兼容性回归测试套件

---

## [1.3.0] - 2026-09-24

### 新增

#### M19-M24 生产治理（T46-T79）

- 多租户 RBAC 与组织树
- 计量与计费（令牌计量、预算控制、成本仪表盘）
- 审计日志与合规查询
- 全局调度器与资源池管理
- 拦截器链（鉴权→ABAC→租户隔离→审计→配额→预算→调度）
- 治理 REST API 与前端面板（`GovernanceDashboard`）

---

## [1.2.0] - 2026-09-24

### 新增

#### 引擎增强（T24-T45）

- 回滚与重放机制
- 实时数据订阅
- 上下文查询 API
- MCP 服务端集成
- 大数据连接器（Hologres、Kafka、Flink、Spark）
- 语义缓存
- 执行可视化

---

## [1.1.0] - 2026-09-24

### 新增

#### 核心引擎（T1-T23）

- YAML 声明式工作流定义与解析
- 工作流图拓扑校验与编译
- 调度器（确定性调度）
- 节点执行器（Agent、Tool、DataSource、Trigger、控制流、子工作流、动态注入、数据变换）
- 检查点与故障恢复
- 仓储抽象与实现（内存 / PostgreSQL）
- FastAPI 应用工厂与 REST API
- React 画布编辑器（节点面板、画布、配置面板、范例库）
- YAML 导入/导出

---

## 环境要求

- Python ≥ 3.10（实测基线 3.10.20）
- Node.js ≥ 18
- PostgreSQL ≥ 14（可选：`[postgres]` extra + postgres profile；未配置时 local_file 形态）
- Redis ≥ 6（可选，未配置时降级为内存实现）

## 已知限制

- SSO 企业身份为 **experimental**：SAML/OIDC 适配器为骨架，验签能力未生产验证
  （roadmap S2 范围）
- LangGraph 双运行时为**严格子集**：并行扇出、HITL、回滚重放等五类场景在子集外，
  由内置图状态机引擎执行（见 `docs/langgraph-equivalence-matrix.md`）
- 企业连接器（CRM/ERP/OA）为骨架实现，需按实际系统适配
- 容器隔离运行时为接口预留，需对接实际容器运行时
- 向量索引外部后端为接口预留，需对接实际向量数据库
- 前端 `npm run build` 受 `tsc --noEmit` 门控：13 基线错误清零前（roadmap A4），
  生产构建路径以 `vite build` 口径为准