# Agent 发现指南（语义索引）

> 本文档说明基于语义索引的 Agent 发现能力，涵盖语义描述管理（三态来源）、检索使用与阈值调优、向量引擎集成与降级。

## 1. 概览

Agent 发现（agent_discovery）通过语义索引复用已有 Agent，避免重复开发。平台对每个 Agent 建立语义描述，支持基于自然语言检索匹配 Agent。默认启用本地索引。

### 1.1 治理目标

- **能力复用**：新需求先检索已有 Agent，避免重复造轮子。
- **描述可信**：语义描述来源可追溯，区分人工/自动/推断。
- **检索可调**：阈值与 Top-K 可调，平衡召回与精度。
- **降级可用**：向量引擎不可用时回退关键词检索。

### 1.2 配置项清单（默认值）

```yaml
agent_discovery:
  enabled: true
  index_backend: local              # local | vector
  vector_engine: faiss              # faiss | milvus | pgvector
  vector_dim: 768
  embedding_model: bge-base-zh
  retrieval_top_k: 3
  similarity_threshold: 0.75        # 召回阈值
  fallback_keyword: true            # 向量不可用时回退关键词
  index_refresh_min: 30             # 索引刷新间隔
  description_source_priority: [manual, auto, inferred]
```

## 2. 语义描述管理（三态来源）

### 2.1 三态来源

每个 Agent 的语义描述有三种来源，按优先级取用：

| 来源 | 代号 | 说明 | 可信度 |
| ---- | ---- | ---- | ------ |
| 人工声明 | `manual` | 开发者在 Agent 定义中显式编写 | 高 |
| 自动抽取 | `auto` | 平台从 Agent 元数据/代码自动生成 | 中 |
| 推断生成 | `inferred` | 由 LLM 根据 Agent 行为日志推断 | 低 |

### 2.2 来源优先级

```yaml
agent_discovery:
  description_source_priority: [manual, auto, inferred]
```

检索时使用最高优先级可用来源。若 `manual` 存在则忽略其余；若仅有 `inferred` 则标注低可信。

### 2.3 人工声明

```yaml
agents:
  - id: data_analyst
    discovery:
      description:
        source: manual
        text: "负责结构化数据分析与可视化，支持 CSV/Excel 输入，输出图表与摘要"
        keywords: [数据分析, 可视化, CSV, 图表]
        capabilities: [data_analysis, charting]
```

### 2.4 自动抽取

平台在 Agent 注册时自动生成描述，包含：

- Agent id 与名称
- 输入/输出类型
- 关联节点与工具
- 标签

自动抽取描述标记 `source: auto`，可被人工声明覆盖。

### 2.5 推断生成

当 `manual` 与 `auto` 均不充分时，平台可调用 LLM 根据 Agent 历史调用日志推断描述：

```yaml
agent_discovery:
  inferred:
    enabled: true
    min_log_entries: 20            # 至少 20 条日志才推断
    review_required: true          # 推断描述需人工确认
```

推断描述标记 `source: inferred`，`review_required: true` 时需人工确认后方可用于检索。

## 3. 检索使用与阈值调优

### 3.1 检索调用

```bash
# 语义检索 Agent
agentctl discover "我需要一个能做财务报表分析的 Agent"
```

返回按相似度排序的 Top-K Agent，含相似度得分与描述来源。

### 3.2 返回示例

```json
{
  "query": "我需要一个能做财务报表分析的 Agent",
  "results": [
    {
      "agent_id": "finance_analyst",
      "score": 0.89,
      "description_source": "manual",
      "description": "负责财务报表分析与指标计算..."
    },
    {
      "agent_id": "data_analyst",
      "score": 0.78,
      "description_source": "auto",
      "description": "结构化数据分析与可视化..."
    }
  ]
}
```

### 3.3 阈值调优

| 参数 | 作用 | 调优建议 |
| ---- | ---- | -------- |
| `similarity_threshold` | 低于此得分的 Agent 不召回 | 精度优先调高，召回优先调低 |
| `retrieval_top_k` | 返回结果数 | 通常 3-5，过大增加噪声 |
| `description_source_priority` | 描述来源优先级 | 生产环境优先 manual |

### 3.4 调优决策

```
召回率偏低 → 降低 threshold 或增大 top_k
精度偏低（噪声多） → 提高 threshold 或收敛描述
低可信来源命中多 → 调整 priority 或补充 manual 描述
```

