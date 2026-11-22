# 范例八：条件循环流水线（conditional_loop_pipeline）

## 适用场景

迭代优化场景：Agent 生成初稿后进入评审循环，每轮由评审 Agent 打分，
达标则退出输出最终稿，未达标则回到循环起点继续修订。
验证循环节点的 `max_iterations` 上限与 `loop_termination_condition` 终止条件两项核心能力。

## 节点结构

```
trigger_start(触发器) → generate_draft(Agent) → review_loop(循环)
                                                      ↓ (循环体)
                                                 review_draft(Agent 评审)
                                                      ↓
                                                 branch_check(条件分支)
                                                   ├─ 未达标 → review_loop(回边继续)
                                                   └─ 达标退出 ──────────────┐
                                                                              ↓
                                                    review_loop ──→ display_result(输出展示)
```

| 节点标识 | 节点类型 | 职责 |
| --- | --- | --- |
| trigger_start | trigger | 手动触发，注入 `topic` 与 `target_score` |
| generate_draft | agent | 生成初稿，输出 `text` 与 `score`（初始 0） |
| review_loop | control_flow | 循环节点，入口 `review_draft`，终止条件 `draft.score >= target_score`，上限 5 次 |
| review_draft | agent | 评审并修订，输出新的 `text` 与 `score` |
| branch_check | control_flow | 条件分支，未达标回循环；达标由循环节点统一裁决退出 |
| display_result | output_display | 展示评审定稿 |

覆盖节点类型：触发器、Agent、控制流（循环 + 条件分支）、输出展示（四类）。

> 迁移说明（merge_tool_into_agent_node DC-E / CHG-23）：结论输出由输出展示
> 节点承接，不再使用独立 tool 节点（原 finalize 已移除）。
> 出口口径：循环终止条件满足时取"非回边出边"，因此评审循环除指向循环体
> 的回边外，还需一条指向输出展示的出边作为达标出口。

## 核心验证点

1. **循环终止条件**：当 `draft.score >= target_score` 为真时，循环经非回边出边退出，进入 `display_result`。
2. **最大迭代上限**：即使始终未达标，循环次数不超过 `max_iterations`（5），到达上限后强制退出。
3. **回边合法性**：`branch_check → review_loop` 构成回边，因路径中含循环节点 `review_loop`，拓扑校验判定为合法环。

## 运行方式

```bash
curl -X POST http://localhost:8000/api/v1/instances \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"definition_id": "conditional_loop_pipeline", "inputs": {"topic": "产品介绍文案", "target_score": 80}}'
```

## 预期结果

- `review_draft` 至少执行 1 次，至多执行 5 次；
- 退出循环后 `display_result` 执行一次，`draft` 字段（评审定稿）非空；
- 若中途达标，后续迭代不再执行（已成功节点副作用不重复）。
