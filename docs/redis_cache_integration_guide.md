# Redis缓存集成指南

## 1. 概述

本模块为平台提供分布式缓存、分布式锁与限流计数器能力。通过配置切换 Redis 或内存后端，未配置时自动降级为内存实现，业务不中断。

## 2. 配置

### 2.1 配置项

```python
# settings.py
redis_enabled: bool = False          # 是否启用Redis
redis_url: str = "redis://localhost:6379/0"  # Redis连接URL
redis_key_prefix: str = "opf:"       # 键前缀
redis_default_ttl: int = 300         # 默认TTL（秒）
redis_lock_timeout: int = 30         # 分布式锁超时（秒）
redis_rate_limit_window: int = 60    # 限流窗口（秒）
```

### 2.2 启用Redis

```bash
# .env
REDIS_ENABLED=true
REDIS_URL=redis://redis-host:6379/0
```

### 2.3 降级模式

- `redis_enabled=False` → 使用内存后端
- `redis_enabled=True` 但 `redis` 包未安装 → 使用内存后端 + 记录 `REDIS_DEGRADED` 日志
- `redis_enabled=True` 但连接失败 → 使用内存后端 + 记录 `REDIS_DEGRADED` 日志

## 3. 缓存后端

### 3.1 抽象接口

```python
class ICacheBackend(Protocol):
    async def get(self, namespace: str, key: str) -> Optional[bytes]: ...
    async def set(self, namespace: str, key: str, value: bytes, ttl: int): ...
    async def delete(self, namespace: str, key: str) -> bool: ...
    async def exists(self, namespace: str, key: str) -> bool: ...
```

### 3.2 使用示例

```python
from orchestration_platform.governance.cache.cache_manager import CacheManager

manager = CacheManager()
backend = manager.get_backend()

# 设置缓存
await backend.set("abac_policies", "policy_1", policy_json.encode(), ttl=300)

# 获取缓存
data = await backend.get("abac_policies", "policy_1")
if data is not None:
    policy = AbacPolicy.model_validate_json(data.decode())

# 删除缓存
await backend.delete("abac_policies", "policy_1")

# 检查存在
exists = await backend.exists("abac_policies", "policy_1")
```

### 3.3 命名空间

使用命名空间隔离不同业务的缓存键：
- `abac_policies` — ABAC策略缓存
- `workflow_definitions` — 工作流定义缓存
- `session_state` — 会话状态缓存

## 4. 分布式锁

### 4.1 抽象接口

```python
class IDistributedLock(Protocol):
    async def acquire(self, lock_key: str, holder: str, timeout: int) -> bool: ...
    async def release(self, lock_key: str, holder: str) -> bool: ...
    async def renew(self, lock_key: str, holder: str, timeout: int) -> bool: ...
```

### 4.2 使用示例

```python
lock = manager.get_lock()

# 获取锁
acquired = await lock.acquire("workflow_execute_lock", "instance_1", 30)
if acquired:
    try:
        # 执行临界区操作
        ...
    finally:
        await lock.release("workflow_execute_lock", "instance_1")
```

### 4.3 Redis实现

Redis分布式锁使用 SET NX EX 原子操作获取锁，Lua脚本释放锁（校验持有者）。

## 5. 限流计数器

### 5.1 抽象接口

```python
class IRateLimiter(Protocol):
    async def check(self, key: str, limit: int, window: int) -> bool: ...
```

### 5.2 使用示例

```python
rate_limiter = manager.get_rate_limiter()

# 每分钟允许100次
allowed = await rate_limiter.check("api:user_1", limit=100, window=60)
if not allowed:
    raise RateLimitExceededError()
```

### 5.3 Redis实现

Redis限流使用 INCR + EXPIRE 实现滑动窗口计数。

## 6. 缓存一致性刷新

`CacheRefreshService` 提供缓存一致性刷新能力：

- 策略变更后主动刷新缓存
- 支持按命名空间批量失效
- 刷新失败时记录日志但不中断业务

## 7. 管理API

### 7.1 缓存状态

```
GET /api/v1/governance/cache/status
```

返回缓存后端类型、降级状态、连接信息。

### 7.2 健康检查

```
GET /api/v1/governance/cache/health
```

返回缓存后端健康状态（Redis ping 或内存状态）。

## 8. 安全考虑

- 缓存不持久化敏感明文（B94）：敏感数据应在写入前加密或脱敏
- Redis连接URL不应包含在日志中
- `.env.example` 中仅包含占位符，不含真实连接信息

## 9. 模块结构

```
governance/cache/
├── __init__.py
├── models.py                     # 缓存领域模型
├── redis_cache_backend.py        # Redis后端实现
├── in_memory_cache_backend.py    # 内存后端实现（降级）
├── cache_manager.py              # 缓存管理器
├── cache_refresh_service.py      # 缓存一致性刷新
└── abstractions/
    └── cache_backend.py          # ICacheBackend/IDistributedLock/IRateLimiter 接口
```

## 10. 验收对照

| 验收编号 | 内容 | 对应任务 |
|---------|------|---------|
| A91 | Redis缓存配置与抽象接口 | T106, T107, T114 |
| A92 | Redis分布式缓存与集成 | T108, T110, T114 |
| A93 | Redis分布式锁 | T108, T114 |
| A94 | Redis限流计数器 | T108, T114 |
| A95 | Redis降级模式与一致性刷新 | T109, T111, T112, T113, T114 |
| B92 | Redis降级连续性与配置默认值 | T106, T109, T112, T114 |
| B93 | Redis延迟导入可观测 | T108, T112, T114 |
| B94 | Redis缓存不持久化敏感明文 | T108, T114 |
