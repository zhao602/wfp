# 范例二：并行执行（parallel_execution）

## 适用场景

双视角分析场景：两个分析 Agent 从不同角度处理同一查询，
结果经并行汇聚节点合并后统一展示。
验证 `parallel_join` 控制流的分支汇聚语义与 `fast_fail` 失败策略。

## 节点结构

```
trigger_start(触发器) → branch_a(Agent) ─┬─→ parallel_fanout(并行汇聚) → display_result(输出展示)
                    └→ branch_b(Agent) ─┘
```

| 节点标识 | 节点类型 | 职责 |
| --- | --- | --- |
| trigger_start | trigger | 手动触发，注入查询主题 |
| branch_a | agent | 分析师 A：输出观点与结论 |
| branch_b | agent | 分析师 B：从另一角度输出观点与结论 |
| parallel_fanout | control_flow | parallel_join 汇聚两个分支，`fast_fail` 失败策略 |
| display_result | output_display | 展示双分支分析结果 |

覆盖节点类型：触发器、Agent、控制流（并行汇聚）、输出展示（四类）。

> 调度口径：调度器按声明顺序逐节点前进，普通节点取第一条出边。
> 因此两个分析分支以顺序边串联保证都被执行（`branch_a → branch_b`），
> `parallel_branches` 声明参与汇聚的分支集合，parallel_join 以两条入边
> 承接汇聚语义；若写成 fan-out（一个节点两条出边），仅有首条出边的分支
> 会被执行。

## 核心验证点

1. **分支全执行**：`branch_a` 与 `branch_b` 依次执行，命名输出 `analysis_a` / `analysis_b` 均写入共享状态。
2. **汇聚语义**：`parallel_fanout` 按 `parallel_branches` 校验两个分支均完成后前进。
3. **失败策略**：任一分支失败时按 `fast_fail` 立即终止整个工作流。

## 运行方式

```bash
curl -X POST http://localhost:8000/api/v1/instances \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"definition_id": "parallel_execution", "inputs": {"query": "新能源车市场趋势"}}'
```

或经范例库服务加载：`ExampleLibraryService().load_example("parallel_execution")`。

## 预期结果

- 两个分支 Agent 先后执行完成，`analysis_a` / `analysis_b` 字段非空；
- 汇聚节点执行一次后进入输出展示，双分支结论并列呈现。
