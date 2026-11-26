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

## 8. 移交实测清单（§8-D5，N/M 类回填；B15 批内实测回填）

原口径：沙箱按「配置产出 + 静态核对/本地等价脚本」交付（OP-06），**真实环境实测由用户执行并回填结果**。
B15 批内解除 docker 类禁试（用户裁决 2026-11-25）后，docker 项已在本沙箱实测并回填如下：

| 项 | 移交内容 | 沙箱已做 |
| --- | --- | --- |
| AC-01 陌生人 10 分钟路径 | 按本文 §1 实测 compose up 全栈 + healthz | `docker compose config` 语法核对通过；**B15 实测**：compose up 全栈多轮（S5 离线 drill Run C [1/6]–[6/6]、S4 备份恢复 drill），healthz 200 |
| AC-03 CI 绿灯 | 推送仓库观察 GitHub Actions 双 job 绿灯 | ci.yml YAML lint；**B15 新增 docker job**（构建+SBOM+trivy 基线门禁）；`scripts/run_local_ci.py` 实测 exit 0（lint 兜底 + pytest 收集 2872 + vitest 单文件） |
| docker build / compose up | 真实构建与运行 | **B15 实测**（禁试已解除）：镜像构建 237870121B、SBOM/trivy 基线过门；离线交付 drill 全绿（`_drills/b15_offline_drill.md`）；备份恢复 drill 全绿（`_drills/b15_restore_drill.md`，全灭态 RTO 17.6s） |

---

## 9. 常见问题

**Q：api 起不来，日志报弱密钥？**
A：`ENVIRONMENT=production` 下占位密钥（`change-me-*`）命中黑名单即 fail-fast 拒绝启动（DC-05）。按 §4.2 换真实强值后重启。

**Q：全部接口 403？**
A：见 §2——先确认 bootstrap 执行记录，重跑 bootstrap，不要关 RBAC。

**Q：想临时回退到本地开发口径？**
A：`ENVIRONMENT=development`（仅告警不阻断）+ `TOKEN_VERIFY_MODE=both`；生产环境禁止。

---

## 10. Worker 拓扑与接管语义（M2，api + worker + postgres 三服务）

### 10.1 拓扑

`docker-compose.yml` 含三个服务：`api`（HTTP 入口）、`worker`（执行进程）、`postgres`。
worker 与 api 同一镜像，仅 command 覆写为 `python -m orchestration_platform.worker`，
不暴露端口。`EXECUTION_MODE=worker` 写在 `.env.production`（两服务共享）：
api 只入队，worker 认领执行。`EXECUTION_MODE=in_process`（默认）时 api 进程内执行，
worker 服务空转（认领不到作业），历史行为不变。

### 10.2 接管语义（at-least-once，诚实口径）

- **租约与心跳**：worker 认领作业即持租约（`EXECUTION_QUEUE_LEASE_SECONDS`，
  默认 30s），执行期按 `EXECUTION_QUEUE_HEARTBEAT_SECONDS`（默认 10s）续租。
- **kill -9 / 容器被杀**：无任何清理，租约到期后任意存活 worker 经
  `take_over_expired` 接管；续跑点 = 该实例最新检查点（调度器逐节点写检查点），
  **重放范围仅被杀时刻执行中的单节点**；死亡早于首个检查点时从初始输入整段重放。
- **优雅停机（SIGTERM，滚动升级）**：停止认领，宽限期
  （`EXECUTION_QUEUE_GRACEFUL_SHUTDOWN_SECONDS`，默认 30s）内等当前作业完成；
  超时停止心跳续租（不取消运行中任务），租约到期由其他 worker 接管。
- **诚实口径**：本语义为 at-least-once（接管重放单节点可能重复执行一次），
  非 exactly-once；业务节点需自带幂等。

### 10.3 读穿透与已知边界

worker 模式实例不在 api 进程内存注册表，api 侧按以下口径回读：

