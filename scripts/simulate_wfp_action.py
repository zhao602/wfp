# =============================================================================
# wfp-check 本地模拟脚本（scripts/simulate_wfp_action.py）
#
# 目标（arch_review_v2_tasks 6.5，CHG-37；AC-05 的 V 部分）：
#   构造本地 PR 模拟——对 examples/ 双快照（base/head）执行与
#   .github/workflows/wfp-check.yml 完全一致的命令序列：
#     1. 变更文件发现（added / removed / changed 分类）；
#     2. wfp validate head/examples/（非零 → 场景失败，镜像「失败即阻断」
#        pipefail 语义）；
#     3. wfp diff base/examples/<f> head/examples/<f>（仅双侧均存在且内容
#        有差异的文件；diff 有变更也是 exit 0，信息非错误）。
#
# 场景：
#   A（PR 双快照完全一致）→ validate exit 0，diff 零调用；补充断言同一
#     文件 diff 输出「无三类变更」且 exit 0；
#   B（注入一处参数变更：generate_draft 模型 27b → 14b）→ validate
#     exit 0，diff exit 0 且报 param-change。
#
# 实现：进程内 import CLI main()（脚本自加 src 到 sys.path，无真实子进程）；
#   真实 PR 评论（actions/github-script）属 N 类禁试移交项（§13.1）。
#
# 用法：python scripts/simulate_wfp_action.py
# 退出码：0 = 两场景全过；1 = 任一场景失败。
# =============================================================================

import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
EXAMPLES_DIR = PROJECT_ROOT / "examples"

# 脚本自加 src 到 sys.path：无 PYTHONPATH 环境也可独立运行
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from orchestration_platform.cli import main as cli_main  # noqa: E402

# 场景 B 注入点（conditional_loop_pipeline：generate_draft 模型标识，
# 首次出现处替换；review_draft 的同值字段不受影响）
_INJECT_FILE = "conditional_loop_pipeline/conditional_loop_pipeline.yaml"
_INJECT_OLD = "model_identifier: qwen3.8-27b"
_INJECT_NEW = "model_identifier: qwen3.8-14b"


def _run_cli(argv: list) -> tuple:
    """进程内调用 wfp CLI main()，返回 (exit_code, 合并输出)。"""
    out_buffer = io.StringIO()
    err_buffer = io.StringIO()
    with contextlib.redirect_stdout(out_buffer), contextlib.redirect_stderr(err_buffer):
        exit_code = cli_main(argv)
    output = out_buffer.getvalue()
    err_text = err_buffer.getvalue()
    if err_text:
        output = output + "[stderr] " + err_text
    return exit_code, output


def _discover_changes(base_examples: Path, head_examples: Path) -> dict:
    """镜像 wfp-check「Discover changed example files」：added/removed/changed。"""
    base_files = {
        item.relative_to(base_examples).as_posix()
        for item in base_examples.rglob("*.yaml")
    }
    head_files = {
        item.relative_to(head_examples).as_posix()
        for item in head_examples.rglob("*.yaml")
    }
    changed = sorted(
        name
        for name in base_files & head_files
        if (base_examples / name).read_bytes() != (head_examples / name).read_bytes()
    )
    return {
        "added": sorted(head_files - base_files),
        "removed": sorted(base_files - head_files),
        "changed": changed,
    }


def _last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _mirror_workflow(
    title: str, base_root: Path, head_root: Path
) -> tuple:
    """镜像 wfp-check.yml 命令序列，返回 (是否全过, 摘要行, diff 输出表)。"""
    print(f"\n[simulate_wfp_action] === {title} ===")
    summaries: list = []
    diff_outputs: dict = {}
    all_ok = True
    base_examples = base_root / "examples"
    head_examples = head_root / "examples"

    # 1. 变更文件发现（镜像 Discover changed example files 步骤）
    changes = _discover_changes(base_examples, head_examples)
    print(
        f"[simulate_wfp_action]   变更发现：added={len(changes['added'])} "
        f"removed={len(changes['removed'])} changed={len(changes['changed'])}"
    )

    # 2. wfp validate head/examples/（pipefail 语义：非零 → 失败阻断）
    exit_code, output = _run_cli(["validate", str(head_examples)])
    ok = exit_code == 0
    all_ok = all_ok and ok
    line = f"validate head/examples/: {'PASS' if ok else 'FAIL'} (exit {exit_code}) | {_last_line(output)}"
    summaries.append(line)
    print(f"[simulate_wfp_action]   {line}")

    # 3. wfp diff（仅双侧变更文件；有变更也是 exit 0）
    for name in changes["changed"]:
        exit_code, output = _run_cli(
            ["diff", str(base_examples / name), str(head_examples / name)]
        )
        diff_outputs[name] = (exit_code, output)
        ok = exit_code == 0
        all_ok = all_ok and ok
        line = f"diff {name}: {'PASS' if ok else 'FAIL'} (exit {exit_code}) | {_last_line(output)}"
        summaries.append(line)
        print(f"[simulate_wfp_action]   {line}")
    print(
        f"[simulate_wfp_action]   diff 调用数：{len(diff_outputs)}"
        "（added/removed 文件不参与语义 diff）"
    )
    return all_ok, summaries, diff_outputs


