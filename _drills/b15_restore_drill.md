# B15 S4 备份恢复与升级演练记录（b15_restore_drill.md）

- 日期：2026-11-25（UTC 15:41–15:47 首段；23:53–00:02 复段，会话中断跨 ~8h）
- 范围：design D7（backup/restore.py）+ D8（恢复 drill+RTO 分项）+ D9（v1_10 迁移往返）
- 项目隔离：`-p b15-s4`，端口 8331（B14 占 8330 错峰纪律）；compose=gate worktree 硬化版
- 前置：S5 收口后 offline-030 栈 down -v 腾端口（三卷+网络全清）

## §1 环境与引擎拓扑（本 drill 期间定谳）

- **引擎 = WSL 发行版内 dockerd**（非 Docker Desktop）：Windows 侧无
  `dockerDesktopLinuxEngine` npipe、`~/.docker/config.json` 不存在；
  `DOCKER_HOST=tcp://localhost:2375`（会话 env）经 WSL2 localhost 转发裸穿
  WSL dockerd；双侧 engine ID 同源（S5 已证）。
- **拓扑推论（§7-1 根因前提）**：裸 TCP 穿透无任何 Windows 路径翻译层。
- **WSL VM 空闲超时特性**：会话中断 ~8h 期间 VM 空闲关闭 → dockerd 随灭 →
  2375 拒连；复探时 `wsl -e` 自动拉起 VM → systemd 拉起 docker.service →
  `restart: unless-stopped` 三容器自愈复活（postgres/api healthy、worker Up），
  **数据全数幸存**（3 行/1 published/schema_versions=10）——卷持久化+
  自愈链为加成证据，非缺陷。

## §2 数据注入（D8/D9 公共前置）

b15-s4 fresh 起：postgres healthy 4s → schema（table_definitions + 10 前向迁移，
`grep -v rollback` + `sort -V` 程序）→ schema_versions=10 → api healthy 6s
（ProxyHandler 探活）→ JWT（len=279）→：

| 步骤 | 结果 |
| --- | --- |
| save1 | v1 draft |
| publish v1 | v1 **published**，published_at=2026-11-25T15:41:36.974760Z |
| save2 | v2 draft，draft_of=1（DB 真值；API 面经 DefinitionRef 不暴露该字段=既有表面选择） |

## §3 v1_10 迁移往返（D9）

**勘察定形态**：无 python 迁移 runner——schema_migrations/README.md 手工
psql 程序即「runner」；v1_10 双向幂等（前向 IF NOT EXISTS ×4 + ON CONFLICT
DO NOTHING；回滚 DROP IF EXISTS ×4）。

**回滚**（v1_10_rollback.sql）四断言全过：列余 0 / 索引余 0 / **行存活 2** /
schema_versions 仍 10（登记留痕）。

**re-apply**（v1_10_workflow_publish_state.sql）断言全过：列回 3 / 索引回 1 /
行 2 / **既有行回填 status='draft'（发布态丢失=脚本头数据警告实证：v1
published→draft、v2 draft_of 1→NULL、published_at 全 NULL）** /
schema_versions 仍 10（INSERT 0 0）。

**⚠ 同版本 re-publish 静默 no-op 陷阱（S4 最高价值发现）**：

- 对 v1 再调 publish → 返回 `status=published, published_at=15:41:36.974760Z`
  （**旧时间戳**），但 DB 列仍 `1:draft`。
- 机理（postgres_workflow_repository.publish:199-206）：幂等检查读
  `definition_body` JSON（get 读 body 为权威）；回滚+re-apply 重置了列、
  body 残留 published → 早退 no-op。`get_published`（列谓词）仍 None =
  执行入口解析不到已发布版；save 不可变守卫（列谓词）与 publish 幂等
  （body 谓词）两真源在迁移往返后分歧。
- **恢复发布态的产品路径（实证）**：save 新版本 v3（body=draft）→ publish
  v3 → 真执行 UPDATE，列/body 双写收敛：`3:published,
  pub_at=2026-11-25 15:46:37.711814+00`（新时间戳）。
- 已入 deploy.md §12.2（runbook：恢复发布态须走新版本重发）。

## §4 backup.py（D7）

**首跑失败两根因（均已修）**：

1. **宿主 bind mount 拓扑失配**：`-v D:\…\backups:/out` 报
   `invalid mode: /out`——裸 TCP 穿 WSL dockerd 无 Windows 路径翻译，
   按冒号裸切三段（ASCII 路径同罪，非编码问题）。修复：**全链去宿主
   bind mount**，卷 tar 走容器 stdout 二进制管道（与 pg_dump 同构，
   拓扑无关：裸 TCP/npipe/Linux 通吃）。
2. **GBK 控制台编码崩溃**：`✓`（U+2713）不在 GBK → `UnicodeEncodeError`
   （Python Windows stdout 缺省本地代码页）。修复：两脚本 main 入口
   `sys.stdout/stderr.reconfigure(errors="replace")` + 文案去 ✓。

