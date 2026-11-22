# 全局调度与资源治理配置说明

## 全局调度策略

支持三种调度策略：

- `strict_priority`：严格优先级，纯按优先级排序
- `fair_share`：公平共享，高优先级优先 + 租户/工作流公平分配
- `weighted_queue`：加权队列

通过 `/api/v1/governance/scheduler/policy` 查询与更新调度策略。

### 配置项

- `GLOBAL_SCHEDULER_ENABLED=true`：是否启用全局调度器
- `GLOBAL_SCHEDULER_DECISION_TIMEOUT_MS=100`：调度决策超时
- `SCHEDULER_QUEUE_MAX_SIZE=10000`：调度队列最大长度
- `SCHEDULER_FAIR_WEIGHT_DEFAULT=1`：公平调度默认权重

## 资源池配置

三类资源池：并发槽位、CPU、内存。

通过 `/api/v1/governance/resource-pools` 查询与配置资源池。

### 配置项

- `RESOURCE_POOL_CONCURRENCY_SLOT_QUOTA=100`：并发槽位配额
- `RESOURCE_POOL_OVERSUBSCRIPTION_RATIO=1.0`：资源超售比
- `RESOURCE_ESTIMATE_DEFAULT_SLOTS=1`：默认资源需求槽位

## 优先级与抢占配置

### 优先级

实例优先级：`low` < `normal` < `high` < `urgent`

### 抢占

高优先级实例无法获得资源时抢占低优先级实例资源：

- 抢占安全点：被抢占实例检查点保存完成后进行
- 被抢占实例暂停（而非终止），状态标记为 `PREEMPTED`
- 抢占次数限制：`SCHEDULER_PREEMPTION_MAX_COUNT=3`
- 抢占不可外部触发：仅全局调度器可发起

### 配置项

- `SCHEDULER_PREEMPTION_ENABLED=true`：是否启用抢占调度
- `SCHEDULER_PREEMPTION_MAX_COUNT=3`：单实例最大被抢占次数
