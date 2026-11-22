# 范例四：层级委派（hierarchical_delegation）

## 适用场景

多层级委派场景：总监 Agent 分解任务，分别委派给前端组长与后端组长
（各为一次子工作流调用），组长在子工作流内再委派两名组员执行并汇总，
最后由总监汇总两份团队报告。
验证子工作流嵌套（深度 2）与 INHERIT 上下文传递策略。

## 节点结构

主定义（hierarchical_delegation.yaml）：

```
trigger_start(触发器) → director_agent(总监 Agent)
    → delegate_frontend(子工作流：委派前端组长)
    → delegate_backend(子工作流：委派后端组长)
    → director_summary(总监汇总 Agent)
```

子定义（team_lead_subworkflow.yaml，definition_id: hierarchical_delegation_team_lead）：

```
trigger_start(组长接收任务) → team_lead_agent(组长 Agent)
    → member_1(组员一 Agent) → member_2(组员二 Agent)
    → team_lead_summary(组长汇总 Agent)
```

| 节点标识 | 节点类型 | 职责 |
| --- | --- | --- |
| director_agent | agent | 总监：分解任务为前端/后端子任务（输出 frontend_task / backend_task） |
| delegate_frontend | subworkflow | 调用组长子工作流处理前端任务，回传 team_report → frontend_result |
| delegate_backend | subworkflow | 调用组长子工作流处理后端任务，回传 team_report → backend_result |
| director_summary | agent | 总监：汇总两份团队报告为最终结论（final_result） |

覆盖节点类型：触发器、Agent、子工作流（三类；子定义内为触发器 + Agent×4）。

> 调度口径：普通节点仅取第一条出边，两次委派以顺序边串联保证都执行；
> 若总监直接引出两条出边，后端委派与总监汇总都不会被执行。
> 映射口径：`input_mapping` 取值必须是父共享状态字段引用，字面量
> （如 `team_type: frontend`）会被当作引用并校验失败；团队方向由
> `team_task` 文本承载。

## 核心验证点

1. **两级嵌套**：主定义 → 组长子工作流（subworkflow_definition_id 解析），嵌套深度 2 未超 `max_nesting_depth: 3`。
2. **上下文传递**：子工作流 `context_strategy: inherit`，父状态经映射注入。
3. **输出映射**：子工作流 `report` 输出映射为父状态 `frontend_result` / `backend_result`。

## 运行方式

```bash
curl -X POST http://localhost:8000/api/v1/instances \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"definition_id": "hierarchical_delegation", "inputs": {"task_description": "开发用户中心模块", "priority": "high"}}'
```

或经范例库服务加载：`ExampleLibraryService().load_example("hierarchical_delegation")`
（子定义 team_lead_subworkflow.yaml 需已登记到平台定义库，运行期按
subworkflow_definition_id 取最新版本）。

## 预期结果

- 前端、后端两次委派先后完成，两份团队报告分别写入 `frontend_result` / `backend_result`；
- 总监汇总执行一次，`final_result` 字段非空。
