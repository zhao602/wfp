# B15-S3 Docker 构建与 compose 运行 drill 记录

- 批次：roadmap_b15_delivery_hardening（S3 交付加固：镜像/编排/供应链）
- 日期：2026-11-25（19:00–21:10 窗口）
- 环境：Windows Docker Desktop（WSL2 后端）；构建网络受限（容器内直连
  pypi/files.pythonhosted.org 读超时，注入镜像源后可达）；npm 直连正常
- 隔离：compose 项目 `b15-s3`（独立于开发环境；host 端口 8331 限环回）
- 权威工作区：gate worktree `%TEMP%\deveco\b15_gate`（detached 3c1b4f1，
  同步 B15 文件后 drill）

## 1. 目标与结论

**目标**：digest 锁基镜像 + compose 三服务拓扑（api+worker+postgres）真实起栈；
read_only/tmpfs 运行时加固实证；PG 写路径 + JWT 链 + 迁移链全绿；供应链缺口清点。

**结论**：全绿（§5 终态证据）。抓出并修复供应链缺口 **4 项**（§3）：镜像内容物级
缺陷 2 项（asyncpg 缺失、schema SQL 被打包器丢弃）、compose 构建网络缺陷 1 项
（PIP 源不可注入）、compose 双 env 通道裂脑 1 项（部署语义陷阱）。

## 2. 镜像演化链（三次构建）

| # | image ID | 大小 | 构建方式 | 关键状态 | 结局 |
|---|---|---|---|---|---|
| 1 | bf876af… | 226MB | 手工 docker build（aliyun 源） | 裸 `.` 安装：**asyncpg 缺失**；无 package-data | 被超越 |
| 2 | b64f5f5fc8a3 | 238MB | compose build（tuna 源） | `'.[postgres]'` 修复：asyncpg 0.32.0 镜像内实证；**schema SQL 仍缺** | 被超越 |
| 3 | **9ab3314c53f7** | **237870121B** | compose build（tuna 源） | **+package-data：schema SQL/json 随镜像实证** | **终版** |

- 构建上下文 77.3kB（.dockerignore 白名单生效）
- pip 步耗时：tuna **37.4s** vs aliyun **615s**（aliyun 大量下载后疑限流，
  Read timed out ~110s exit 2；均为容器内直连）
- 终版镜像内容物：`docker cp` 从 api 容器取 `table_definitions.sql` 成功
  （4157B 往返）；`schema_migrations/` 21 项（v1_1–v1_10 前向 10 + 回滚 9
  （v1_4 无回滚为历史已知）+ README）；`workflow/yaml_schema/
  workflow_definition_v1.schema.json` 在位

## 3. 供应链缺口四连（本 drill 核心产出）

### 缺口①：postgres extra 未装（worker 必崩）

- 症状：worker `ModuleNotFoundError: No module named 'asyncpg'`（api 不受影响，
  仅 worker 走 postgres 队列池）
- 根因：Dockerfile `pip install ... .` 裸装 main 依赖；asyncpg 在
  `[project.optional-dependencies] postgres`（base 仅 psycopg[binary]>=3.1）
- 修复：Dockerfile 改 `'.[postgres]'`
- 验证：镜像内 import asyncpg 成功（0.32.0）；worker 起栈后安静轮询
- 备注：`[sso]` extra 不装——`jwt_tokens.py` 纯 stdlib（base64/hmac/json）手写
  HS256，无 PyJWT 依赖

### 缺口②：compose build 默认源不可用（受限网络构建必败）

- 症状：`docker compose build` exit 1，默认进度模式吞掉 pip 报错细节
- 诊断：隔离诊断 build（-p b15-s3-diag）定谳——默认 PIP_INDEX_URL 官方源 →
  BuildKit 层缓存未命中 → 真跑 pip → files.pythonhosted.org 读超时 exit 1
- 修复：compose api/worker `build.args: PIP_INDEX_URL:
  ${PIP_INDEX_URL:-https://pypi.org/simple}`（默认不变；受限网络
  `export PIP_INDEX_URL=...` 注入）
