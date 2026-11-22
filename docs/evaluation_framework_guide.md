# Agent 效率评估框架指南

> 本文档说明 Agent 效率评估框架的场景集定义与版本管理、基线/回归评估流程、CLEAR 五维指标解读、可复现性配置（seed/temperature）。

## 1. 概览

评估框架（evaluation）量化 Agent 能力，支撑回归测试与选型决策。框架基于场景集驱动，采用 CLEAR 五维指标，默认启用但场景集为空。

### 1.1 治理目标

- **可量化**：统一指标定义，Agent 能力可比较。
- **可回归**：版本迭代后自动回归，防止能力退化。
- **可复现**：固定 seed 与 temperature，评估结果可复现。

### 1.2 配置项清单（默认值）

```yaml
evaluation:
  enabled: true
  scenario_set: ./scenarios/default       # 场景集路径
  baseline_version: ""                    # 基线版本标签
  clear_dimensions: [correctness, latency, efficiency, adaptability, robustness]
  seed: 42                                # 随机种子
  temperature: 0.0                        # 采样温度（0=确定性）
  parallel_workers: 4
  timeout_per_scenario_ms: 60000
  report_dir: ./reports/evaluation
  fail_on_regression: false               # 回归是否阻断流水线
```

## 2. 场景集定义与版本管理

### 2.1 场景集结构

```
scenarios/
└── default/
    ├── manifest.yaml          # 场景集元数据
    ├── scenarios/
    │   ├── s001.yaml
    │   ├── s002.yaml
    │   └── ...
    └── expected/
        ├── s001.expected.json
        └── ...
```

### 2.2 场景集 manifest

```yaml
# scenarios/default/manifest.yaml
name: default
version: 1.0.0
description: "通用 Agent 能力基线场景集"
agent_target: all               # all | 指定 agent id
tags: [baseline, general]
scenarios:
  - id: s001
    name: "数据抽取准确性"
    difficulty: easy
  - id: s002
    name: "多轮对话上下文保持"
    difficulty: medium
```

### 2.3 场景定义

```yaml
# scenarios/default/scenarios/s001.yaml
id: s001
name: "数据抽取准确性"
input:
  prompt: "从以下文本抽取所有日期：..."
  context:
    document: "..."
expected:
  type: exact_match            # exact_match | contains | semantic | custom
  value: ["2026-01-15", "2026-03-20"]
  tolerance: 0                 # 允许偏差
scoring:
  weights:
    correctness: 1.0
    latency: 0.2
```

### 2.4 版本管理

- 场景集使用语义化版本，`manifest.version`。
- 基线版本由 `evaluation.baseline_version` 指定。
- 场景集变更须提交版本号变更，并记录 changelog。
- 旧版本场景集保留，支持历史回归对比。

## 3. 基线与回归评估流程

### 3.1 基线评估

建立某 Agent 版本的能力基线，作为后续回归参照。

```bash
agentctl eval baseline \
  --agent my-agent \
  --scenario-set default \
  --version 1.0.0 \
  --tag baseline-2026-09
```

基线报告存于 `reports/evaluation/baseline/`，含各场景 CLEAR 得分。

### 3.2 回归评估

对比当前版本与基线版本，检测能力退化。

```bash
agentctl eval regression \
  --agent my-agent \
  --scenario-set default \
  --baseline baseline-2026-09
```

### 3.3 回归判定

| 指标变化 | 判定 |
| -------- | ---- |
| 任一维度得分下降 > 5% | `regression_detected` |
| 得分变化在 ±5% 内 | `stable` |
| 得分提升 > 5% | `improvement` |

```yaml
evaluation:
  regression_threshold_pct: 5     # 回归判定阈值
  fail_on_regression: false        # 检出回归是否阻断 CI
```

### 3.4 评估流程

```
加载场景集 → 并行执行场景 → 采集输出 → 对照 expected 评分 → 汇总 CLEAR → 生成报告
```

## 4. CLEAR 五维指标解读

CLEAR 是评估框架的核心指标体系，五个维度正交覆盖 Agent 能力。

### 4.1 维度定义

