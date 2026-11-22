# 仓库推送与发布 runbook（B8a · N2）

> 首次提交与 annotated tag `v0.2.0` 已于 2026-11-22 在本地完成（`main` 分支）。
> 本 runbook 覆盖凭据到位后的 remote 挂接、分支保护、CI 首触发与后续版本发布节奏。

## 0. 前置状态核对

```bash
git log --oneline --decorate   # 应见 initial public baseline 提交（HEAD -> main, tag: v0.2.0）
git tag                        # 应见 v0.2.0
git status                     # 应干净（.gitignore 排除项除外）
```

## 1. 挂接 remote 并推送（凭据后补）

```bash
git remote add origin <REMOTE_URL>
git push -u origin main
git push origin v0.2.0         # tag 一并推送
```

推送后核对：远端默认分支为 `main`；tag `v0.2.0` 可见。

## 2. 分支保护

- Settings → Branches → Add branch protection rule → `main`
- 建议：Require status checks to pass before merging（勾选 backend / frontend /
  examples 三个 CI 作业，首触发后可选）。
- 单人维护期可暂缓 PR 强制门，按团队协作情况后补。

## 3. CI 首触发与已知预期

`.github/workflows/ci.yml` + `wfp-check.yml` 已随首次提交入库，推送即触发。预期：

| 作业 | 预期结果 | 说明 |
|---|---|---|
| backend（ruff + pytest） | 绿 | ruff check 零违例；pytest 3468 collected exit 0 |
| examples（wfp validate） | 绿 | 16 YAML 全量校验 exit 0 |
| frontend（typecheck + vitest） | **typecheck 步骤将红** | `tsc --noEmit` 有 13 个历史基线错误（A4 前端债范围）；vitest 1007+13 应绿 |

- **frontend typecheck 红为已知预期**：修复属 roadmap A4（typecheck 清零），
  不在本 runbook 范围。临时缓解选项（二选一，凭据到位后决策）：
  1. 维持现状：接受 typecheck 步骤红，A4 清零后自然转绿（不推荐长期）；
  2. CI 内 typecheck 步骤临时加 `continue-on-error: true`（单行改动，A4 清零后移除）。
- 若 CI 数字与本地门禁不一致，以 [docs/testing.md](testing.md) 口径排查。

## 4. 凭据清单（需用户提供）

| 凭据 | 用途 | 状态 |
|---|---|---|
| Git remote URL + 认证（PAT / SSH key） | 推送 `main` 与 tag | 待提供 |
| PyPI 账号 + API token | `wfp-sdk` 发布（见 [release-sdk.md](release-sdk.md)，B8b） | 待提供 |
| （可选）TestPyPI token | 发布演练 | 待提供 |

## 5. 版本发布节奏（约定）

1. 版本号三处同步升位：根 `pyproject.toml` / `sdk/pyproject.toml` / `web/package.json`。
2. `CHANGELOG.md` 顶部新增版本段。
3. 提交 → annotated tag `vX.Y.Z` → `git push origin main --follow-tags`。
4. tag 附注携带门禁数字（pytest collected / typecheck / vitest），与
   [testing.md](testing.md) 口径一致。

## 6. 待办登记（后补项）

- LICENSE 版权主体当前为占位 `wfp-orchestration contributors`——法人主体确定后，
  一次文本替换（`LICENSE` 附录版权行 + 各源文件如需头部声明时统一追加）。
- `ci.yml` 内「15 个 YAML」注释为历史口径（现为 16），随下次触碰 CI 文件时顺带修正。