| 能力 | 口径 |
| --- | --- |
| `GET /instances/{id}`、SSE `/events/stream` | 注册表未命中 → 事件仓储回读（worker 双写落账）；**M5 可见窗**：提交即落 `INSTANCE_QUEUED` 事件，提交→认领窗口期推导 **pending**（不再 404，轮询间隔默认 1s） |
| `/cancel` | **M3 已支持 worker 模式**（读穿透三态，见 10.4） |
| `/events` 列表 | **M5 已支持**：注册表未命中 → 统一读取器回读事件仓储（归一化形态与注册表一致：`NODE_SUCCEEDED`→`NODE_COMPLETED`、payload dict） |
| `/results` | **M5 已支持**：事件推导快照——非终态 `outputs: null`（轮询友好）；succeeded 输出取检查点快照 `node_outputs`（INV-08 唯一权威输出存储；检查点仓储不可达/无快照回退 `{}`）；其余终态 `{}`；`finished_at` 取快照留痕时刻 |
| `/recover` | **M5 已支持**：快照守卫 + 检查点续跑作业入队（幂等键 `{instance_id}:recover:{checkpoint_id}`、`trigger_source=recover`、origin 作业载荷 yaml/版本/租户/trace 补齐）。守卫：running → 409 仍在运行；**waiting_review → 409 请提交审核决策、review_timeout → 409（安全优先有意分歧——in_process 可重挂起/门续跑，worker 无法代持注册表挂起态，且从门检查点续跑 = 绕过已留痕审核决策）**；pending 窗口无检查点 → 500「无可用检查点」（从未运行的如实口径）；执行队列/检查点仓储不可用 → 500 |
| `/review` | **M5 已支持**：快照守卫（非 waiting_review → 409 `INSTANCE_NOT_WAITING_REVIEW` 同文案）+ 编译重建（origin 作业 yaml 优先，工作流仓储 (definition_id, version) 回退）+ `ReviewDecisionService` 复用（决策留痕/终态映射零重复实现）。approve → 服务留痕 `STATUS_CHANGED(running)` 后入队续跑作业（幂等键 `:review-resume:` 前缀、`trigger_source=review_resume`；approve 前置队列可用性检查，防「running 但无续跑作业」悬挂态）；reject → `cancelled`（`review_timeout_action=wait` → `failed`）；timeout_action → `review_timeout` |
| `/checkpoints`、`/rollback`、`/replay` | **注册表口径**（worker 模式实例未注册 → 404；W5–7 桩现状冻结，Z-4），M5 未适配 |

**配额 worker 口径（M5）**：worker 模式并发配额计数走队列落库口径
（`count_active_by_tenant`：pending/running 作业数，local/postgres 双后端）；
count→check→enqueue 跨 await 存在准入竞态（best-effort 诚实口径，与
in_process 窗口同类）；队列不可达降级注册表计数（worker 模式注册表恒空
≈ 0，配额近似旁路——告警可见）。

postgres 后端事件读侧 payload 丢失已于 M5 修复（`_decode_stored_event_payload`：
asyncpg 未配置 JSONB codec 时 payload 列为全量事件 dump 字符串，解内层
payload 键）；作业 `initial_inputs` JSONB 读侧同步修复（`_decode_jsonb_dict`）。
快照 `definition_id` 两后端一致。

### 10.4 实例取消与超时语义（M3）

**取消（`POST /instances/{id}/cancel`）**：

- in_process 模式：注册表内取消（M1 语义不变）。
- worker 模式：读穿透三态——
  1. **活跃作业存在**（pending/running）：置 `cancel_requested` 标志；
     running 时取消时延上界 = 心跳间隔（默认 10s，
     `EXECUTION_QUEUE_HEARTBEAT_SECONDS`），worker 心跳发现标志 →
     抢占式取消执行 + 协作式节点边界检查兜底；pending 时认领即收口
     不执行。
  2. **已终态/waiting_review**：事件可见 → 409 `INSTANCE_CANCEL_CONFLICT`；
     不可见 → 404。
  3. **盲区可见性**：pending 取消在 api 侧落 `INSTANCE_CANCELLED` 事件
     （GET/SSE 立即可见）；running 取消由 worker 落事件；认领/接管时
     若发现标志但端点未落账，幂等补落（查重后写）。
- 取消终态为 `cancelled`，作业照常 complete（不留僵尸 running）。

**实例级看门狗**：

