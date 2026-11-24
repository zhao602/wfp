# 开源组件说明（第三方依赖清单）

> 适用版本：`orchestration-platform` / `wfp-sdk` **v0.3.0**、前端 `orchestration-platform-web` **v0.3.0**。
> 生成基准：`pyproject.toml`（后端）、`sdk/pyproject.toml`（嵌入 SDK）、`web/package.json`（前端）。
> 各组件许可证以官方仓库声明为准，本文用于选型说明与合规自查；实际安装版本以 `pip` / `npm` 解析结果为准（前端受 `web/package-lock.json` 锁定）。

---

## 1. 后端运行时组件（pyproject.toml `dependencies`）

| 组件 | 版本约束 | 许可证 | 用途 |
|---|---|---|---|
| FastAPI | ≥0.110, **<0.136** | MIT | Web 框架：路由、依赖注入、SSE 推送底座 |
| Uvicorn[standard] | ≥0.27 | BSD-3-Clause | ASGI 服务器（后端启动入口） |
| Pydantic | ≥2.6 | MIT | 领域模型与工作流定义校验（v2 `model_validate`） |
| pydantic-settings | ≥2.2 | MIT | 类型化配置（`get_settings()` 单例） |
| typing-extensions | ≥4.6 | PSF-2.0 | 跨版本类型兼容 |
| PyYAML | ≥6.0 | MIT | YAML 工作流定义解析（`yaml.safe_load`） |
| LangGraph | ≥0.2 | MIT | 底层编排引擎（**仅** `engine/adapter/` 隔离层可 import） |
| httpx | ≥0.27 | BSD-3-Clause | 异步 HTTP 客户端：MCP、LLM、数据源连接器 |
| psycopg[binary] | ≥3.1 | LGPL-3.0 | PostgreSQL 同步驱动（见 §6 合规说明） |

**版本约束备注**：
- `fastapi <0.136`：上界防御。starlette 1.x 移除 `add_event_handler`，与 `application_factory` 的 7 处调用点不兼容；门禁实测 0.135.0 + starlette 0.52.1。
- LangGraph 经 `engine/abstractions/` + `engine/adapter/` 抽象隔离，更换/移除该依赖不影响业务代码。

## 2. 前端运行时组件（web/package.json `dependencies`）

| 组件 | 版本约束 | 许可证 | 用途 |
|---|---|---|---|
| react / react-dom | ^18.3.1 | MIT | UI 框架（StrictMode 受控组件架构） |
| reactflow | ^11.11.4 | MIT | 可视化画布（节点/连线/环检测） |
| yaml | ^2.5.1 | ISC | 浏览器端 YAML 导入/导出（画布 ↔ YAML 往返） |

## 3. 可选 / 惰性导入组件（未列入主依赖，按 profile 安装）

| 组件 | 许可证 | 启用方式 | 用途 |
|---|---|---|---|
| asyncpg | Apache-2.0 | `pip install -e ".[postgres]"` + `STORAGE_BACKEND=postgres` | PostgreSQL 异步仓储 |
| xmlsec | MIT | `pip install -e ".[sso]"` | SAML/OIDC 断言验签（需系统级 libxmlsec1） |
| PyJWT | ≥2.8, MIT | `pip install -e ".[sso]"` | SSO 令牌处理 |
| lxml | BSD-3-Clause | `pip install -e ".[sso]"` | SSO 报文解析 |
| aiokafka | Apache-2.0 | 运行时惰性导入 | Kafka 事件源（企业事件触发场景） |
| redis | MIT | 运行时惰性导入 | 缓存 / 分布式状态 |
| cryptography | Apache-2.0 | 运行时惰性导入 | 加密与签名能力 |

## 4. 开发与测试工具（不影响运行时分发）

| 组件 | 端 | 许可证 | 用途 |
|---|---|---|---|
| pytest / pytest-asyncio | 后端 | MIT / Apache-2.0 | 单元、集成、契约与性能测试 |
| ruff | 后端 | MIT | Lint（line-length 120，target py311） |
| setuptools / wheel | 后端 | MIT | src 布局构建 |
| TypeScript | 前端 | Apache-2.0 | strict 类型检查 |
| Vite + @vitejs/plugin-react | 前端 | MIT | 开发服务器与构建（dev 端口 5173，`/api` 代理至 8000） |
| Vitest + @testing-library/react + jsdom | 前端 | MIT | 组件测试 |
| @playwright/test | 前端 | Apache-2.0 | E2E 测试 |

## 5. 嵌入 SDK（sdk/，独立发布包 wfp-sdk）

- **仅依赖 httpx**（BSD-3-Clause），零 `import orchestration_platform`，不泄漏内部模块。
- 从 `sdk/` 目录构建：`python -m pip wheel . --no-deps -w dist`。

## 6. 许可证合规说明

- 本项目以 **Apache-2.0** 发布（见根目录 `LICENSE`）。
- 主依赖中 MIT / BSD-3 / Apache-2.0 / ISC / PSF-2.0 均与 Apache-2.0 兼容，可自由分发与商用。
- **唯一例外：psycopg（LGPL-3.0）**。LGPL 对"修改库本身"要求开放源码；作为独立库被应用动态链接（pip 安装二进制轮子即属此形态）不影响应用闭源。若需彻底规避，可切换 asyncpg（Apache-2.0）路线或自行从源码静态链接满足 LGPL 义务。
- 二次分发时注意：保留各组件版权与许可证声明；`pyproject.toml` 已声明 `Private :: Do Not Upload`，主包禁止上传 PyPI；`wfp-sdk` 发布前需回填 `[project.urls]`（见 `sdk/pyproject.toml` 注释）。

## 7. 一键安装与本清单的关系

- 运行根目录 `install.bat`（Windows）或 `./install.sh`（Linux/macOS）：
  - `core`（默认）= §1 + §2；
  - `test` = core + §4 的 pytest / pytest-asyncio；
  - `all` = core + §3 的 postgres / sso + §4 测试工具（sso 需系统级 libxmlsec1）；
  - `check` = 仅检查 Python ≥3.10、Node ≥18 与 `.env` 状态，不安装。
- 前端依赖由 `web/package-lock.json` 锁定，脚本优先 `npm ci` 保证可复现。
