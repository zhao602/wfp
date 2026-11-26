# =============================================================================
# 多阶段 Dockerfile（Dockerfile）
#
# 目标（arch_review_v2_tasks 4.2，CHG-33）：
#   stage1 backend-deps     后端依赖层（pyproject.toml 安装主包 + 依赖）；
#   stage2 frontend-build   前端构建（web/ npm ci + npm run build → dist/）；
#   stage3 runtime          运行时合并（uvicorn 服务 + 前端静态产物）。
#
# 口径：
#   - Python 3.10 基础镜像与项目现状一致（requires-python >=3.10，实测 3.10.20）；
#   - 依赖清单以 pyproject.toml 为单一事实源（零额外锁定文件）；安装含
#     postgres extra（asyncpg——STORAGE_BACKEND=postgres 运行时惰性依赖，
#     B15 compose drill 实测镜像缺件致 worker 崩溃后补入，2026-11-25）；
#   - 前端版本以 web/package-lock.json 锁定口径为准（npm ci）；
#   - 运行时以非 root 用户运行（生产加固）；前端产物置于 /app/web/dist，
#     由反向代理/网关托管（应用本身不挂载静态目录，deploy.md §前端访问）。
#
# 禁试说明（历史）：docker build 曾属 N 类禁试（§13.1），本文件为配置产出物。
# B15 实测说明（roadmap_b15_delivery_hardening，2026-11-25，用户裁决解除 N 类禁试）：
#   - 本文件经真实三阶段构建验证（镜像 orchestration-platform:0.3.0；构建记录
#     与 SBOM/扫描见批次 _drills/ 与 delivery/，出口记录 §门禁）；
#   - 基础镜像 digest 锁定（供应链锚定）：digest 经 docker.m.daocloud.io 显式
#     拉取实测后按内容寻址锁定，与 Docker Hub 官方 digest 一致（provenance 注记）；
#   - 构建上下文白名单排除见 .dockerignore（stage2 宿主 node_modules 覆层修复）。
# =============================================================================

# ---------- stage1：后端依赖层 ----------
# digest 锁：python:3.10-slim（provenance：daocloud 显式拉取，内容寻址=官方）
FROM python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d AS backend-deps

# B15：pip 源可注入——默认官方源保可移植；网络受限环境经
# --build-arg PIP_INDEX_URL=<镜像源> 注入（实测口径见批次 drill 记录：
# 直连 files.pythonhosted.org 读超时，aliyun 镜像实测通过）。
ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /build

# 先复制依赖声明与包源码，一次性安装（--prefix 便于运行时层整层拷贝）
COPY pyproject.toml README.md ./
COPY src ./src
# B15：postgres extra（asyncpg）随主包一并安装——compose 生产路径
# STORAGE_BACKEND=postgres 的仓储/队列惰性导入依赖；裸 `.` 安装缺件（drill 实测
# worker ModuleNotFoundError: asyncpg → 修为 '.[postgres]'）。
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} --prefix=/install '.[postgres]'

# ---------- stage2：前端构建层 ----------
# digest 锁：node:20-slim（provenance：daocloud 显式拉取，内容寻址=官方）
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS frontend-build

WORKDIR /web

# 先复制锁文件安装依赖（利用层缓存），再复制源码构建
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ---------- stage3：运行时合并层 ----------
# digest 锁：与 stage1 同源同版（python:3.10-slim）
FROM python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d AS runtime

# 非特权运行用户
RUN useradd --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# 后端：整层拷贝 site-packages 与入口脚本（uvicorn 等）
COPY --from=backend-deps /install /usr/local

# 前端：静态产物（dist/），供反向代理/网关托管
COPY --from=frontend-build /web/dist ./web/dist

# 运行时数据目录（local_file 后端落点；postgres 后端时不依赖）
RUN mkdir -p /app/.data /app/logs && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# 应用工厂模式启动（与 README「后端启动」一致：--factory）
CMD ["uvicorn", "orchestration_platform.api.application_factory:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
