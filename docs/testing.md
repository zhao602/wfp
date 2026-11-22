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

## 端到端测试（Playwright）

### 前置条件

```bash
cd web
npm install
# Playwright 浏览器二进制（单浏览器口径）
npx playwright install chromium
# 官方 CDN 不可达时经 npmmirror 镜像重试：
#   PowerShell: $env:PLAYWRIGHT_DOWNLOAD_HOST='https://cdn.npmmirror.com/binaries/playwright'
```

OEM 产物无需预构建：`05-oem.spec.ts` 检测到 `oem/dist/` 缺失时自动执行
`scripts/build_oem.py`（幂等，已有产物直接复用）。

### 运行命令

```bash
cd web
npx playwright test                        # 全部用例（5 路径）
npx playwright test e2e/01-canvas.spec.ts  # 单路径
npx playwright show-report                 # HTML 报告
```

### 端口与进程隔离

| 进程 | 端口 | 说明 |
| --- | --- | --- |
| e2e 专用后端（uvicorn --factory） | 8330 | 独立存储根与日志目录（`%TEMP%\wfp_e2e_backend`），与开发态 8000 互不污染 |
| vite dev server | 5173 | `strictPort`；proxy `/api` 经 `WFP_E2E_API_TARGET` 指向 8330 |
| OEM 静态服（python http.server） | 8931 / 8932 | default / acmeflow 产物，测试内起停 |

后两个服务由 `playwright.config.ts` 的 `webServer` 自动拉起与回收；
`workers: 1`、`retries: 0`（缺陷不掩盖口径）。

### 路径覆盖

| spec | 路径 | 口径 |
| --- | --- | --- |
| 01-canvas | 画布 DnD→连线→运行→执行状态浮层→重载还原 | UI 全链（含 localStorage 持久化） |
| 02-examples | 范例库载入 sequential_pipeline | UI + 动态范例名（API 解析，防文案漂移） |
| 03-hitl | 人工审核链挂起→审批→终态 | 混合：UI 呈现 + API 提交审批决策 |
| 04-subscriptions | 订阅端点 503 契约 | D1 缺陷锁定（订阅管理器未装配；修复后翻转回 CRUD 断言） |
| 05-oem | 双品牌产物 title/主色/菜单差异 | 生产构建产物 + 静态服 |

### 稳定性与隔离口径

- 本地准入：**连续 10 次 `npx playwright test` 全绿**（时间戳与结果记录于批退出记录）。
- vitest 与 e2e 隔离：`vite.config.ts` 的 `test.exclude` 显式排除 `e2e/**`
  （vitest 基线 1007+13 / 123 文件不受影响）。
- typecheck 13 基线零新增：`web/tsconfig.json` include 仍仅 `src`，`e2e/` 构造性隔离。

### CI 接入步骤（凭据后补批启用）

1. 前置 job 安装 Python + Node 依赖后追加
   `cd web && npx playwright install --with-deps chromium`。
2. 执行 `cd web && npx playwright test`（webServer 自管，无需额外服务）。
3. 产物归档：`web/test-results/`（失败上下文与 trace.zip，`trace: retain-on-failure`）。
4. 门禁：6 用例全绿；04 的 503 契约断言在 D1 修复批同步翻转。
