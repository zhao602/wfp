# wfp-sdk 发布 runbook（B8b · N3）

> 状态（2026-11-22）：打包元数据完备、构建产物可复现（`python -m build` +
> `twine check` 通过）、干净 venv 安装 + 免 LLM demo 验证通过。
> PyPI 真实上传待凭据（用户裁决：本地先行，凭据后补）。
> 包名可用性已验证（2026-11-22 pypi.org/pypi/wfp-sdk/json → 404）；
> 上传前按 §0 预检 #3 复核一次。

## 0. 预检清单（上传前逐项核对）

| # | 项 | 状态 |
|---|---|---|
| 1 | `sdk/pyproject.toml` `[project.urls]` 回填真实 Homepage/Repository（当前为注释占位） | 待 remote 确定后回填 |
| 2 | `LICENSE` 版权主体占位 `wfp-orchestration contributors` 替换（法人主体确定后） | 待定 |
| 3 | PyPI 包名 `wfp-sdk` 可用性复核：`curl -s https://pypi.org/pypi/wfp-sdk/json` 应 404 | 已验证可用（2026-11-22） |
| 4 | 版本三处同步（根 / sdk / web 同一版本号） | v0.2.0 已同步 |
| 5 | 全量门禁绿（pytest 3468 exit 0 / typecheck 13 零新增 / vitest 1007+13） | 2026-11-22 实测绿 |

## 1. 构建（可复现，无需凭据）

```bash
cd sdk
python -m build                 # 产出 dist/wfp_sdk-0.2.0-py3-none-any.whl + .tar.gz
python -m twine check dist/*    # 元数据与 README 渲染校验（应双 Passing）
```

- 工具链：`pip install build twine`（开发机工具，不进运行时依赖）。
- 兜底路径：`python -m pip wheel . --no-deps -w dist`（B2 先例，仅 wheel）。

## 2. 本地验证（干净环境，B8b 实测口径）

```bash
# 干净 venv 安装（仅 wheel 依赖 httpx）
python -m venv %TEMP%\wfp_sdk_venv
%TEMP%\wfp_sdk_venv\Scripts\pip install sdk\dist\wfp_sdk-0.2.0-py3-none-any.whl

# 起平台（免 LLM；demo 用 code 节点，须开 CODE_NODE_ENABLED）
set PYTHONPATH=src
set CODE_NODE_ENABLED=true
uvicorn orchestration_platform.api.application_factory:create_app --factory --port 8310

# 另开终端跑 demo（预期逐步 DEMO STEP n OK，终局 DEMO PASS）
%TEMP%\wfp_sdk_venv\Scripts\python sdk\demo\demo.py --base-url http://127.0.0.1:8310
```

B8b 实测（2026-11-22）：DEMO STEP 1–7 OK，`DEMO PASS`，exit 0。

## 3. TestPyPI 演练（凭据后补）

```bash
# 凭据：TestPyPI 账号 + API token（repo-release.md §4 凭据清单）
python -m twine upload --repository testpypi sdk\dist\*

# 独立环境验证（--index-url 指向 TestPyPI）
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple wfp-sdk
python sdk\demo\demo.py --base-url http://127.0.0.1:8310
```

## 4. PyPI 正式上传（凭据后补）

```bash
# 同一构建产物直接上传（不重复构建，保证与验证过的产物逐字节一致）
python -m twine upload sdk\dist\*
```

发布后核对：`https://pypi.org/project/wfp-sdk/` 页面可访问；
`curl -s https://pypi.org/pypi/wfp-sdk/json` 应 200。

## 5. 发布后验证与回填

```bash
pip install wfp-sdk                      # 全新环境安装
python sdk\demo\demo.py --base-url http://127.0.0.1:8310   # DEMO PASS
```

- `README.md` 安装段：`pip install wfp-sdk` 状态注记更新（「发布后」→ 直接可用）。
- `sdk/README.md` 安装段：方式一去掉「发布后」限定。
- `CHANGELOG.md`：补 SDK 发布记录行。
- tag：若发布版本与仓库版本一致则复用既有 tag；不一致（如紧急 patch）则按
  repo-release.md §5 节奏补 tag。

## 6. 回滚（PyPI 政策）

- PyPI 版本**不可删除**，只能 yank：`python -m twine ... ` 或网页操作
  `https://pypi.org/manage/project/wfp-sdk/releases/` → yank。
- yank 后 `pip install wfp-sdk` 不再默认命中该版本，已安装环境不受影响；
  已知问题修复后按 PATCH 版本重新上传（`0.2.1`），禁止复用同一版本号重传。
