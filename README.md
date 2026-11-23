# wfp —— 企业级多 Agent 工作流编排与治理平台

> **YAML 声明式 + 画布双模编辑、单机可跑、面向私有化、可整嵌**的多 Agent 工作流编排与治理平台。
>
> Apache-2.0（[LICENSE](LICENSE)）｜差异化定位：[docs/differentiation-strategy-2026-11.md](docs/differentiation-strategy-2026-11.md)

## 嵌入 SDK（wfp-sdk）

同步客户端，仅依赖 `httpx`，零平台内部依赖——校验 / 提交 / 触发 / 订阅 / 取消 / 取结果：

```bash
# 从源码构建安装（PyPI 发布 runbook：docs/release-sdk.md）
cd sdk
python -m pip wheel . --no-deps -w dist
python -m pip install dist/wfp_sdk-0.2.0-py3-none-any.whl
```

```python
from wfp_sdk import WorkflowClient

YAML = "..."  # 工作流 YAML 文本（spec_version 1.0/1.1）

with WorkflowClient("http://127.0.0.1:8310") as client:
    report = client.validate_definition(YAML)      # 1 校验（恒返报告）
    client.submit_definition(YAML)                 # 2 提交定义
    info = client.start_instance("wf_demo", YAML, inputs={"query": 1})   # 3 触发执行
    for event in client.stream_events(info.instance_id):                 # 4 SSE 订阅
        print(event.event_type)
    client.wait_for_completion(info.instance_id)   # 5 等待终态
    print(client.get_results(info.instance_id).outputs)                  # 6 取结果
```

9 方法面 / 同构错误家族 / 租户鉴权契约见 [sdk/README.md](sdk/README.md)；
冻结契约见 [docs/sdk-contract.md](docs/sdk-contract.md)。免 LLM 端到端演示：
`sdk/demo/demo.py`。

## 项目定位

以 **YAML 声明式工作流契约为核心**（YAML 即接口：可校验、可 diff、可迁移），配合 **React Flow 可视化画布**双向编辑，覆盖「编排—执行—治理—生态」四层能力面。单机零外部依赖可跑通全链路（local_file 存储 + SQLite 级部署复杂度），面向私有化场景交付。

## 四层能力面

| 层 | 能力 | 主要落点 |
| --- | --- | --- |
| **编排** | YAML 声明式定义（spec_version v1.0/1.1 兼容解析 + 拓扑校验，[1.x 只增不删承诺](docs/spec-versioning.md)）、画布双向编辑、14 类节点、自然语言编排、范例库（12 套 / 16 个 YAML） | `web/src/canvas/`、`src/orchestration_platform/workflow/`、`examples/` |
| **执行** | 图状态机引擎：**单游标确定性调度 + 并行扇出区域**（`wait_all / wait_any / n_of_m` + 失败游标恢复，范例 [examples/parallel_execution/parallel_fanout.yaml](examples/parallel_execution/parallel_fanout.yaml)）、检查点恢复、回滚与重放、SSE 执行可视化、HITL 人工审批、定时/手动触发 | `src/orchestration_platform/engine/`、`web/src/execution-visualization/` |
| **治理** | RBAC + ABAC、多租户、审计、[计量计费与用量对账](docs/metering-quota-guide.md)、治理驾驶舱、评估、墓碑标记、沙箱策略；SSO 企业身份为 **experimental**（roadmap S2） | `src/orchestration_platform/api/routers/governance/`、`web/src/governance/` |
| **生态** | Agent/Tool 市场、贡献与开发提交流、插件节点 SDK、企业连接器、Agent 发现、向量知识库 | `src/orchestration_platform/api/routers/`、`web/src/market/` 等 |

## 快速上手

```bash
# 1. 配置环境变量（示例值均为占位符，按实际环境替换）
cp .env.example .env

# 2. 启动后端（Python 3.10+）
pip install -e ".[test]"
uvicorn orchestration_platform.api.application_factory:create_app --factory --host 0.0.0.0 --port 8000
curl http://localhost:8000/healthz

# 3. 启动前端
cd web
npm install
npm run dev   # http://localhost:5173，API 请求自动代理至 :8000
```

Docker Compose 一键形态（api + postgres）见 [docs/deploy.md](docs/deploy.md) 与 `docker-compose.yml`。

关键环境变量（完整开关矩阵见 [docs/config.md](docs/config.md)）：

| 环境变量 | 说明 | 默认值 |
| --- | --- | --- |
| API_HOST / API_PORT | API 监听地址 / 端口 | 0.0.0.0 / 8000 |
| API_ACCESS_TOKEN | 接口访问令牌 | 占位符（部署前必改） |
| STORAGE_BACKEND | 存储后端 | local_file |
| LLM_PRIMARY_BASE_URL / _API_KEY / _MODEL | 主模型接入 | 占位符（需配置） |

