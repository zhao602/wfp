# 部署指南（deploy.md）

> 目标读者：第一次接触本仓库的部署/运维人员。
> 目标：**10 分钟内**从零起一套 API + PostgreSQL 全栈（AC-01 移交文本）。
> 配套配置：`Dockerfile`（三阶段）、`docker-compose.yml`、`.env.production.example`（生产配置单一事实源）。

---

## 1. 十分钟路径（AC-01）

**前置条件**：Docker ≥ 20 + Compose v2 插件（`docker compose version` 可用）、git、curl。

```bash
# 1. 获取代码
git clone <仓库地址> orchestration-platform && cd orchestration-platform

# 2. 生成生产配置（单一事实源：.env.production.example）
cp .env.production.example .env.production

# 3. 【必改】编辑 .env.production：三项密钥 + 管理员标识（见 §2/§4；不改起不来）
#    - API_ACCESS_TOKEN、POSTGRES_PASSWORD、TOKEN_SIGNING_SECRET → 强随机值
#    - RBAC_BOOTSTRAP_ADMIN_USER → 实际管理员账号
vi .env.production

# 4. 构建并启动（api + postgres）
docker compose --env-file .env.production up -d --build

# 5. 健康检查（/healthz 免鉴权）
curl http://localhost:8000/healthz
# 期望：{"status":"ok"}
```

三条命令失败排查：`docker compose --env-file .env.production logs api`（api 启动失败最常见原因是弱密钥 fail-fast，见 §4）。

> 沙箱口径说明：`docker build` / `docker compose up` 属本轮**禁试清单**（N 类），本指南按配置产物静态核对产出；陌生人路径实测与结果回填属移交项（§8 移交实测清单）。

---

## 2. 首启顺序：先 bootstrap 再开 RBAC（DC-06）

模板默认 `RBAC_BOOTSTRAP_ENABLED=true` 与 `RBAC_ENABLED=true` **同时开启**——这是安全的：bootstrap 在应用工厂装配期（服务对外就绪前）幂等执行（默认租户 → 内置角色 → 可选初始管理员绑定），不会出现「先开门后授权」窗口。

理解要点（排障必需）：

- **仓储空 + RBAC 开 = 全部接口 403**，这是 **fail-closed 正确行为**，不是故障（DC-06 修复语义：宁可拒绝服务也不无授权放行）；
- 若遇全员 403：确认 bootstrap 是否成功执行（启动日志含 RBAC 初始化记录），**修复方式是补跑/重跑 bootstrap（重启 api 即幂等重入），而不是关闭 RBAC**；
- `RBAC_BOOTSTRAP_ADMIN_USER` 留空时只初始化租户与角色，不创建管理员绑定——首个管理员须后续手工授权。

---

## 3. 前端访问

compose 栈内 `api` 服务只提供 API（`:8000`）。前端为纯静态产物，位于镜像 `/app/web/dist`：

- **生产**：由反向代理（nginx / 网关）托管 `dist` 静态文件，并将 `/api` 反代到 `api:8000`；
- **本地开发**：`cd web && npm install && npm run dev`（Vite 开发服务器 5173，已内置 `/api` → `localhost:8000` 代理）。

---

## 4. 上线必改清单（生产三强化 + 三密钥）

### 4.1 三强化项（`ENVIRONMENT` / `TOKEN_VERIFY_MODE` / `CODE_EXECUTION_CHANNEL`）

| 变量 | 生产值 | 说明 |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | 弱密钥 fail-fast（DC-05）+ 生产口径；`development` 仅限本地 |
| `TOKEN_VERIFY_MODE` | `jwt` | 仅 JWT 令牌。**`both`（默认）的明文回退在生产有残留风险**（明文令牌不校验签名/过期），生产必须收窄为 `jwt` |
| `CODE_EXECUTION_CHANNEL` | `subprocess` | 代码节点走子进程通道（2.13 生产语义）。**`inprocess` 仅限本地开发与单测通道，生产禁用**；资源限额沿用既有边界（超时强杀 / 输出上限 / 白名单命名空间）；`code_node_executor` 通道选择与降级告警已内建，非法值自动降级并告警 |