- `INSTANCE_TIMEOUT_SECONDS`（默认 1800，支持小数秒）——实例执行墙钟
  上界，in_process/worker 两模式同点生效；超时终态 **failed**（非
  cancelled）+ 失败码 `WF_INSTANCE_TIMEOUT`（载荷含 `timeout_seconds`），
  作业照常 complete。

**租约调整注意事项（D9，M5 已收口）**：

- `IExecutionQueue.heartbeat()` 增可选 `lease_ttl_seconds` 参——
  worker 心跳按自身租约续传（claim/take_over/heartbeat 全链 TTL
  一致传递），**不再被心跳覆盖回队列全局默认**。
- **生产调整租约**：优先调 worker 构造参数或
  `EXECUTION_QUEUE_LEASE_SECONDS` 环境变量（全链一致缺省）。
- **旧签名兼容**：未升级的队列实现（双参 `heartbeat`）仍可运行——
  worker 构造期一次签名探测决定调用形态，协议演进不破坏既有部署
  （探测含 `**kwargs` 型实现）。

---

## 11. 备份与恢复（B15 S4，`scripts/ops/backup.py` / `restore.py`）

### 11.1 备份

```bash
python scripts/ops/backup.py --project <compose项目名> [--keep N]
# 产出 backups/<UTC时间戳>/：
#   db.dump          pg_dump -Fc（custom 格式，与 pg_restore 配对）
#   api-data.tar.gz / api-logs.tar.gz   两卷 tar（alpine:3.20 digest 锁辅助容器）
#   manifest.json    schema=backup-manifest/1：各件 sha256/尺寸、项目名、
#                    pg_dump 版本、created_at（备份为时点产物，含时间戳）
#   SHA256SUMS       全部文件校验和（含 manifest 自身；SHA256SUMS 自身除外）
```

- **一致性口径**：DB 走逻辑转储（pg_dump），postgres-data 卷**不做**物理备份；
  api-data/api-logs 卷 tar 物理归档。
- **实现纪律**：pg_dump/tar 均走容器 stdout 二进制管道落盘，**不做宿主目录
  bind mount**——`DOCKER_HOST=tcp://…` 裸穿、npipe、Linux 任意拓扑下
  Windows 风格路径均无翻译保障（B15 实测 `invalid mode: /out` 根因）。
- `--keep N` 按目录名 UTC 时间戳清理旧备份（缺省不清）。

### 11.2 恢复

```bash
python scripts/ops/restore.py --from backups/<时间戳> --project <项目名> \
    --env-file .env.production --confirm
```

- **双闸**：`--confirm` 必须（缺省仅打印计划）；manifest.project 与
  `--project` 不一致即中止（防跨项目恢复）；校验和任一坏行即中止。
- **步骤（各自计时）**：校验和 → compose down（保卷）→ 卷 tar 解回
  （stdin 管道，清后还原）→ postgres 单服务起 + healthy 闸 →
  `pg_restore --clean --if-exists` → `up -d --no-build` → healthz 200
  （探活强制直连 `ProxyHandler({})`，防目标机 docker client 代理劫持，
  见 §12.5）。
- **两条恢复路径均覆盖**：既有库（`--clean --if-exists` 按对象替换）与
  全新库（down -v 后 fresh initdb，pg_restore 全量重建）。

### 11.3 RTO 实测（b15_restore_drill.md，2026-11-25）

全灭态（down -v：容器/网络/三卷全删，等价新机）→ healthz 200：

| 分项 | 实测 |
| --- | --- |
| compose down（幂等） | 0.5s |
| 卷 tar 解回 ×2 | 2.8s |
| postgres 起 + healthy | 8.3s |
| pg_restore（--clean --if-exists） | 1.2s |
| api/worker up --no-build | 1.6s |
| healthz 200 | 3.2s |
| **总计** | **17.6s** |

恢复后核验逐位一致：3 版本行（v3 published、published_at 精确到微秒回放）、
schema_versions=10、API versions 端点与备份前完全相同。

---

## 12. 升级 runbook（B15 S4）

### 12.1 schema 迁移程序（无 python runner，手工 psql=「runner」）