## 双运行时与边界（如实声明）

- 内置图状态机引擎为主运行时；LangGraph 为**严格子集**适配（单源 node 语义，
  `langgraph_runtime_enabled` 开关默认关闭）——13 例等价矩阵与子集外五类场景
  见 [docs/langgraph-equivalence-matrix.md](docs/langgraph-equivalence-matrix.md)。
- resume/rollback/replay 等子集外方法级声明与 SSO experimental 核对见
  [docs/dual-runtime-boundaries.md](docs/dual-runtime-boundaries.md)（B8e，A5）。
- PostgreSQL / Redis 未配置时降级 local_file / 内存实现；postgres profile 依赖
  缺失时显式报错、不静默降级。
- 工作流定义规范版本治理（`spec_version` 1.x 只增不删三承诺）见
  [docs/spec-versioning.md](docs/spec-versioning.md)。

## 前端结构要点

- **画布配置面板**：`web/src/canvas/NodeConfigurationPanel.tsx`（调度壳）+ `web/src/canvas/panels/`——12 个节点类型子表单目录（trigger / agent / tool / data_source / control_flow / subworkflow / dynamic_injector / data_transform / output_display / vector_knowledge_base / llm / code）与 `panels/shared/` 共享基元（Field / PanelIcon / panelStyle / OutputContractSection）。
- **API 客户端层**：15 个领域客户端统一经 `web/src/api/httpClient.ts` 基元（token 注入 / 错误映射 / 超时 / 重试幂等限定默认关闭），错误对象统一 `ApiError` 族（分层：transport / auth / tenant / business / validation）。
- **管理后台**：18 个面板 4 组（画布与编排 / 安全与合规 / 生态与扩展 / 评估与发现）。

## 测试

```bash
# 后端（单元 + 集成，全量 exit 0 为门禁口径；最近实测 3490 collected，2026-11-23）
PYTHONPATH=src python -m pytest

# 前端（逐文件运行）
cd web && npx vitest run <测试文件路径>
npx tsc --noEmit   # 类型检查（13 基线错误为已知存量，口径零新增）
```

详见 [docs/testing.md](docs/testing.md)。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/yaml-spec.md](docs/yaml-spec.md) | 工作流定义 YAML 规范（声明式契约） |
| [docs/spec-versioning.md](docs/spec-versioning.md) | 规范版本治理（HC-02 兼容承诺 / 1.x 只增不删） |
| [docs/sdk-contract.md](docs/sdk-contract.md) | 嵌入 SDK 契约（冻结） |
| [docs/config.md](docs/config.md) | 配置开关矩阵与环境变量映射 |
| [docs/api-reference.md](docs/api-reference.md) | API 端点清单（路径前缀 / 认证要求 / 错误码口径） |
| [docs/deploy.md](docs/deploy.md) | 部署指南（源码 / Docker Compose） |
| [docs/benchmark.md](docs/benchmark.md) | 性能基线（四基准 / 两遍法口径 / 复现命令） |
| [docs/repo-release.md](docs/repo-release.md) | 仓库推送与发布 runbook（remote / tag / CI） |
| [docs/testing.md](docs/testing.md) | 测试运行说明 |
| [docs/langgraph-equivalence-matrix.md](docs/langgraph-equivalence-matrix.md) | 双运行时等价矩阵与边界（内置引擎 × LangGraph） |
| [docs/dual-runtime-boundaries.md](docs/dual-runtime-boundaries.md) | 双运行时边界与能力声明（子集外方法级声明 / SSO experimental 核对） |
| [docs/metering-quota-guide.md](docs/metering-quota-guide.md) | 计量配额闭环（执行级记账 / 429 超配额契约 / 用量对账） |
| [docs/architecture-review-2026-11.md](docs/architecture-review-2026-11.md) | 架构评审基线（2026-11） |
| [docs/differentiation-strategy-2026-11.md](docs/differentiation-strategy-2026-11.md) | 差异化定位与竞争策略 |
| [docs/project-status-and-roadmap-2026-11.md](docs/project-status-and-roadmap-2026-11.md) | 项目状态与 B 批次路线图 |
| [docs/roadmap-v3-longterm-execution-2026-11.md](docs/roadmap-v3-longterm-execution-2026-11.md) | V3 长期执行方案与实现路径（B9–B16 批次化） |
| [docs/phase4_ecosystem_summary.md](docs/phase4_ecosystem_summary.md) | 生态阶段能力摘要 |
