# 离线交付指南（offline-delivery.md）

> 目标读者：在内网/隔离环境部署本平台的交付与运维人员。
> 目标：离线包到位后 **10 分钟内**完成安装、schema 初始化与验证。
> 配套：`scripts/ops/package_offline.py`（打包器）、`delivery/offline-<version>/`（交付包）、`install-offline.sh`（包内一键安装器）。
> 实证：`_drills/b15_offline_drill.md`（rmi→load→up→healthz→冒烟全链 drill，2026-11-25）。

---

## 1. 交付包清单（offline-0.3.0 实测：67 文件 / 556.9 MB）

| 件 | 尺寸 | 说明 |
|---|---|---|
| `images/app.tar` | 235.1 MB | 应用镜像（api+worker 同镜像） |
| `images/postgres.tar` | 283.7 MB | PostgreSQL 16 基镜像（amd64） |
| `wheels/` ×58 | — | 平台本体 wheel + `.[postgres,sso]` 依赖（manylinux2014_x86_64 + cp310） |
| `sbom/sbom-app.spdx.json` | 1.27 MB | 镜像 SBOM（SPDX-2.3，176 包） |
| `manifest.json` | 11.3 KB | 交付清单（无时间戳；digest/校验和/repo_digests） |
| `SHA256SUMS` | 7.6 KB | 全包校验和（manifest 自身除外） |
| `install-offline.sh` | 2.0 KB | 一键安装器（bash） |
| `docker-compose.yml` | 6.6 KB | 编排副本（离线版：image 引用 digest 已剥离） |
| `.env.production.example` | 5.4 KB | 生产配置模板（密钥单一事实源） |
| `README.md` | 1.6 KB | 包内说明 |

## 2. 前置条件（目标机）

- Linux x86_64 + Docker ≥ 20 + Compose v2（`docker compose version` ≥ v2.22，
  需支持 `up --pull never`）；bash、curl、sha256sum。
- **零外网假设**：安装全程不访问 registry/pypi（`--no-build --pull never`）。

## 3. 安装（install-offline.sh）

```bash
cd offline-0.3.0/

# 0. 完整性校验（也可直接跑安装器，其 [1/6] 内置本步）
sha256sum -c SHA256SUMS          # 期望：全部 OK，0 坏行

# 1. 生成生产配置（不改起不来：弱密钥生产 fail-fast）
cp .env.production.example .env.production
vi .env.production               # 三密钥强随机值 + 管理员标识（见 deploy.md §2/§4）

# 2. 一键安装（校验 → load ×2 → up --no-build --pull never → healthz 轮询）
bash install-offline.sh
# 期望末行：[6/6] 完成；healthz OK（默认探 127.0.0.1:${API_PORT}/healthz）
```

安装器六步（每步失败即停，`set -euo pipefail`）：

1. `sha256sum -c SHA256SUMS`（篡改/缺文件零容忍）；
2. `docker load` app/postgres 两 tar；
3. `.env.production` 存在性检查（缺失报错并提示模板红线）；
4. `docker compose --env-file .env.production up -d --no-build --pull never`
   （**零构建零拉取**；漏 `--env-file` 会触发端口插值裂脑，见 compose 头注）；
5. healthz 轮询（60 次 × 2s；`curl --noproxy '*'` 环回探活代理免疫）；
6. 完成提示。

## 4. schema 初始化（fresh install 必做）

fresh 数据卷无 schema 时 **worker 会 crash loop**（`UndefinedTableError:
execution_job`，restart 策略兜底自愈）——属已知态，schema 初始化后自然转安静：

```bash
# 进入 postgres 容器应用前向迁移链（ON_ERROR_STOP；勿吞 *_rollback.sql；sort -V 防字典序）
docker cp src/orchestration_platform/persistence/postgres/table_definitions.sql <pg容器>:/tmp/schema/
docker cp src/orchestration_platform/persistence/postgres/schema_migrations <pg容器>:/tmp/schema/
docker exec <pg容器> sh -c 'set -e
  psql -U $POSTGRES_USER -d $POSTGRES_DB -v ON_ERROR_STOP=1 -q -f /tmp/schema/table_definitions.sql
  for f in $(ls /tmp/schema/schema_migrations/v1_*.sql | grep -v rollback | sort -V); do
    psql -U $POSTGRES_USER -d $POSTGRES_DB -v ON_ERROR_STOP=1 -q -f $f
  done'
# 期望：schema_versions 10 行；worker 数十秒内转 Up 且日志安静
```

> 注：离线包不含源码 SQL（交付物=镜像 tar）；schema 初始化用的 SQL 随交付渠道
> 附带或从镜像内提取（镜像已含全量 SQL，package-data 修复后随包分发）。

## 5. 验证清单

```bash
docker ps                                   # api/postgres (healthy)；worker Up
curl http://127.0.0.1:${API_PORT}/healthz   # {"status":"ok"}
docker exec <api容器> printenv | grep -c proxy  # 代理注入仅影响探活（已免疫），出网代理为部署方管理
```

冒烟（完整链见 `_drills/b15_offline_drill.md` §6）：铸 token → validate →
save draft v1 → GET 回读 → `wf_rows=1`（RLS 下 psql 需
`SELECT set_config('app.bypass_rls','on',true)`）。

## 6. 零拉取语义与 provenance

- 安装只 `docker load` + `up --no-build --pull never`；镜像引用缺失时**硬错误**，
  绝不回退拉取（drill 反向实证：digest 引用缺失 → `No such image` 直接失败）。
- **save/load 不恢复 RepoDigests**：离线包 compose 副本因此剥离 image 引用的
  digest 后缀（tag-only）；registry digest 与内容 digest 均在 `manifest.json`
  （`images[].digest / repo_digests`），tar 内容由 SHA256SUMS 锁定——provenance
  链完整，在线部署路径的 digest 锁不受影响。

## 7. 代理注意事项（B15 实测）

目标机 docker client 若配 proxies（`~/.docker/config.json` proxies 段），
compose 会**自动注入** `http_proxy` 等六变量进容器：

- 探活已免疫：compose healthcheck 用 `ProxyHandler({})` 强制直连；安装器
  curl 用 `--noproxy '*'`（曾因 urllib 走代理产生 43 连败假 unhealthy，已修复）。
- 出网代理（如 LLM egress）为部署方显式管理项，本包不替目标机做代理决策。

## 8. 重新生成交付包（在线打包机）

```bash
python scripts/ops/package_offline.py \
  --pip-index-url https://pypi.tuna.tsinghua.edu.cn/simple
# 默认输出 delivery/offline-<pyproject版本>/；可加 --output / --skip-images
# 打包实测：67 文件 / 556.9 MB / ~66s（save ×2 ≈20s + wheels ≈50s）
# sdist 回退：实测 0（.[postgres,sso] 全链 manylinux2014 wheel 可得）
```

单测（纯函数，不触 docker/pip）：`pytest tests/unit/test_offline_package_manifest.py`
（9 测试：校验和聚合/坏校验和拒绝/manifest 无时间戳红线/离线语义断言/digest 剥离靶向）。

## 9. 已知边界

- 平台：Linux x86_64（镜像 amd64 + wheels manylinux2014_x86_64/cp310）；
  其他架构需重新打包。
- 离线实证口径：单机模拟（同机 rmi→load→up，事件面零 pull + 镜像内容 ID 同一
  + `--pull never` 三层论证）；真实内网隔离环境以交付方实测为准。
- 升级：参照 `deploy.md` §11/§12（备份恢复与升级 runbook）。
