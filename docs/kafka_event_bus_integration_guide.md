# Kafka事件总线集成指南

## 1. 概述

本模块为平台提供消息事件总线能力，用于工作流执行事件与会话事件流的发布订阅。通过配置切换 Kafka 或内存后端，未配置时自动降级为内存实现，业务不中断。

## 2. 配置

### 2.1 配置项

```python
# settings.py
kafka_enabled: bool = False          # 是否启用Kafka
kafka_bootstrap_servers: str = ""    # Kafka引导服务器列表
kafka_workflow_topic: str = "workflow-events"  # 工作流事件Topic
kafka_session_topic: str = "session-events"    # 会话事件Topic
kafka_consumer_group: str = "opf-consumer"     # 消费者组
kafka_auto_offset_reset: str = "earliest"      # Offset重置策略
kafka_enable_auto_commit: bool = True          # 自动提交Offset
```

### 2.2 启用Kafka

```bash
# .env
KAFKA_ENABLED=true
KAFKA_BOOTSTRAP_SERVERS=kafka1:9092,kafka2:9092
KAFKA_WORKFLOW_TOPIC=workflow-events
KAFKA_SESSION_TOPIC=session-events
```

### 2.3 降级模式

- `kafka_enabled=False` → 使用内存事件总线
- `kafka_enabled=True` 但 `aiokafka` 包未安装 → 使用内存事件总线 + 记录 `MQ_DEGRADED` 日志
- `kafka_enabled=True` 但连接失败 → 使用内存事件总线 + 记录 `MQ_DEGRADED` 日志

## 3. 事件总线

### 3.1 抽象接口

```python
class IEventBus(Protocol):
    async def publish(self, topic: str, event: EventMessage) -> PublishResult: ...
    async def subscribe(self, topic: str, handler: Callable) -> None: ...
    async def replay_from_offset(
        self, topic: str, start_offset: int, max_count: int
    ) -> ReplayResult: ...
    @property
    def is_degraded(self) -> bool: ...
```

### 3.2 事件消息模型

```python
EventMessage(
    event_id="evt_xxx",              # 事件唯一标识
    event_type=MessageEventType.WORKFLOW_EXECUTION,  # 事件类型
    sequence_number=1,               # 序列号（单调递增）
    payload={"node_id": "node_1"},   # 事件载荷
    instance_id="inst_1",            # 实例标识
    occurred_at=datetime.now(tz),    # 事件时间
    is_degraded=False,               # 是否经降级内存总线发布
)
```

### 3.3 事件类型

| 事件类型 | 说明 |
|---------|------|
| `WORKFLOW_EXECUTION` | 工作流执行事件（节点开始/完成/失败） |
| `SESSION_STATE_CHANGE` | 会话状态变更事件 |
| `WORKFLOW_COMPLETED` | 工作流执行完成 |
| `WORKFLOW_FAILED` | 工作流执行失败 |

## 4. 事件发布

### 4.1 工作流执行事件发布

```python
from orchestration_platform.governance.event_bus.workflow_event_publisher import (
    WorkflowEventPublisher,
)

publisher = WorkflowEventPublisher(event_bus, topic="workflow-events")

await publisher.publish_execution_event(
    instance_id="inst_1",
    node_id="node_retrieve",
    status="completed",
    payload={"result": "data_retrieved"},
)
```

### 4.2 会话事件流发布

```python
from orchestration_platform.governance.event_bus.session_event_publisher import (
    SessionEventPublisher,
)

session_publisher = SessionEventPublisher(event_bus, topic="session-events")

await session_publisher.publish_session_event(
    session_id="session_1",
    event_type="state_change",
    payload={"from": "idle", "to": "active"},
)
```

## 5. 事件重放恢复

### 5.1 重放服务

```python
from orchestration_platform.governance.event_bus.event_replay_service import (
    EventReplayService,
)

replay_service = EventReplayService(event_bus)

result = await replay_service.replay(
    topic="workflow-events",
    start_offset=0,
    max_count=1000,
)
```

### 5.2 重放结果

```python
ReplayResult(
    events=[...],           # 重放事件列表
    has_gap=False,          # 是否存在缺失区间
    gap_ranges=[],          # 缺失区间列表 [(start, end), ...]
)
```

### 5.3 缺失区间检测

当 Kafka 保留策略导致事件过期被删除时，重放会检测到序列号不连续，返回 `has_gap=True` 和缺失区间列表。应用可据此触发补偿逻辑。

## 6. 事件序列号重排序

`EventSequenceReorder` 对乱序事件按序列号排序：

```python
from orchestration_platform.governance.event_bus.event_sequence_reorder import (
    EventSequenceReorder,
)

reorder = EventSequenceReorder()
sorted_events = reorder.sort(events)
gaps = reorder.detect_gaps(events)  # 检测缺失区间
```

## 7. 既有事件溯源兼容

- Kafka 事件与本地事件存储并存（B97）
- 不修改既有事件溯源机制
- Kafka 事件总线作为新增能力，不影响既有本地事件存储
- 降级模式下事件通过内存总线发布，标记 `is_degraded=True`

## 8. 管理API

### 8.1 事件总线状态

```
GET /api/v1/governance/event-bus/status
```

返回事件总线类型、降级状态、Topic配置。

### 8.2 健康检查

```
GET /api/v1/governance/event-bus/health
```

返回事件总线健康状态（Kafka 连接检查或内存状态）。

## 9. 延迟导入

`aiokafka` 采用延迟导入，未安装时不影响平台启动：

```python
# kafka_event_bus.py
try:
    from aiokafka import AIOKafkaProducer
except ImportError:
    raise ImportError("aiokafka not installed")
```

`EventBusManager` 捕获 `ImportError` 并降级为内存后端，记录 `MQ_DEGRADED` 日志。

## 10. 模块结构

```
governance/event_bus/
├── __init__.py
├── models.py                     # 事件总线领域模型
├── kafka_event_bus.py            # Kafka事件总线实现
├── in_memory_event_bus.py        # 内存事件总线实现（降级）
├── event_sequence_reorder.py     # 事件序列号重排序
├── event_bus_manager.py          # 事件总线管理器
├── workflow_event_publisher.py   # 工作流执行事件发布
├── session_event_publisher.py    # 会话事件流发布
├── event_replay_service.py       # 事件重放恢复服务
└── abstractions/
    └── event_bus.py              # IEventBus 接口
```

## 11. 验收对照

| 验收编号 | 内容 | 对应任务 |
|---------|------|---------|
| A96 | Kafka事件总线配置与抽象接口 | T115, T116, T126 |
| A97 | Kafka事件总线与工作流事件发布 | T117, T120, T121, T126 |
| A98 | 会话事件流发布 | T122, T126 |
| A99 | 事件重放恢复与序列号重排序 | T119, T123, T126 |
| A100 | Kafka降级模式与管理API | T118, T124, T125, T126 |
| B95 | Kafka降级连续性与配置默认值 | T115, T118, T124, T126 |
| B96 | Kafka延迟导入可观测 | T117, T120, T124, T126 |
| B97 | Kafka既有事件溯源兼容 | T123, T126 |