| 维度 | 代号 | 含义 | 评分方式 |
| ---- | ---- | ---- | -------- |
| 正确性 | C (correctness) | 输出与期望的符合程度 | exact/contains/semantic 匹配 |
| 延迟 | L (latency) | 响应时延 | 与阈值对比，越低越好 |
| 效率 | E (efficiency) | 资源效率（token/调用次数） | 归一化，越低越好 |
| 适应性 | A (adaptability) | 对输入扰动的稳健性 | 扰动场景得分衰减 |
| 鲁棒性 | R (robustness) | 异常输入下不崩溃能力 | 异常场景通过率 |

### 4.2 综合得分

```yaml
evaluation:
  clear_weights:
    correctness: 0.4
    latency: 0.15
    efficiency: 0.15
    adaptability: 0.15
    robustness: 0.15
```

综合得分 = Σ(维度得分 × 权重)，取值 [0, 1]。

### 4.3 维度评分示例

```json
{
  "scenario": "s001",
  "scores": {
    "correctness": 0.95,
    "latency": 0.80,
    "efficiency": 0.85,
    "adaptability": 0.70,
    "robustness": 1.0
  },
  "overall": 0.89
}
```

### 4.4 维度解读指引

- **C 偏低**：核心逻辑或 prompt 需优化。
- **L 偏低**：检查链路耗时，考虑缓存或并行。
- **E 偏低**：减少冗余调用，优化 token 用量。
- **A 偏低**：增强 prompt 鲁棒性，补充示例。
- **R 偏低**：增加输入校验与异常处理。

## 5. 可复现性配置

### 5.1 seed 与 temperature

```yaml
evaluation:
  seed: 42
  temperature: 0.0
```

- `seed`：控制场景顺序、扰动采样、随机选择，确保多次运行一致。
- `temperature: 0.0`：确定性采样，相同输入产生相同输出。
- 需要评估多样性时可将 `temperature` 调高，但会降低复现性。

### 5.2 复现性保障

| 因素 | 保障措施 |
| ---- | -------- |
| 随机顺序 | 固定 `seed` |
| 模型采样 | `temperature: 0.0` |
| 场景版本 | 锁定 `scenario_set` 版本 |
| Agent 版本 | 记入报告，回归对比同一版本 |
| 环境差异 | 报告记录运行环境（模型、SDK 版本） |

### 5.3 复现报告

报告头部记录复现所需全部信息：

```json
{
  "reproducibility": {
    "seed": 42,
    "temperature": 0.0,
    "scenario_set": "default@1.0.0",
    "agent_version": "1.2.0",
    "model": "glm-5.2",
    "sdk_version": "1.0.0"
  }
}
```

## 6. 降级行为与故障排查

### 6.1 降级

| 场景 | 降级策略 |
| ---- | -------- |
| 场景集为空 | 跳过评估，记录 `evaluation.skipped` 告警 |
| 部分场景超时 | 该场景标记 `timeout`，其余继续 |
| Agent 不可用 | 整体评估失败，不阻断平台 |
| 报告目录不可写 | 输出至 stdout 并告警 |

### 6.2 故障排查

| 现象 | 可能原因 | 处置 |
| ---- | -------- | ---- |
| 评估跳过 | 场景集为空或路径错误 | 核对 `scenario_set` 路径 |
| 结果不可复现 | seed/temperature 未固定 | 确认配置为 42/0.0 |
| 回归误报 | 基线版本不匹配 | 核对 `baseline_version` 与 tag |
| 场景超时 | Agent 响应慢或场景过重 | 调整 `timeout_per_scenario_ms` |
| 维度得分为 0 | expected 配置错误 | 检查 expected.type 与 value |

### 6.3 诊断命令

```bash
# 运行单场景评估
agentctl eval run --agent my-agent --scenario s001

# 对比两份报告
agentctl eval diff report_a.json report_b.json

# 验证场景集
agentctl eval validate-scenarios ./scenarios/default
```

## 7. 最佳实践

- 基线建立后锁定 `baseline_version`，每次发版前跑回归。
- 场景集按难度分层，覆盖核心路径与边界情况。
- `fail_on_regression` 在 CI 中设为 `true`，防止退化合入。
- 定期审查低分场景，驱动 Agent 优化。
- 报告归档保留，支撑长期能力趋势分析。
- 评估与 Agent 发版解耦，避免互相阻塞。
