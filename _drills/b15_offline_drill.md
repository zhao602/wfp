# B15 S5 离线交付 drill 记录（`_drills/b15_offline_drill.md`）

- 日期：2026-11-25；批：roadmap_b15_delivery_hardening S5（design D5/D6）
- 环境：Windows 宿主 + Docker Engine 28.5.2 / Compose v5.6.1；WSL 侧同一引擎
  （engine ID `004d2388-a87d-4e1e-905f-f085bed79e6a` 双侧一致，install-offline.sh
  可 WSL bash 全保真执行）
- 交付物：`delivery/offline-0.3.0/`（**67 文件 / 556.9 MB**，gitignored）

## 1. 目标与结论

- 目标（design D5/D6）：打包器实测 → 离线 drill（rmi→load→`up -d --no-build`→
  healthz+冒烟）→ 零 pull 论证 → 单测（纯函数）。
- 结论：**全绿**。install-offline.sh `[1/6]→[6/6]` 一次通过（两缺口修复后）；
  api **healthy（streak=0）且代理注入在场**；冒烟 save draft v1 → **wf_rows=1**；
  零 pull（三层论证，§7）；终产物 `sha256sum -c` **0 坏行**（67 文件全过）。
- 诚实口径：单机模拟离线（同机 rmi/load，网络仍在）；真实内网隔离部署以
  交付方实测为准（design D6 原文照录 README）。

## 2. 交付包结构（67 文件 / 556.9 MB；打包 66.0–87.5s/轮）

| 件 | 尺寸 | 说明 |
|---|---|---|
| `images/app.tar` | 246480384B（235.1MB） | orchestration-platform:0.3.0（save） |
| `images/postgres.tar` | 297489920B（283.7MB） | postgres:16-alpine（save） |
| `wheels/` ×58 | — | 57 依赖 + 1 平台本体（§5） |
| `sbom/sbom-app.spdx.json` | 1269309B | S3 产物拷入 |
| `manifest.json` | 11287B | 无时间戳红线 + 递归禁键自检 |
| `SHA256SUMS` | 7581B | 67 文件全锁（manifest 自身除外） |
| `install-offline.sh` | 1967B | 校验→load×2→up `--no-build --pull never`→healthz `--noproxy '*'` |
| `docker-compose.yml` | 6567B | **digest 剥离版副本**（缺口①，§3） |
| `.env.production.example` | 5399B | 模板副本 |
| `README.md` | 1584B | 生成时间唯一落点（不入 manifest） |

- 打包器 `scripts/ops/package_offline.py`（--version/--output/--skip-images/
  --pip-index-url）；单测 `tests/unit/test_offline_package_manifest.py` 9/9 绿
  （纯函数：sha256 向量/聚合排除往返/坏校验和/坏行拒绝/无时间戳+检测器灵敏度/
  schema 锁定/install 离线语义/digest 剥离行靶向）。
- 打包统计含 README 自身的 off-by-one 已修（先算后写 +1 投影，README 文件数=67 准数）。

## 3. 缺口①：docker save/load 不恢复 RepoDigests（第一次 install 实跑抓到）

- 症状（Run A，包内 compose 仍带 digest 引用）：`[2/6]` load 成功、
  `[4/6]` 报 daemon 原文 `No such image: postgres:16-alpine@sha256:f2f98726…fb6d`。
- 根因：save 只导出 manifest+layers，load 只还 **RepoTags**；RepoDigests 是
  registry 元数据不随 tar 走。`up --pull never` 按 digest 引用解析本地库必失败。
- 修复：包内 compose 副本剥离 `image:` 行 digest 后缀（`strip_digest_pins`，
  行靶向只动 image: 行，注释不动；单测覆盖）+ 副本头注记。
  **provenance 不降级**：digest 在 manifest.json（`images[].digest/repo_digests`）
  与 SHA256SUMS（tar 内容锁）；仓库在线 compose 的 digest 锁不动。
- 实证：重打包后同场景 `[4/6]` 通过（Run B/C）。

## 4. 缺口②：目标机 docker client proxies 注入劫持 healthcheck（决定性发现）

- 症状（Run B）：外探 healthz `{"status":"ok"}`、uvicorn 日志自证绑
  `0.0.0.0:8000`、`/proc/net/tcp` LISTEN 在场（uid 1000）、exec 裸 socket 连
  loopback **成功**（`CONNECT_RESULT 0`）——容器 healthcheck 却 **43 连败**
  `ConnectionRefusedError [Errno 111]`，api 转 unhealthy（外探始终 200）。
- 根因链（逐环实测）：WSL `~/.docker/config.json` proxies 段
  （`http://10.255.255.254:10808`，noProxy=`localhost,10.255.255.254` 不含
  127.0.0.1）→ install-offline.sh 在 WSL 执行 → compose 读**执行上下文** client
  config 自动注入 `http_proxy/https_proxy/no_proxy` 六变量进 api/worker →
  healthcheck 的 `urllib.request.urlopen` 代理感知 → `http://127.0.0.1:8000`
  被路由到代理 → 代理自容器 netns 不可达 → refused。
- **S3 为何 healthy**：S3 compose 自 Windows PS 执行（该侧无 client config）→
  零注入。同引擎、同镜像、同 env——唯一变量是执行上下文。
- 排除项（负证据留档）：daemon proxies 空（`docker info` 三字段空）、
  WSL `/etc/docker/daemon.json` 仅镜像源 4 项、Windows 侧无
  `~/.docker/config.json`、lo 接口 UP（flags 0x9 正常）。