### 3.5 检索质量评估

```bash
# 评估检索质量（需标注集）
agentctl discover evaluate --testset ./testsets/discovery.json
```

输出 Precision@K、Recall@K、MRR 等指标。

## 4. 向量引擎集成与降级

### 4.1 支持的向量引擎

| 引擎 | 代号 | 适用场景 | 部署方式 |
| ---- | ---- | -------- | -------- |
| FAISS | `faiss` | 中小规模（< 10万 Agent） | 本地嵌入 |
| Milvus | `milvus` | 大规模、分布式 | 独立服务 |
| pgvector | `pgvector` | 已有 PG 环境 | PG 扩展 |

### 4.2 引擎配置

```yaml
agent_discovery:
  vector_engine: faiss
  vector_dim: 768
  engine_config:
    faiss:
      index_type: IVFFlat
      nlist: 100
    milvus:
      host: milvus.example.com
      port: 19530
      collection: agent_index
    pgvector:
      dsn: postgres://...
      table: agent_vectors
```

### 4.3 索引流程

```
Agent 注册/更新 → 生成语义描述 → embedding 向量化 → 写入向量引擎 → 可检索
```

- 索引刷新由 `index_refresh_min` 控制。
- Agent 描述变更触发增量更新。
- 删除 Agent 同步删除索引。

### 4.4 降级链

| 层级 | 触发条件 | 生效策略 | 检索质量 |
| ---- | -------- | -------- | -------- |
| L0 向量 | 无 | 向量语义检索 | 最优 |
| L1 关键词 | 向量引擎不可用 | 回退关键词检索 | 下降 |
| L2 本地 | 引擎与关键词均不可用 | 仅本地元数据匹配 | 显著下降 |
| L3 关闭 | `enabled: false` | 发现不可用 | 无 |

### 4.5 降级配置

```yaml
agent_discovery:
  fallback_keyword: true
  degradation:
    auto_fallback: true
    alert_on_degrade: true
    min_level: L1               # 最低允许层级
```

降级时发出 `discovery.fallback_keyword` 审计事件。

## 5. 故障排查

| 现象 | 可能原因 | 处置 |
| ---- | -------- | ---- |
| 召回结果偏少 | threshold 过高或索引未刷新 | 降低 threshold，手动刷新索引 |
| 召回精度低 | 描述质量差或 top_k 过大 | 补充 manual 描述，减小 top_k |
| 向量引擎连接失败 | 引擎不可达或配置错误 | 检查引擎配置与连通 |
| 降级告警频繁 | 向量引擎不稳定 | 排查引擎健康，考虑切换引擎 |
| 推断描述未生效 | `review_required` 未确认 | 人工确认推断描述 |
| embedding 失败 | 模型不可达或维度不匹配 | 检查 embedding_model 与 vector_dim |

### 5.1 诊断命令

```bash
# 查看发现服务状态
agentctl discover status

# 查看某 Agent 的描述与来源
agentctl discover describe --agent data_analyst

# 手动刷新索引
agentctl discover refresh-index

# 测试检索
agentctl discover test "财务报表分析" --top-k 5 --verbose
```

### 5.2 索引健康检查

```bash
agentctl discover health
```

输出：索引条数、最后刷新时间、引擎状态、降级层级。

## 6. 使用流程

### 6.1 启用发现

1. 设置 `agent_discovery.enabled: true`。
2. 选择 `index_backend` 与 `vector_engine`。
3. 为核心 Agent 补充 `manual` 描述。
4. 触发首次全量索引构建。
5. 测试检索并调优阈值。

### 6.2 日常维护

- 新 Agent 注册后自动进入索引。
- 定期审查 `inferred` 描述并确认或替换为 `manual`。
- 监控检索质量指标，必要时调优。
- 向量引擎变更时重建索引。

## 7. 最佳实践

- 核心 Agent 一律提供 `manual` 描述，确保高可信。
- `inferred` 描述必须经人工确认后用于检索。
- 生产环境 `similarity_threshold` 不低于 0.7，避免噪声。
- 向量引擎与平台解耦部署，便于独立扩缩容。
- 保留关键词检索作为永久降级方案。
- 定期评估检索质量，驱动描述与阈值优化。
- Agent 下线时同步清理索引，防止幽灵召回。
