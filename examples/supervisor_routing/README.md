# 范例三：监督者路由（supervisor_routing）

## 适用场景

监督者模式：由一个 Agent 充当监督者，根据输入查询决定路由到不同专业 Agent，
分析结果统一经输出展示节点呈现。验证条件分支节点的唯一路径选择与默认分支策略。

## 节点结构

```
trigger_start(触发器) → supervisor(Agent) → branch_router(条件分支)
                                                    ├─→ finance_analysis(Agent) ─┐
                                                    └─→ operations_analysis(Agent)─┤
                                                                                 ↓
                                                          display_result(输出展示)
```

| 节点标识 | 节点类型 | 职责 |
| --- | --- | --- |
| trigger_start | trigger | 手动触发，注入用户查询 |
| supervisor | agent | 监督者，输出 `route` 字段（finance / operations） |
| branch_router | control_flow | 条件分支，按 `routing_decision.route` 选择下游 |
| finance_analysis | agent | 财务分析专家 |
| operations_analysis | agent | 运营分析专家 |
| display_result | output_display | 汇聚展示路由决策与被激活分支的分析结果 |

覆盖节点类型：触发器、Agent、控制流（条件分支）、输出展示（四类）。

> 迁移说明（merge_tool_into_agent_node DC-E / CHG-23）：结论输出由输出展示
> 节点承接，不再使用独立 tool 节点；分析结果以命名输出写入共享状态。

## 核心验证点

1. **条件分支唯一路径选择**：同一实例中 `finance_analysis` 与 `operations_analysis` 有且仅有一个被执行。
2. **默认分支策略**：当 `route` 既非 `finance` 也非 `operations` 时，走 `default_branch_target`（此处为 `finance_analysis`），避免"无匹配分支"直接报错终止。

## 运行方式

```bash
curl -X POST http://localhost:8000/api/v1/instances \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"definition_id": "supervisor_routing", "inputs": {"query": "上季度利润下降原因"}}'
```

## 预期结果

- `supervisor` 输出 `route` 字段后，`branch_router` 仅激活一条分支；
- 未被选中的分支节点状态为 `SKIPPED`，不产生 `NODE_COMPLETED` 事件；
- `display_result` 汇聚展示路由决策与被激活分支的命名输出，最终 `routing_decision` 等字段非空。