### 4.2 三密钥（占位值在 production 下**拒绝启动**——是特性不是故障）

| 变量 | 要求 |
| --- | --- |
| `API_ACCESS_TOKEN` | 强随机值（≥32 字符） |
| `POSTGRES_PASSWORD` | 强随机值（≥24 字符）；compose 的 postgres 与 api 共用此值 |
| `TOKEN_SIGNING_SECRET` | **≥32 字节随机值**（`openssl rand -base64 32`） |

### 4.3 步数护栏（G6，生产建议开启）

`WORKFLOW_STEP_GUARD_ENABLED=true` + `WORKFLOW_MAX_STEPS`（默认 500）：实例轮次达到限额即 `WF_BUDGET_EXCEEDED` 阻断（防失控工作流无限消耗）；与节点级 `max_iterations`（`WF_LOOP_NOT_CONVERGED`）解耦，先触发先报告。

---

## 5. 密钥保管要求（TOKEN_SIGNING_SECRET 与 postgres_password 同级）

- 三密钥经 `.env.production` 注入（已入 `.gitignore`）；**禁止**提交版本库、**禁止**内嵌 compose/Dockerfile（DC-13 否决项）；
- `TOKEN_SIGNING_SECRET` 保管等级与数据库口令同级：密管系统/保险库存放、最小知悉范围、变更留痕；
- **轮换语义**：JWT 无状态、不落库——更换签名密钥即**全员已签发令牌立即失效**（用户须重新登录/换发）；轮换窗口期可临时用 `both` 模式过渡（明文令牌兜底），窗口结束立即收回 `jwt`。

---

## 6. auth/token 端点信任边界

`AUTH_TOKEN_ISSUANCE_ENABLED=true` 时暴露 `POST /api/v1/auth/token`（JWT 签发）：

- **面向内网 / 可信网关后调用**，不是公网端点；
- 公网部署必须经网关收口：TLS 终结、限流、IP 白名单；
- 签发依赖的 `TOKEN_SIGNING_SECRET` 一旦泄露，攻击者可自行签发任意用户令牌——保管要求见 §5。

---

## 7. 健康检查与运维入口

| 入口 | 说明 |
| --- | --- |
| `GET /healthz` | 免鉴权存活探针（compose healthcheck 使用） |
| `GET /metrics` | 运行指标快照 |
| `docker compose --env-file .env.production logs -f api` | api 日志 |
| `docker compose --env-file .env.production exec postgres pg_dump ...` | 数据库运维 |

数据卷：`postgres-data`（库）、`api-data`（本地文件存储降级落点）、`api-logs`（运行日志）。

---

## 8. 移交实测清单（§8-D5，N/M 类回填）

以下项在本轮沙箱按「配置产出 + 静态核对/本地等价脚本」交付（OP-06），**真实环境实测由用户执行并回填结果**：

| 项 | 移交内容 | 沙箱已做 |
| --- | --- | --- |
| AC-01 陌生人 10 分钟路径 | 按本文 §1 实测 compose up 全栈 + healthz | `docker compose config` 语法核对通过；文档步骤静态核对 |
| AC-03 CI 绿灯 | 推送仓库观察 GitHub Actions 双 job 绿灯 | ci.yml YAML lint；`scripts/run_local_ci.py` 实测 exit 0（lint 兜底 + pytest 收集 2872 + vitest 单文件） |
| docker build / compose up | 真实构建与运行 | 禁试清单 §13.1；Dockerfile 三阶段结构静态核对 |

---

## 9. 常见问题

**Q：api 起不来，日志报弱密钥？**
A：`ENVIRONMENT=production` 下占位密钥（`change-me-*`）命中黑名单即 fail-fast 拒绝启动（DC-05）。按 §4.2 换真实强值后重启。

**Q：全部接口 403？**
A：见 §2——先确认 bootstrap 执行记录，重跑 bootstrap，不要关 RBAC。

**Q：想临时回退到本地开发口径？**
A：`ENVIRONMENT=development`（仅告警不阻断）+ `TOKEN_VERIFY_MODE=both`；生产环境禁止。