def _scenario_a(workspace: Path) -> tuple:
    """场景 A：PR 双快照完全一致 → validate 0、diff 零调用、同文件 diff 无变更。"""
    base_root = workspace / "scenario_a" / "base"
    head_root = workspace / "scenario_a" / "head"
    shutil.copytree(EXAMPLES_DIR, base_root / "examples")
    shutil.copytree(EXAMPLES_DIR, head_root / "examples")

    all_ok, summaries, diff_outputs = _mirror_workflow(
        "场景 A：PR 双快照完全一致", base_root, head_root
    )

    # 预期 1：零 added/removed/changed → diff 零调用
    changes = _discover_changes(base_root / "examples", head_root / "examples")
    ok = not (changes["added"] or changes["removed"] or changes["changed"])
    ok = ok and not diff_outputs
    all_ok = all_ok and ok
    line = f"场景 A 预期（零变更文件 → diff 零调用）：{'PASS' if ok else 'FAIL'}"
    summaries.append(line)
    print(f"[simulate_wfp_action]   {line}")

    # 补充断言：同一文件 diff → 「无三类变更」且 exit 0（wfp diff 语义）
    sample = sorted((head_root / "examples").rglob("*.yaml"))[0]
    exit_code, output = _run_cli(["diff", str(sample), str(sample)])
    ok = exit_code == 0 and "无三类变更" in output
    all_ok = all_ok and ok
    line = f"补充断言（同文件 diff 无三类变更）：{'PASS' if ok else 'FAIL'} (exit {exit_code})"
    summaries.append(line)
    print(f"[simulate_wfp_action]   {line}")
    return all_ok, summaries


def _scenario_b(workspace: Path) -> tuple:
    """场景 B：注入一处参数变更 → validate 0、diff 0 且报 param-change。"""
    base_root = workspace / "scenario_b" / "base"
    head_root = workspace / "scenario_b" / "head"
    shutil.copytree(EXAMPLES_DIR, base_root / "examples")
    shutil.copytree(EXAMPLES_DIR, head_root / "examples")

    target = head_root / "examples" / _INJECT_FILE
    text = target.read_text(encoding="utf-8")
    if _INJECT_OLD not in text:
        print(f"[simulate_wfp_action] 注入点缺失：{_INJECT_FILE} 不含 {_INJECT_OLD!r}")
        return False, [f"注入点缺失：{_INJECT_FILE}"]
    target.write_text(text.replace(_INJECT_OLD, _INJECT_NEW, 1), encoding="utf-8")

    all_ok, summaries, diff_outputs = _mirror_workflow(
        "场景 B：注入一处参数变更（generate_draft 模型 27b → 14b）",
        base_root,
        head_root,
    )

    # 预期 1：changed 恰为注入文件
    changes = _discover_changes(base_root / "examples", head_root / "examples")
    ok = (
        changes["changed"] == [_INJECT_FILE]
        and not changes["added"]
        and not changes["removed"]
    )
    all_ok = all_ok and ok
    line = f"场景 B 预期（changed 恰为注入文件）：{'PASS' if ok else 'FAIL'}"
    summaries.append(line)
    print(f"[simulate_wfp_action]   {line}")

    # 预期 2：该文件 diff exit 0 且报 param-change（model_identifier）
    exit_code, output = diff_outputs.get(_INJECT_FILE, (1, ""))
    ok = exit_code == 0 and "[param-change]" in output and "model_identifier" in output
    all_ok = all_ok and ok
    line = f"场景 B 预期（param-change + model_identifier）：{'PASS' if ok else 'FAIL'} (exit {exit_code})"
    summaries.append(line)
    print(f"[simulate_wfp_action]   {line}")
    return all_ok, summaries


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(
        "[simulate_wfp_action] wfp-check 本地模拟"
        "（镜像 .github/workflows/wfp-check.yml 命令序列；真实 PR 评论为 N 类禁试移交）"
    )
    with tempfile.TemporaryDirectory(prefix="wfp-simulate-") as tmp:
        workspace = Path(tmp)
        ok_a, summaries_a = _scenario_a(workspace)
        ok_b, summaries_b = _scenario_b(workspace)

    print()
    print("=" * 72)
    print("[simulate_wfp_action] 结果摘要")
    print("=" * 72)
    for line in summaries_a + summaries_b:
        print(f"  {line}")
    print("=" * 72)
    all_ok = ok_a and ok_b
    verdict = "ALL PASS" if all_ok else "FAILED"
    print(f"[simulate_wfp_action] {verdict} — exit {'0' if all_ok else '1'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