- 验证：tuna 源 compose build 成功（pip 步 37.4s）
- 附注：`--progress` 为 compose 全局旗标（`docker compose --progress plain
  build`），置于 build 后会被警告忽略

### 缺口③：setuptools 默认丢弃非 .py 包数据（镜像无 schema，无法自举建库）

- 症状：`docker cp <api容器>:.../table_definitions.sql` 报 "Could not find
  the file"；容器内 site-packages 无任何 .sql
- 根因：pyproject 无 `[tool.setuptools.package-data]`——setuptools 默认仅打包
  .py，21 个 SQL + workflow_definition_v1.schema.json 全被静默丢弃；dev 无感
  （pythonpath=src 直读源码树）
- 修复：pyproject 增 package-data 段（postgres：`*.sql` +
  `schema_migrations/*.sql` + README；yaml_schema：`*.json`）
- 验证：重建后镜像内 ls 实证 + docker cp 4157B 往返成功
- 影响面：任何 wheel/sdist 安装路径（镜像、S5 离线包、pip install）同病——
  离线交付直接受益

### 缺口④：compose 双 env 通道裂脑（端口绑定静默回落）

- 症状：drill 内部复现——up 时漏传 `--env-file .env.production`，api 端口从
  127.0.0.1:8331 静默漂移为 **0.0.0.0:8000**（全网卡暴露），容器却 healthy、
  数据面正常
- 根因：compose 两条独立 env 通道——①`env_file:` 服务指令（喂容器环境）；
  ②`${VAR}` 插值（喂 ports/healthcheck；源=--env-file/shell/.env）。漏
  --env-file 时①仍读 .env.production、②回落 `.env`（dev 基线 API_PORT=8000、
  无 API_BIND_IP）→「容器环境=生产模板、绑定面=开发默认」裂脑
- 加固：compose 头部裂脑告警 + ports 注释补插值源说明 +
  `.env.production.example` 增 `API_BIND_IP`（含成因注记）
- 防纵深实证：裂脑态若再遇 fresh volume，postgres 以 .env 弱密码 initdb →
  api 侧 `startup_checks.py` 生产 fail-fast 拦截（change-me 前缀黑名单）——
  应用层自防成立
- 后续：deploy.md §12 升级 runbook（S4）纳入此陷阱

## 4. drill env 修复链（.env.production 迭代实录）

| 故障 | 症状 | 修复 |
|---|---|---|
| API_PORT 复合值 | api crash loop：pydantic `int_parsing`（'127.0.0.1:8331'） | API_PORT=8331 整数 + 新增 API_BIND_IP=127.0.0.1（compose ports 模板 `${API_BIND_IP}:${API_PORT}:8000`） |
| TOKEN_VERIFY_MODE=plaintext | pydantic `literal_error` | 合法值仅 legacy/jwt/both |
| TOKEN_VERIFY_MODE=legacy | StartupConfigError 生产 fail-fast 拒启 | **生产仅允许 jwt**（both/legacy 明文路径禁）→ TOKEN_VERIFY_MODE=jwt |
| 签发路由 404 | POST /api/v1/auth/token 无此路由 | AUTH_TOKEN_ISSUANCE_ENABLED=true |

生产 fail-fast 检查全集（`startup_checks.py` 实读）：①token_verify_mode=jwt；
②三密钥弱值黑名单（默认值 / change-me 前缀 / <32 字符）。drill 密钥
64-hex / 32-hex / 44-char（base64 32B）全过。

## 5. schema 应用与终态证据

### 5.1 迁移链（手工 psql，schema_migrations/README 容器程序）

- `docker cp` 源 SQL 入 postgres 容器 + `psql -v ON_ERROR_STOP=1 -f` 逐个应用
- table_definitions.sql（4 基表）+ v1_1…v1_10（10 前向）全 OK
- schema_versions 登记 10 版（1.1–1.10）；RLS（v1_7）已启用——psql 直查需
  `SELECT set_config('app.bypass_rls','on',true)`