```bash
# 容器内落地迁移文件后（docker cp 防嵌套路径）：
psql -v ON_ERROR_STOP=1 -q -f /tmp/schema/table_definitions.sql
for f in $(ls /tmp/schema/v1_*.sql | grep -v rollback | sort -V); do
  psql -v ON_ERROR_STOP=1 -q -f $f
done
```

- **顺序纪律**：`sort -V` 防字典序（v1_10 < v1_2）；`grep -v rollback`
  防回滚脚本混入（`v1_*.sql` 通配同时命中 9 个回滚文件）。
- 迁移清单与「无回滚即不可逆」原则见
  `src/orchestration_platform/persistence/postgres/schema_migrations/README.md`
  （v1_4 无回滚；v1_5 不重跑 v1_2）。

### 12.2 v1_10 回滚往返语义（S4 drill 实证，b15_restore_drill.md）

- **回滚**（v1_10_rollback.sql）：DROP INDEX/COLUMN IF EXISTS ×4——
  列/索引净除、**数据行存活**、schema_versions 登记保留（历史留痕）。
- **re-apply**（v1_10_workflow_publish_state.sql）：列/索引回建幂等，
  既有行 status 回填 `'draft'`——**发布态/溯源数据丢失**（脚本头数据警告），
  schema_versions `ON CONFLICT DO NOTHING`（INSERT 0 0）。
- **⚠ 同版本 re-publish 静默 no-op 陷阱**：publish 幂等检查读
  `definition_body` JSON（get 读 body 为权威），回滚+re-apply 后
  body 残留 `status=published`、列已被重置 `draft` → 对既有版本调
  publish 返回**旧 published_at 且列不动**（API 面看似成功、
  `get_published`（列谓词）仍为 None=执行入口解析不到已发布版）。
  **恢复发布态须走新版本**：save 新版本（v(max+1)，body 为 draft）→
  publish 该版本 → 列/body 双写收敛（drill 实证 v3：新时间戳、
  列 published）。
- **守卫真源分裂说明**：publish 幂等读 body、save 不可变守卫
  （ON CONFLICT WHERE status<>'published'）读列——两真源在迁移
  往返后短暂分歧，上述「新版本重发」程序为收敛路径。

### 12.3 镜像与 digest 更新

- 基础镜像 digest 四锁（design D2）：python:3.10-slim / node:20-slim /
  alpine:3.20 / postgres:16-alpine（全量 sha256 见
  `docker-compose.yml`、`Dockerfile`、`scripts/ops/*.py` 内引用）。
- **在线升级**：更新 compose 内 digest → `docker compose pull` →
  `up -d --no-build` → healthz → 按 §12.1 补迁移。
- **离线升级**：目标机 `docker load -i images/*.tar`（install-offline.sh
  已含）→ 替换 compose/manifest → `up -d --no-build`。离线包 compose
  副本不带 digest 后缀（docker save/load 不恢复 RepoDigests，
  provenance 由 manifest.json images[].digest + SHA256SUMS 内容锁承载）。
- compose 文件**不写** `pull_policy: never`（锁死在线升级通道）；
  离线语义由 install-offline.sh 的 `up --pull never` 携带。

### 12.4 schema 初始化（fresh 部署）

fresh postgres-data 卷首次启动：postgres healthy 后按 §12.1 全量应用
table_definitions + 10 前向迁移 → `up -d --no-build` 全量起 →
healthz 200 → 按 §11.3 口径核验 schema_versions=10。
**未初始化 schema 先起 worker**：`UndefinedTableError: execution_job`
重启循环为已知态（schema 应用后自愈），非缺陷。

### 12.5 目标机代理陷阱（S5 缺口② 实证）

目标机 docker client 配置（`~/.docker/config.json` proxies 段）会在
compose up 时向容器**自动注入 http_proxy 系六变量**；容器内
代理感知客户端访问 `127.0.0.1` 可能被路由至不可达代理（实测
healthcheck 43 连败假 unhealthy，外探 200）。已有防护：
compose healthcheck `ProxyHandler({})` 强制直连；install-offline.sh
curl `--noproxy '*'`。**运维侧**：目标机 noProxy 应含
`127.0.0.1,localhost`，或清理 client proxies 段。
