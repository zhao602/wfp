# 偏差修正总览

## 1. 背景

在第三阶段（生产治理）完成后，对照项目构建计划书进行偏差检查，发现5项偏差。本阶段（phase3_5_deviation_fixes）通过 SDD 流程（spec.md → design.md → tasks.md）对5项偏差进行系统性修正。

## 2. 偏差清单与修正情况

| 编号 | 偏差描述 | 修正里程碑 | 任务范围 | 状态 |
|------|---------|-----------|---------|------|
| D1 | 自然语言辅助编排缺失 | M19 | T80-T88 | 已完成 |
| D2 | ABAC权限模型未扩展 | M20 | T89-T97 | 已完成 |
| D3 | GDPR Article 17 墓碑删除缺失 | M21 | T98-T105 | 已完成 |
| D4 | Redis分布式缓存未集成 | M22 | T106-T114 | 已完成 |
| D5 | Kafka消息中间件未集成 | M23 | T115-T126 | 已完成 |

## 3. 各偏差修正摘要

### 3.1 D1: 自然语言辅助编排（M19, T80-T88）

**偏差**：项目构建计划书要求支持自然语言辅助编排，但既有平台仅支持 YAML 声明式定义工作流。

**修正内容**：
- 注入防护校验器（YAML注入/提示注入/越狱检测）
- NL意图解析器（调用LLM解析用户指令）
- YAML定义生成器（ParsedIntent→WorkflowDefinition→YAML）
- 既有工作流修改应用器（节点插入/删除/更新）
- NL编排服务（编排完整流程+拓扑校验前置）
- REST API：`POST /api/v1/nl_orchestration/create`、`/modify`
- 前端组件：NLOrchestrationPanel

**关键文件**：
- `src/orchestration_platform/governance/nl_orchestration/`
- `src/orchestration_platform/api/routers/nl_orchestration_router.py`
- `web/src/nl_orchestration/`

### 3.2 D2: ABAC权限模型扩展（M20, T89-T97）

**偏差**：计划书要求基于属性的访问控制（ABAC），但既有平台仅实现RBAC。

**修正内容**：
- ABAC领域模型（AbacPolicy, AbacCondition, AbacDecision）
- 属性求值器（主体/资源/环境三类属性实时求值）
- ABAC策略引擎（加载策略→逐条求值→合取匹配→首个匹配决定效果）
- 组合决策引擎（RBAC先+ABAC后，RBAC拒绝短路）
- ABAC拦截器 + InterceptorChain扩展（abac参数，向后兼容）
- REST API：`POST/GET/DELETE /api/v1/governance/abac/policies`
- 前端组件：AbacPolicyPanel

**关键文件**：
- `src/orchestration_platform/governance/rbac/abac/`
- `src/orchestration_platform/api/routers/governance/abac_router.py`
- `web/src/governance/AbacPolicyPanel.tsx`

### 3.3 D3: GDPR墓碑删除（M21, T98-T105）

**偏差**：计划书要求符合GDPR Article 17被遗忘权，但既有审计日志不可删除。

**修正内容**：
- AuditLogEntry扩展墓碑字段（tombstone/deleted_at/deleted_by/forget_operation_id，向后兼容）
- 墓碑标记服务（脱敏原始内容→保留元数据→重算哈希→写入墓碑标记）
- 被遗忘权服务（批量标记+独立审计日志+物理删除拒绝）
- 墓碑查询服务（仅返回元数据）
- REST API：墓碑标记/被遗忘权/墓碑查询/物理删除拒绝（403）
- 前端组件：TombstonePanel

**关键文件**：
- `src/orchestration_platform/governance/audit/tombstone/`
- `src/orchestration_platform/api/routers/governance/tombstone_router.py`
- `web/src/governance/TombstonePanel.tsx`

### 3.4 D4: Redis缓存集成（M22, T106-T114）

**偏差**：计划书要求Redis分布式缓存，但既有平台仅支持本地内存缓存。

