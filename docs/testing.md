# 测试运行说明

## 后端测试（pytest）

### 前置条件

```bash
pip install -e ".[test]"
```

### 运行命令

```bash
# 全部测试
pytest

# 仅单元测试
pytest tests/unit/

# 仅集成测试
pytest tests/integration/

# 带覆盖率
pytest --cov=orchestration_platform

# 详细输出
pytest -v

# 运行特定测试
pytest tests/unit/test_yaml_workflow_parser.py::TestYamlWorkflowParser::test_parse_valid_sequential
```

### 测试分层

| 层 | 目录 | 覆盖验收 | 关键用例 |
| --- | --- | --- | --- |
| 单元 | `tests/unit/` | A1, A2, A3, B1, B7 | YAML 解析、拓扑校验、调度确定性、控制流执行、仓储一致性、框架隔离 |
| 集成 | `tests/integration/` | A3, A4, A5, A6, A8, A9, A13 | 分支执行、循环执行、检查点恢复、回滚重放、MCP 集成、范例可运行性 |

### 关键用例说明

- **test_yaml_workflow_parser**：验证 5 个合法 fixture 解析通过、11 个非法 fixture 被拒绝
- **test_graph_topology_validator**：验证环检测、含循环节点的合法环、不连通图
- **test_scheduler_determinism**：验证相同输入两次执行路径一致、调度开销 < 50ms
- **test_control_flow_node_executor**：验证条件分支唯一路径、默认分支、循环终止与上限
- **test_repository_implementations**：验证事件序号严格递增、检查点保存/加载一致
- **test_no_direct_framework_dependency**：扫描源码断言除 engine/adapter/ 外无 langgraph 导入
- **test_checkpoint_recovery**：验证中断后恢复、已成功节点不重复执行
- **test_rollback_and_replay**：验证回滚状态一致、事件标记作废、严格重放无外部调用
- **test_example_workflows_runnable**：验证三个预置范例均可解析与编译

## 前端测试（Vitest）

### 前置条件

```bash
cd web
npm install
```

### 运行命令

```bash
# 运行全部测试
npm test

# 监听模式
npm run test:watch

# 类型检查
npm run typecheck
```

### 关键用例说明

- **canvasToYamlExporter.test**：导出满足五要素、导出后导入结构一致
- **yamlToCanvasImporter.test**：导入成功还原、导入失败返回错误
- **ConnectionValidator.test**：构成环的连线被拒绝、合法连线被接受