- 程序与工具坑：docker cp 目录目标已存在会嵌套（先 rm -rf）；psql 多语句 -c
  输出按行拆分，管道取值注意尾部空行

### 5.2 冒烟（JWT 链 + read_only 下 PG 写）

- 铸 token：POST /api/v1/auth/token `{tenant_id,user_id}` → Bearer len=287
  （部署侧信任边界，无凭证交换；ttl 上界=默认×24）
- 校验：POST /api/v1/workflows/validate（sequential_pipeline.yaml，UTF-8
  干净读）→ **valid=True**
- **写路径（read_only 根文件系统下）**：POST /api/v1/workflows → **HTTP 200**
  （definition_id=sequential_pipeline，version=1，status=draft）
- 回读：GET /api/v1/workflows/sequential_pipeline → HTTP 200（version=1）
- DB 侧：psql bypass_rls 计数 **wf_rows=1**——跨两轮容器重建存活（named
  volume `postgres-data` 与容器根只读解耦实证）
- 注：GET /api/v1/workflows 返 405 属设计（无列表端点；路由=validate/save/
  {id}/versions/publish/rollback）

### 5.3 终态全绿快照（down 前）

```
b15-s3-api-1:       Up (healthy) | 127.0.0.1:8331->8000/tcp
b15-s3-worker-1:    Up           | 8000/tcp（无 host port）
b15-s3-postgres-1:  Up (healthy) | 5432/tcp（无 host port；digest 锁
                                 postgres:16-alpine@sha256:f2f98726…fb6d）
```

- inspect：api/worker **ReadOnly=true**、**Tmpfs=map[/tmp:]**（read_only+tmpfs
  在三轮重建后稳定成立）
- /healthz：`{"status":"ok"}`；worker 日志安静（队列轮询器无作业静默，logs
  --since 0 行）
- 暴露面：唯一 host port 8331 且限环回；postgres/worker 不对外

## 6. 主树提交边界备忘

- **742d570**（zhao602，2026-11-25 19:54 收口）= 本 drill 中途快照：
  .dockerignore/Dockerfile/docker-compose.yml 三文件**修复前状态**（Dockerfile
  裸 `.`、compose 无 build.args/API_BIND_IP/裂脑告警）
- 其后修复（asyncpg extra/package-data/裂脑告警/API_BIND_IP 模板项）在 B15
  收口提交的工作区增量中携带
- **3ac1d1a**（B16 批）与 B15 文件零交集 ✓；B15 收口叠加其上（门禁锚点
  3c1b4f1 冻结口径不变）

## 7. 工具陷阱登记（Windows PS 5.1 环境实录）

1. `Get-Content` 输出字符串带 FileSystem provider NoteProperties（PSPath 等），
   `ConvertTo-Json` 会把 payload 序列化成对象（服务端 422 string_type）；
   修法 `[IO.File]::ReadAllText(path, UTF8)` 取纯净串
2. `Get-Content` 默认按 GBK 读 UTF-8 文件（中文乱码）；HTTP 字符串体按
   Latin-1 上线——统一显式 UTF-8 字节体（`[Text.Encoding]::UTF8.GetBytes`）
3. `curl.exe` 单引号 JSON 传参双引号被剥（服务端 json_invalid）——用
   Invoke-RestMethod
4. `docker logs` 含全史——用 `--since` 取新（旧 traceback 曾两度误导诊断）
5. `docker cp` 目标目录已存在会嵌套——先 `rm -rf` 或 cp 到父级
6. PS 控制台按 Latin-1 显示 UTF-8 响应（回读 name 乱码为显示伪影，线上字节
   正确）——判定以字节/psql 侧为准

## 8. 清理证据

```
$ docker compose -p b15-s3 down -v
 Volume b15-s3_api-logs Removed
 Volume b15-s3_postgres-data Removed
 Volume b15-s3_api-data Removed
 Network b15-s3_default Removed
containers_left=0  volumes_left=0
image survives: sha256:9ab3314c53f7… 237870121B（保留供 SBOM/扫描/S5 离线打包）
```