**复跑全绿**（20261125T235302Z，alpine:3.20 digest 锁 d9e853e8…4b6bc）：

| 件 | 实测 |
| --- | --- |
| db.dump（pg_dump -Fc 16.16） | 92864B / 1.7s |
| api-data.tar.gz | 120B / 1.5s（tar 读侧 stdin 管道验证：`./mcp_tool_market/`） |
| api-logs.tar.gz | 88B / 1.4s |
| manifest.json（schema=backup-manifest/1） | 1005B（项目名/pg_dump 版本/created_at/各件 sha256） |
| SHA256SUMS（4 项含 manifest 自身） | 322B |
| **合计** | **5 文件 / 0.1MB / 5.7s** |

## §5 全灭与恢复（D8）

`down -v` 全灭确认（0 容器/0 卷/网络删，postgres-data 同灭=新机等价态）。

restore.py（`--confirm` 双闸+项目名一致性闸+校验和 4 项前置通过）：

| RTO 分项 | 实测 |
| --- | --- |
| compose down（幂等） | 0.5s |
| 卷 tar 解回 ×2（stdin 管道，清后还原） | 2.8s |
| postgres 起 + healthy（fresh initdb） | 8.3s |
| pg_restore --clean --if-exists | 1.2s |
| api/worker up --no-build | 1.6s |
| healthz 200（127.0.0.1:8331，ProxyHandler 直连） | 3.2s |
| **总计（全灭态→healthz 200）** | **17.6s** |

## §6 恢复后核验（逐位一致）

- DB 真值：`1:draft / 2:draft / 3:published pub_at=2026-11-25 15:46:37.711814+00`
  ——与备份前**微秒级一致**；schema_versions=10。
- API 面：versions 端点 v3 published `2026-11-25T15:46:37.711814Z` / v2、v1
  draft——与备份前完全相同。
- 既有库路径（非 fresh）由 `pg_restore --clean --if-exists` 按对象替换覆盖，
  与 fresh 路径同码（§5 一次 drill 覆盖最强 fresh 路径）。

## §7 发现清单

| # | 发现 | 定性 | 处置 |
| --- | --- | --- | --- |
| 1 | 裸 TCP 穿 WSL dockerd 无宿主路径翻译（bind mount 必炸） | 环境拓扑 | backup/restore 改 stdout/stdin 管道（无 bind mount）；deploy.md §11.1 纪律 |
| 2 | WSL VM 空闲超时 → dockerd/容器随灭；systemd+restart 自愈、卷数据幸存 | 环境特性 | 记录；长静默后 docker 拒连先查 VM 存活 |
| 3 | 迁移往返后同版本 re-publish 静默 no-op（body/列两真源分歧） | **产品语义陷阱** | deploy.md §12.2 runbook（新版本重发收敛）；不动 src（B15 边界） |
| 4 | Python Windows 控制台 GBK 编码崩溃（U+2713） | 工具健壮性 | reconfigure(errors="replace")（两脚本） |
| 5 | API 面不暴露 draft_of（DefinitionRef 无该字段，DB 写路径正确） | 观察项 | 记录（B11 既有表面选择） |

## §8 产出与留档对应

- `scripts/ops/backup.py` / `scripts/ops/restore.py`（ruff 零违规 + py_compile 过；
  CI 侧 ruff 沙箱未装=run_local_ci 既有 py_compile 兜底口径）
- `docs/deploy.md` §11 备份与恢复 + §12 升级 runbook + §8 移交清单回填
- 本记录；tasks.md S4 L43–L50
- drill 残留清理：b15-s4 down -v（0 容器/0 卷）；`backups/20261125T154659Z`
  残包已删，保留 `backups/20261125T235302Z` 为产物样本（gitignored）

## §9 B14 kill-9 记录核对（tasks L49）

任务预期路径 `_drills/m3_kill9_takeover_record.json` 为简写——实际双份在
spec 目录（同批同源）：

- `.codeartsdoer/specs/roadmap_b14_contract_fault_batch/_drills/m3_kill9_takeover_record.json`（1216B）
- `.codeartsdoer/specs/roadmap_m3_cancel_semantics/_drills/m3_kill9_takeover_record.json`（1216B）
- 新鲜度：2026-11-25T06:05:07Z→06:05:23Z（与 tmp-kill9-20261125T060507Z
  同批）；kill-9=taskkill /F /T；attempts=2、checkpoint 不重放、接管完成，
  六检查全真，**verdict=PASS**。核对通过。

## §10 结论

S4 全绿收口：backup 5.7s / 5 文件 / 0.1MB；全灭态恢复 RTO **17.6s** 分项
留档；恢复后 DB/API 逐位一致；v1_10 往返双向幂等实证 + re-publish no-op
陷阱入 runbook；kill-9 双记录核对 PASS。
