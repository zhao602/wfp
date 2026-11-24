#!/usr/bin/env bash
# ============================================================
#  AgentFlow (orchestration-platform) 一键安装脚本 Linux/macOS 版
#  用法:
#    ./install.sh            安装核心依赖 core
#    ./install.sh test       额外安装测试依赖 pytest / pytest-asyncio
#    ./install.sh all        额外安装 postgres / sso / test 全部可选档
#    ./install.sh check      仅检查环境，不安装
#  注意: all 档的 sso 依赖 xmlsec，需要系统级 libxmlsec1 运行库
#        (Debian/Ubuntu: apt install libxmlsec1-dev libxmlsec1-openssl)
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

PROFILE="${1:-core}"

echo "=================================================="
echo " AgentFlow 一键安装 - profile: ${PROFILE}"
echo "=================================================="
echo

fail() { echo "[错误] $1"; echo "安装中止。修复上述问题后重新运行 ./install.sh"; exit 1; }

# ---------- 1. Python ----------
if ! command -v python3 >/dev/null 2>&1; then
  fail "未找到 python3，请安装 Python 3.10+"
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
  fail "Python 版本过低，需要 3.10+（当前 $(python3 -V 2>&1)）"
fi
echo "[1/6] Python $(python3 -V 2>&1 | awk '{print $2}') 检查通过"

# ---------- 2. Node.js ----------
if ! command -v node >/dev/null 2>&1; then
  fail "未找到 node，请安装 Node.js 18+"
fi
NODE_MAJOR="$(node -p 'parseInt(process.versions.node, 10)')"
if [ "${NODE_MAJOR}" -lt 18 ]; then
  fail "Node.js 版本过低，当前主版本 ${NODE_MAJOR}，需要 18+"
fi
echo "[2/6] Node.js ${NODE_MAJOR} 检查通过"

# ---------- 3. .env ----------
if [ -f ".env" ]; then
  echo "[3/6] 已存在 .env，跳过初始化"
elif [ -f ".env.example" ]; then
  cp .env.example .env
  echo "[3/6] 已从 .env.example 生成 .env，请修改 API_ACCESS_TOKEN 等默认值"
else
  echo "[3/6] 警告: 未找到 .env.example，跳过 .env 初始化"
fi

if [ "${PROFILE}" = "check" ]; then
  echo
  echo "check 模式: 环境检查通过，未执行安装。"
  echo "完整安装请运行: ./install.sh [core|test|all]"
  exit 0
fi

case "${PROFILE}" in
  core) PIP_TARGET="-e ." ;;
  test) PIP_TARGET="-e .[test]" ;;
  all)  PIP_TARGET="-e .[postgres,sso,test]" ;;
  *)    fail "未知档位 \"${PROFILE}\"，可选 core / test / all / check" ;;
esac

# ---------- 4. 后端虚拟环境 ----------
if [ ! -d ".venv" ]; then
  echo "[4/6] 创建虚拟环境 .venv ..."
  python3 -m venv .venv
else
  echo "[4/6] 复用已有虚拟环境 .venv"
fi
VPY=".venv/bin/python"

"${VPY}" -m pip install --upgrade pip
echo "[5/6] 安装后端依赖: ${PIP_TARGET} ..."
"${VPY}" -m pip install ${PIP_TARGET}

"${VPY}" -c "from orchestration_platform.api.application_factory import create_app; print('backend import OK')"
echo "      后端导入冒烟测试通过，wfp CLI 已注册，可用: .venv/bin/wfp --help"

# ---------- 6. 前端依赖 ----------
echo "[6/6] 安装前端依赖 web/ ..."
(
  cd web
  if [ -f "package-lock.json" ]; then
    npm ci || npm install
  else
    npm install
  fi
)

echo
echo "=================================================="
echo " 安装完成"
echo "=================================================="
echo "启动方式:"
echo "  后端: source .venv/bin/activate && uvicorn orchestration_platform.api.application_factory:create_app --factory --host 0.0.0.0 --port 8000"
echo "  前端: cd web && npm run dev    访问 http://localhost:5173"
echo "  可选 PostgreSQL: pip install -e \".[postgres]\" 并设 STORAGE_BACKEND=postgres"
echo