**修正内容**：
- 缓存领域模型（CacheKey, LockHolder, RateLimitResult）
- 缓存后端抽象接口（ICacheBackend, IDistributedLock, IRateLimiter）
- Redis缓存后端实现（延迟导入redis.asyncio）
- 内存缓存后端实现（降级模式）
- 缓存管理器（根据配置选择Redis/内存后端）
- 缓存一致性刷新服务
- 降级模式（REDIS_DEGRADED日志）
- REST API：`GET /governance/cache/status`、`/health`

**关键文件**：
- `src/orchestration_platform/governance/cache/`
- `src/orchestration_platform/api/routers/governance/cache_router.py`

### 3.5 D5: Kafka消息中间件集成（M23, T115-T126）

**偏差**：计划书要求Kafka消息中间件，但既有平台仅支持本地事件溯源。

**修正内容**：
- 事件总线领域模型（EventMessage, PublishResult, ReplayResult）
- 事件总线抽象接口（IEventBus）
- Kafka事件总线实现（延迟导入aiokafka）
- 内存事件总线实现（降级模式）
- 事件序列号重排序（排序+缺失区间检测）
- 事件总线管理器（根据配置选择Kafka/内存后端）
- 工作流执行事件发布 + 会话事件流发布
- 事件重放恢复服务（重放+重排序+缺失检测）
- 降级模式（MQ_DEGRADED日志）
- REST API：`GET /governance/event-bus/status`、`/health`

**关键文件**：
- `src/orchestration_platform/governance/event_bus/`
- `src/orchestration_platform/api/routers/governance/event_bus_router.py`

## 4. 集成测试与兼容性验证（M24, T127-T129）

### 4.1 端到端集成测试（T127）

- 文件：`tests/governance/test_deviation_fixes_integration.py`
- 覆盖：NL编排→ABAC决策→执行→事件发布→审计墓碑全链路
- 降级模式全链路：Redis+Kafka均降级时业务不中断
- 组合决策+缓存集成：ABAC策略缓存至缓存后端

### 4.2 兼容性回归测试（T128）

- 文件：`tests/governance/test_deviation_fixes_compatibility.py`
- 覆盖：
  - LangGraph隔离契约（governance/无import langgraph）
  - 既有RBAC行为回归（不启用ABAC时行为不变）
  - 既有审计行为回归（不启用墓碑时行为不变）
  - 既有事件溯源回归（不启用Kafka时行为不变）
  - 既有接口签名不变（InterceptorChain/IAuditLogRepository向后兼容）
  - Python 3.10兼容性（无StrEnum/Self/TaskGroup/PEP695）

### 4.3 文档（T129）

- `docs/deviation_fixes_summary.md`（本文件）
- `docs/nl_orchestration_guide.md`
- `docs/abac_extension_guide.md`
- `docs/gdpr_tombstone_guide.md`
- `docs/redis_cache_integration_guide.md`
- `docs/kafka_event_bus_integration_guide.md`

## 5. 与构建计划书对齐情况

| 计划书要求 | 偏差编号 | 修正状态 | 对齐情况 |
|-----------|---------|---------|---------|
| 自然语言辅助编排 | D1 | 已完成 | 完全对齐 |
| ABAC权限模型 | D2 | 已完成 | 完全对齐 |
| GDPR被遗忘权 | D3 | 已完成 | 完全对齐 |
| Redis分布式缓存 | D4 | 已完成 | 完全对齐 |
| Kafka消息中间件 | D5 | 已完成 | 完全对齐 |

## 6. 技术约束遵循情况

- LangGraph隔离：所有治理模块通过抽象层封装，无直接import langgraph
- 向后兼容：所有新增参数默认None/False，不传时既有行为不变
- 降级模式：Redis/Kafka未配置或驱动缺失时降级为内存实现，业务不中断
- Python 3.10兼容：无StrEnum/Self/TaskGroup/PEP695等3.11+独有语法
- 延迟导入：redis.asyncio/aiokafka均延迟导入，未安装时不影响启动

## 7. 任务统计

- 任务总数：50个（T80-T129）
- 里程碑总数：6个（M19-M24）
- 功能验收：28个（A76-A103）
- 非功能验收：18个（B83-B100）
- 全部状态：已完成
