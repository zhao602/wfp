# 范例五：反思迭代（reflection_iteration）

## 适用场景

质量收敛场景：生成 Agent 产出初版内容，评审 Agent 检查质量，
未达标则带反馈回到生成 Agent 重做，循环直至达标或达最大迭代次数。
验证反思回路（generate → critique → revise）与循环收敛。

## 节点结构

```
trigger_start(触发器) → generator_agent(生成 Agent) → critic_agent(评审 Agent)
    → quality_check(质量分支) → check_iterations(循环节点)
        ├─ 未达标（回边）→ generator_agent 重做
        └─ 达标（非回边出边）→ display_result(输出展示)
```

| 节点标识 | 节点类型 | 职责 |
| --- | --- | --- |
| trigger_start | trigger | 接收任务描述与质量阈值（默认 0.8） |
| generator_agent | agent | 生成/改进输出，输出 content 与 version |
| critic_agent | agent | 评审质量，输出 score / feedback / pass |
| quality_check | control_flow | 条件分支：达标与否都进入迭代检查，由循环节点统一裁决 |
| check_iterations | control_flow | 循环节点：`critique_result.pass == true` 终止，上限 5 次 |
| display_result | output_display | 展示收敛后的生成结果与最后一轮评审结论 |

覆盖节点类型：触发器、Agent、控制流（条件分支 + 循环）、输出展示（四类）。

> 字段口径：循环节点只识别 `loop_body_entry` / `loop_termination_condition` /
> `max_iterations`（loop_variable、loop_condition、loop_target 不是合法字段，
> 写入会被忽略并导致循环必然不收敛）。
> 出口口径：终止条件满足时取"非回边出边"，循环节点除指向循环体的回边外，
> 另有一条指向输出展示的出边作为达标出口。

## 核心验证点

1. **反思回路**：每轮"生成 → 评审"，评审反馈进入下一轮生成上下文。
2. **循环收敛**：`critique_result.pass == true` 时经非回边出边退出循环。
3. **迭代上限**：始终未达标时迭代不超过 `max_iterations`（5）次。

## 运行方式

```bash
curl -X POST http://localhost:8000/api/v1/instances \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"definition_id": "reflection_iteration", "inputs": {"task": "撰写产品发布说明", "quality_threshold": "0.8"}}'
```

或经范例库服务加载：`ExampleLibraryService().load_example("reflection_iteration")`。

## 预期结果

- 生成与评审至少各执行 1 次，至多 5 轮；
- 退出循环后 `display_result` 执行一次，`generation_result` 与
  `critique_result`（最后一轮评审）字段非空。
