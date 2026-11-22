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
#   - 依赖清单以 pyproject.toml 为单一事实源（pip install .，零额外锁定文件）；
#   - 前端版本以 web/package-lock.json 锁定口径为准（npm ci）；
#   - 运行时以非 root 用户运行（生产加固）；前端产物置于 /app/web/dist，
#     由反向代理/网关托管（应用本身不挂载静态目录，deploy.md §前端访问）。
#
# 禁试说明：docker build 属 N 类禁试（§13.1），本文件为配置产出物，
#           仅做三阶段结构静态核对（S）；真实构建移交用户（§8-D5）。
# =============================================================================

# ---------- stage1：后端依赖层 ----------
FROM python:3.10-slim AS backend-deps

WORKDIR /build

# 先复制依赖声明与包源码，一次性安装（--prefix 便于运行时层整层拷贝）
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

# ---------- stage2：前端构建层 ----------
FROM node:20-slim AS frontend-build

WORKDIR /web

# 先复制锁文件安装依赖（利用层缓存），再复制源码构建
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ---------- stage3：运行时合并层 ----------
FROM python:3.10-slim AS runtime

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
