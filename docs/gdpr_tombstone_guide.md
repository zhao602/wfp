# GDPR墓碑与被遗忘权操作指南

## 1. 概述

本模块实现 GDPR Article 17（被遗忘权）的墓碑删除机制。审计日志不进行物理删除，而是通过墓碑标记（tombstone）软删除原始内容，保留元数据以满足合规审计要求。

**核心原则**：
- 原始内容脱敏后不可恢复
- 元数据（时间、操作类型、资源标识）保留
- 哈希链连续性不破坏
- 物理删除被拒绝

## 2. 墓碑字段

`AuditLogEntry` 扩展以下字段（向后兼容，默认值不影响既有数据）：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `tombstone` | bool | False | 是否标记为墓碑 |
| `deleted_at` | datetime | None | 墓碑标记时间 |
| `deleted_by` | str | None | 执行墓碑标记的操作人 |
| `forget_operation_id` | str | None | 被遗忘权操作批次标识 |

## 3. REST API

### 3.1 单条墓碑标记

```json
POST /api/v1/governance/audit/tombstone/mark
{
    "entry_id": "audit_001",
    "operator_id": "gdpr_officer",
    "tenant_id": "tenant_1"
}
```

### 3.2 被遗忘权批量标记

```json
POST /api/v1/governance/audit/tombstone/forget-user
{
    "user_id": "user_to_forget",
    "tenant_id": "tenant_1",
    "operator_id": "gdpr_officer"
}
```

批量标记该用户的所有审计日志条目，生成独立的 `forget_operation_id`，并产生独立的审计日志记录墓碑操作本身。

### 3.3 墓碑查询

```
GET /api/v1/governance/audit/tombstone?tenant_id=tenant_1&user_id=user_1
```

查询结果仅返回元数据，不返回已脱敏的原始内容。

### 3.4 物理删除拒绝

```
DELETE /api/v1/governance/audit/logs/{entry_id}
```

返回 `403 Forbidden`，物理删除被永久拒绝。

## 4. 墓碑标记流程

```
查询条目 → 脱敏原始内容 → 保留元数据 → 重算哈希 → 写入墓碑标记
    ↓
哈希链断裂 → 中止标记 + 触发告警
```

### 4.1 脱敏处理

墓碑标记后：
- `request_payload` → 置为 None
- `response_summary` → 置为 None
- `actor_id` → 保留（用于合规审计追溯）
- `occurred_at` → 保留
- `resource_type` / `operation` → 保留

### 4.2 哈希链连续性

墓碑字段不参与哈希链序列化，保证墓碑标记不破坏哈希链连续性。哈希链校验时仅基于既有字段计算。

## 5. 被遗忘权服务

### 5.1 批量操作

```python
result = await right_to_be_forgotten_service.forget_user(
    user_id="user_to_forget",
    tenant_id="tenant_1",
    operator_id="gdpr_officer",
)
```

### 5.2 操作结果

```python
ForgetResult(
    success=True,
    forget_operation_id="forget_xxx",
    marked_count=150,
    failed_count=0,
    audit_entry_ids=["audit_forget_1"],  # 墓碑操作自身的审计日志
)
```

### 5.3 墓碑操作可审计

被遗忘权操作本身会生成独立的审计日志条目，记录：
- 操作人（`operator_id`）
- 被遗忘用户（`user_id`）
- 标记条目数（`marked_count`）
- 操作时间

## 6. 前端管理

`TombstonePanel` 组件提供：
- 墓碑标记操作界面
- 被遗忘权批量操作界面
- 墓碑查询结果展示（仅元数据）
- 物理删除拒绝提示

## 7. 降级模式

- `tombstone_enabled=False` 时，墓碑端点不注册
- 既有审计日志行为不变（墓碑字段默认 False）
- 不影响既有审计日志追加写入与查询

## 8. 模块结构

```
governance/audit/tombstone/
├── __init__.py
├── models.py                        # 墓碑领域模型
├── tombstone_service.py             # 墓碑标记服务
├── right_to_be_forgotten_service.py # 被遗忘权服务
├── tombstone_query_service.py       # 墓碑查询服务
└── abstractions/
    └── tombstone_service.py         # ITombstoneService 接口
```

## 9. 性能考虑

- 批量墓碑标记支持100万条审计日志在60秒内完成（B91）
- 批量操作使用仓储的 `mark_tombstone_batch` 方法，单次事务提交
- 墓碑查询仅返回元数据，不加载脱敏内容

## 10. 验收对照

| 验收编号 | 内容 | 对应任务 |
|---------|------|---------|
| A86 | 墓碑模型与仓储扩展 | T98, T99, T105 |
| A87 | 墓碑标记与被遗忘权批量标记 | T100, T101, T105 |
| A88 | 墓碑元数据保留与查询 | T100, T102, T105 |
| A89 | 墓碑操作自身可审计 | T101, T105 |
| A90 | 物理删除拒绝与前端管理 | T101, T102, T103, T104, T105 |
| B89 | 墓碑哈希链连续性 | T98, T100, T105 |
| B90 | 墓碑既有接口不变 | T99, T110, T105 |
| B91 | 墓碑批量操作性能 | T101, T105 |