诊断残留（b15-s3-diag）为纯 build 无容器/卷/网络产出，随 build 缓存自然回收。

## 9. SBOM 与镜像扫描（design D3，S3 出口件）

### 9.1 工具 provenance 与网络梯子（实测实录）

- 计划口径：daocloud 容器化执行——**实测受阻**：`docker.m.daocloud.io/anchore/syft`
  拒拉（DaoCloud public-image-mirror 仅白名单 library/*，anchore/syft 不在列）；
  daemon 镜像链与 docker.1ms.run 对非 library 仓库均超时死等（4–5 分钟零输出）
- **实际路径：官方 GitHub Releases 宿主二进制**（同工具同版本，官方分发渠道，
  provenance 由发布校验和闭环）：
  - syft v1.18.0（`syft_1.18.0_windows_amd64.zip`，17191362B，
    sha256 `e7c5face12a70cd1…` vs 官方 checksums.txt **match**）
  - trivy v0.76.2（`trivy_0.76.2_windows-64bit.zip`，53905616B，
    sha256 `691f737a7e5566a6…` vs 官方 checksums.txt **match**）
- trivy 漏洞库拉取梯子（DB ~OCI 制品，与二进制分发独立）：
  ①默认 `mirror.gcr.io/aquasec/trivy-db:2` → dial 超时（Google 线路不可达）
  ②`ghcr.io/aquasec/trivy-db:2` → 匿名 token DENIED
  ③**`public.ecr.aws/aquasecurity/trivy-db:2` → SUCCESS**（Aqua 官方 ECR 公共画廊）
- 版本锚定：ci.yml docker job 同口径使用 syft v1.18.0 / trivy v0.76.2 +
  ECR DB 仓库（真实 CI 落地时按 D10 语义）

### 9.2 SBOM（syft → SPDX JSON）

- 产物：`delivery/sbom/sbom-app.spdx.json`（**SPDX-2.3**，syft-1.18.0 生成，
  1269309B UTF-8）
- 摘要：**176 包 = 87 debian（OS 层）+ ~88 pypi（应用层）+ 1 image 自引用**；
  npm 0（前端构建产物为静态 dist，npm 依赖不进终镜像——多阶段构建有效性佐证）
- 镜像级证据互链：`asyncpg@0.32.0` 在册（缺口①修复）；`orchestration-platform@0.3.0`
  在册（应用本体）；`psycopg@3.3.9`/`fastapi@0.135.4`/`langgraph@1.2.24` 等主链齐
- 工具坑：PS 5.1 `>` 重定向把 stdout 写成 UTF-16LE（0xFF BOM）——必须用
  syft 原生 `-o spdx-json=<file>` 进程内落盘

### 9.3 镜像扫描基线（trivy → JSON，登记非零门禁）

- 产物：`delivery/scan/trivy-app.json`（1320429B）+ `delivery/scan/baseline-summary.txt`
  （去重清单，副本 `_drills/b15_s3_scan_baseline.txt` 入库）
- **基线（登记口径，不设零门禁——design D3 裁决）**：
  - 严重度计数：**CRITICAL=0 / HIGH=61（去重 CVE=20）/ MEDIUM=112 / LOW=82 / UNKNOWN=21**
  - **自研层（orchestration-platform 包本体）发现数=0** ✓（D3「新增自研层 CVE
    关注度为 0」判据达成）
  - HIGH 构成：Debian 13.7 基镜像系统包为主（util-linux 系 9 包 ×4 CVE、
    openssl ×2、sqlite ×3、ncurses、apt、zstd、zlib、systemd、perl）+
    应用依赖 4 项（starlette ×2、wheel、jaraco.context——pyproject 上界约束
    域，超批登记）；全清单见 baseline 文件
  - 存量 CVE 不阻塞出口；重建基镜像（digest 升级）与依赖升级属长期运维域

