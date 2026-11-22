# 数据漂移检测范例（drift_detection）

数据科学范例：检测数据分布漂移并给出结论，演示
「trigger → data_source → conditional_branch → agent → output_display」编排模式，
含基线缺失不误报的默认分支与漂移告警订阅联动配置示例。

## 目录内容

| 文件 | 说明 |
| --- | --- |
| `drift_detection.yaml` | 检测流水线编排定义（spec_version 1.1） |
| `drift_detection_agents.yaml` | 预置 Agent 定义清单（职责/输入输出契约） |
| `drift_alert_subscription.example.json` | 漂移告警订阅示例配置（G6 阈值模板联动） |
| `README.md` | 本说明文档 |

## 编排模式

```
trigger_start → read_current_data → baseline_check ─┬─ 基线已建立 → read_baseline_data → drift_agent ──┐
                                                    └─ 基线缺失（默认分支）→ baseline_missing_agent ──┤
                                                                                                       ↓
                                                                              display_result（检测结论展示）
```

1. **trigger**（manual）：接收 `current_dataset_id` 与可选 `baseline_dataset_id`
2. **data_source**：读取当前数据（基线建立分支另读基线数据）
3. **control_flow（conditional_branch）**：基线存在性检查——
   条件命中走"基线已建立"分支；未命中走 `default_branch_target`
   （`baseline_missing_agent`）。若不设默认分支，"无匹配分支"会直接
   报错终止，"不误报"路径便没有出口
4. **agent**：
   - 漂移检测 Agent（仅基线存在时）：量化 drift_score 并标记 drift_detected
   - 基线未建立结论 Agent（默认分支）：直接产出
     `baseline_established: false` 的结论性输出（不做漂移判定、
     不依赖异常报错表达该状态——不误报）
5. **output_display**：汇聚展示两条分支的结论性输出

> 迁移说明（merge_tool_into_agent_node DC-E / CHG-23）：原结论输出/事件
> 发布/汇聚三个 tool 节点已移除——结论由输出展示节点承接。

## 运行方式

1. 配置 LLM 主模型三变量（同特征工程范例）
2. 通过范例库服务加载：

```python
from orchestration_platform.application.example_library_service import ExampleLibraryService

service = ExampleLibraryService()
definition = service.load_example("drift_detection")
```

## 漂移告警订阅联动（G6 触发器模板）

订阅侧为平台真实能力：**THRESHOLD_SUSTAINED_BREACH** 触发器模板
订阅 `drift_score` 字段（连续 3 次突破阈值才触发，防抖防误报），
示例配置见 `drift_alert_subscription.example.json`。

事件生产侧口径（P4 工具节点移除后）：范例工作流不再内嵌事件发布节点，
漂移结论以命名输出（`drift_analysis` / `baseline_status`）经输出展示节点
产出；如需事件化联动，由数据集成侧（或独立事件生产者）将检测结论写入
订阅声明的事件源（`drift_detection_events`），消息中间件不可用时经内存
降级通道同语义投递。

### 操作步骤

1. 以示例 JSON 为请求体调用
   `POST /api/v1/subscriptions`（既有订阅创建接口）
2. 请求体中的 `template_binding` 字段声明阈值模板：
   `field_name="drift_score"`、`threshold` 按业务设定、`sustained_count=3`
3. 模板在订阅创建时展开：gt 粗筛规则进入既有 `filter_rules` 过滤路径，
   有状态评估器按订阅维度挂载（防抖判定）
4. 事件源 `drift_detection_events` 上 `drift_score` 连续 3 次突破阈值时，
   订阅触发并按 `input_mapping` 投递至告警接收方工作流
5. 不新建告警子系统——完全复用既有实时订阅机制

## 异常场景

- **基线缺失**（异常场景 2）：走默认分支进入"基线未建立"结论 Agent，
  直接产出 `baseline_established: false` 的结论性输出，不做漂移判定、不误报。
- **消息中间件不可用**（异常场景 3）：订阅联动经内存降级通道同语义可用
  （带降级标识）。
- **依赖缺失**：LLM 主模型变量缺失时范例标记不可用
  （EXAMPLE_DEPENDENCY_MISSING 语义）。
