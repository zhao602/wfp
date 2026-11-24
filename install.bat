@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  AgentFlow (orchestration-platform) 一键安装脚本 Windows 版
REM  用法:
REM    install.bat            安装核心依赖 core
REM    install.bat test       额外安装测试依赖 pytest / pytest-asyncio
REM    install.bat all        额外安装 postgres / sso / test 全部可选档
REM    install.bat check      仅检查环境，不安装
REM  注意: all 档的 sso 依赖 xmlsec，需要系统级 libxmlsec1 运行库。
REM ============================================================

set "PROFILE=%~1"
if "%PROFILE%"=="" set "PROFILE=core"

echo ==================================================
echo  AgentFlow 一键安装 - profile: %PROFILE%
echo ==================================================
echo.

rem ---------- 1. Python ----------
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>nul
if errorlevel 1 goto :no_python
echo [1/6] Python 3.10+ 检查通过

rem ---------- 2. Node.js ----------
node -v >nul 2>nul
if errorlevel 1 goto :no_node
for /f %%v in ('node -p "parseInt(process.versions.node, 10)"') do set "NODE_MAJOR=%%v"
if %NODE_MAJOR% LSS 18 goto :node_old
echo [2/6] Node.js %NODE_MAJOR% 检查通过

rem ---------- 3. .env ----------
if exist ".env" (
    echo [3/6] 已存在 .env，跳过初始化
) else (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo [3/6] 已从 .env.example 生成 .env，请修改 API_ACCESS_TOKEN 等默认值
    ) else (
        echo [3/6] 警告: 未找到 .env.example，跳过 .env 初始化
    )
)

if /i "%PROFILE%"=="check" goto :check_done

rem ---------- 4. 后端依赖 ----------
if not exist ".venv" (
    echo [4/6] 创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 goto :venv_fail
) else (
    echo [4/6] 复用已有虚拟环境 .venv
)

set "VPY=.venv\Scripts\python.exe"
if not exist "%VPY%" set "VPY=.venv\bin\python.exe"
if not exist "%VPY%" goto :venv_fail

"%VPY%" -m pip install --upgrade pip
if errorlevel 1 goto :pip_fail

if /i "%PROFILE%"=="core" set "PIP_TARGET=-e ."
if /i "%PROFILE%"=="test" set "PIP_TARGET=-e .[test]"
if /i "%PROFILE%"=="all"  set "PIP_TARGET=-e .[postgres,sso,test]"
if "%PIP_TARGET%"=="" goto :bad_profile

echo [5/6] 安装后端依赖: %PIP_TARGET% ...
"%VPY%" -m pip install %PIP_TARGET%
if errorlevel 1 goto :pip_fail

"%VPY%" -c "from orchestration_platform.api.application_factory import create_app; print('backend import OK')"
if errorlevel 1 goto :smoke_fail
echo       后端导入冒烟测试通过，wfp CLI 已注册，可用: .venv\Scripts\wfp --help

rem ---------- 6. 前端依赖 ----------
echo [6/6] 安装前端依赖 web/ ...
pushd web
if exist "package-lock.json" (
    call npm ci
    if errorlevel 1 (
        echo npm ci 失败，回退到 npm install ...
        call npm install
        if errorlevel 1 goto :npm_fail
    )
) else (
    call npm install
    if errorlevel 1 goto :npm_fail
)
popd

goto :done

:check_done
echo.
echo check 模式: 环境检查通过，未执行安装。
echo 完整安装请运行: install.bat [core^|test^|all]
goto :summary_hint

:done
echo.
echo ==================================================
echo  安装完成
echo ==================================================

:summary_hint
echo 启动方式:
echo   后端: .venv\Scripts\activate ^&^& uvicorn orchestration_platform.api.application_factory:create_app --factory --host 0.0.0.0 --port 8000
echo   前端: cd web ^&^& npm run dev    访问 http://localhost:5173
echo   可选 PostgreSQL: pip install -e ".[postgres]" 并设 STORAGE_BACKEND=postgres
echo.
exit /b 0

:no_python
echo [错误] 未找到 Python 3.10+。请安装 Python 3.10 及以上版本并加入 PATH。
goto :fail
:no_node
echo [错误] 未找到 Node.js。请安装 Node.js 18 及以上版本并加入 PATH。
goto :fail
:node_old
echo [错误] Node.js 版本过低，当前主版本 %NODE_MAJOR%，需要 18+。
goto :fail
:bad_profile
echo [错误] 未知档位 "%PROFILE%"，可选 core / test / all / check。
goto :fail
:venv_fail
echo [错误] 虚拟环境创建失败，请检查 Python 安装。
goto :fail
:pip_fail
echo [错误] 后端依赖安装失败，请检查网络或代理设置。
goto :fail
:smoke_fail
echo [错误] 后端导入冒烟测试失败，请检查 Python 版本是否为 3.10 兼容。
goto :fail
:npm_fail
echo [错误] 前端依赖安装失败，请检查 Node.js 与网络。
goto :fail
:fail
echo.
echo 安装中止。修复上述问题后重新运行 install.bat。
exit /b 1
