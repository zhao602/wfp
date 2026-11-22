# 上下文引擎配置说明

## 概述

上下文引擎负责 Agent 执行上下文的组装、裁剪与持久化，由四部分组成：
1. 系统提示（system_prompt）
2. 历史对话（conversation_history）
3. 工具结果（tool_results）
4. 共享状态引用（state_references）

## 窗口策略

### 滑动窗口（sliding_window）

保留最近 N 轮对话，超限时丢弃最早轮次。

```yaml
strategy_type: sliding_window
max_turns: 10
```

### 摘要窗口（summary_window）

将早期对话压缩为摘要，保留近期原文。

```yaml
strategy_type: summary_window
max_recent_turns: 5
summary_model: primary-model
```

### 重要性窗口（importance_window）

按 Token 重要性评分保留高价值内容。

```yaml
strategy_type: importance_window
max_tokens: 4096
importance_threshold: 0.5
```

## 上下文传递策略

子工作流节点支持两种上下文传递策略：

- `inherit`：继承父工作流上下文
- `isolate`：隔离上下文（子工作流独立上下文）

## 语义缓存

上下文引擎支持语义缓存，基于嵌入向量的相似查询缓存：

```python
from orchestration_platform.context_engine.semantic_cache import SemanticCache

cache = SemanticCache(
    similarity_threshold=0.92,
    max_entries=1000,
    ttl_seconds=3600,
)

# 查询缓存
result = cache.get("用户查询")
if result is None:
    result = await llm_infer("用户查询")
    cache.put("用户查询", result)
```

预期节省 60-95% Token 消耗。

## 检查点

上下文检查点支持持久化与跨节点传递，与执行检查点同事务写入。

## 脱敏

上下文日志与检查点经脱敏处理，移除完整密钥、令牌与用户敏感数据原文。