- 修复（交付物代理免疫，目标机 config 无法控制故必免疫）：
  ① compose api healthcheck 改 `ProxyHandler({})` 强制直连（仓库+包内副本）；
  ② install-offline.sh 探活 curl 加 `--noproxy '*'`（单测断言锁死）。
- 实证（Run C，同一 WSL 注入上下文）：api **healthy streak=0**（restarts=0），
  注入仍在场（`http_proxy=http://10.255.255.254:10808`）——43 连败 → 0 失败。

## 5. wheels 口径（design D5 全参数）

- 依赖：`pip download --platform manylinux2014_x86_64 --python-version 3.10
  --only-binary=:all: .[postgres,sso]` → 57 wheel（tuna 源 44.5–58.0s）；
  **sdist 回退 0**（全链 wheel 可得，无需登记裁决）。
- 本体缺口（打包首跑发现）：pip download 对本地目录根包不落盘 → wheels/ 无
  orchestration_platform → 离线 `pip install --no-index` 不成立 → 增
  `pip wheel --no-deps` 本地构建 `orchestration_platform-0.3.0-py3-none-any.whl`
  （纯 Python 无平台约束），合计 **58**。
- 抽查：asyncpg 0.32.0 cp310 manylinux2014、pydantic_core cp310 manylinux2014、
  psycopg[binary] 3.3.9、starlette 0.52.1、fastapi 0.135.4 均在。

## 6. drill 时间线（Run C 决定性跑，缺口①②修复后）

1. 清场：rmi `orchestration-platform:0.3.0` + `postgres:16-alpine`
   （daocloud digest ref 上轮已无：load 只还 tag 不还 digest——缺口①同源）。
2. WSL bash `install-offline.sh`：`[1/6]` sha256sum -c 67 文件 OK →
   `[2/6]` load ×2 → `[4/6]` compose `--env-file .env.production up -d
   --no-build --pull never` → `[5/6]` healthz OK（第 2 探，127.0.0.1:8331）→
   `[6/6]` 完成。
3. 代理注入验证：api healthy（streak=0）+ `http_proxy` 注入在场（§4 实证）。
4. schema：docker cp + psql `-v ON_ERROR_STOP=1` 前向链（table_definitions +
   v1_1…v1_10；**`grep -v rollback` 防 9 个回滚文件误吞** + `sort -V` 防字典序
   v1_10<v1_2 坑；NOTICE 均为 IF EXISTS 幂等守护）→ schema_versions=10。
5. worker：fresh volume 无 schema 时 crash loop
   （`UndefinedTableError: relation "execution_job" does not exist`，
   restart: unless-stopped 兜底）→ schema 后自愈 Up、日志安静（§8）。
6. 冒烟（JWT 链）：铸 token `POST /api/v1/auth/token` `{tenant_id,user_id}` →
   access_token len=293 → validate `valid=true`（1 warning：analyze
   model_identifier 空，同 S3 基线）→ **POST /api/v1/workflows = draft v1
   HTTP 200** → GET 回读 200 → **wf_rows=1**（psql bypass_rls）。

## 7. 零 pull 论证（三层）

1. **事件面**：12m 窗口（覆盖 rmi→load→up→冒烟全程）`docker events` 零 pull
   条目；
2. **内容面**：镜像 ID 前后同一（app `sha256:9ab3314c53f7…`、postgres
   `sha256:a3c40a6a77a9…`）——load 还原内容非拉取；
3. **语义面**：up 显式 `--pull never`（compose v5.6.1）；缺口①的失败模式
   （digest 引用缺失 → 硬错误、无回退拉取）反向佐证 never 语义真实生效。
- `pull_policy: never` 入 compose 的评估：**不改** compose（服务级 pull_policy
  锁死在线路径升级语义），离线语义由 install 脚本 `--pull never` 显式携带。

## 8. worker crash loop 语义（fresh install 已知态，非缺陷）

- fresh volume + 未初始化 schema：worker 反复 `UndefinedTableError` exit 1
  （restart: unless-stopped 自愈），schema 初始化后自然转安静。
- 属设计内行为（schema 初始化=手工运维步骤，schema_migrations/README 程序）；
  install-offline.sh 不自动初始化 schema（与 S3 口径一致）。
- 运维提示入包 README；deploy.md §12（S4）升级 runbook 将补 schema 初始化
  为前置步骤。

## 9. 工具陷阱（增量，Windows PS 5.1 / WSL 实录）

1. PS 5.1 传 `sh -c` 内嵌双引号会被撕碎（`$(basename…)` 报语法错）→ 去 `"`
   用 `${var##*/}`；psql 引号嵌套用 PS 双单号转义（`''`）直传。
2. `docker rmi` digest ref 在 ref 已被前轮清掉时报 No such image（预期态）。
3. PS 控制台 Latin-1 显示 UTF-8 响应为伪影（GET 回读 name 乱码，字节正确；
   S3 同款）。
4. WSL 与 Windows docker 同引擎（ID 比对法：`docker info --format '{{.ID}}'`
   双侧一致）→ install-offline.sh 可 WSL 全保真执行；但 compose 读**执行侧**
   client config（缺口②根源）——跨上下文执行交付脚本前须审两侧 config。

## 10. 产物与下一步

- 产物：`scripts/ops/package_offline.py`、
  `tests/unit/test_offline_package_manifest.py`（9 测试）、compose healthcheck
  加固、install 脚本 `--noproxy '*'`、`delivery/offline-0.3.0/`（67 文件）。
- 下一步：`docs/offline-delivery.md` → S4 备份恢复批 → 收口（exit-record 纳
  两缺口叙事与代理处置留档）。
