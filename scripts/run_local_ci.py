# =============================================================================
# 本地等价 CI 脚本（scripts/run_local_ci.py）
#
# 目标（arch_review_v2_tasks 4.4，CHG-36；AC-03 的 V 部分，OP-06 本地镜像）：
#   按 CI job 同序执行等价检查，输出与 CI 相同口径结果摘要：
#     1. backend lint  —— ruff check .（PYTHONPATH=src）；
#     2. engine import isolation —— M4 B10 六前缀门禁（design D5，
#        scripts/check_engine_imports.py，与 CI backend job 同步插步）；
#     3. backend tests —— pytest 收集（--collect-only -q，与 pytest -q 同发现口径）；
#     4. （--frontend 触发）frontend tests —— web/ 下 vitest 单文件口径
#        （与 CI `npm test` 同运行时，单文件控制沙箱时长 ≤180s，§13.2）。
#
# 降级口径（§13.2/§13.3）：
#   - ruff 不可用（本沙箱未安装）→ 回退 py_compile 全量语法编译（HC-04 兜底），
#     摘要显式标注 fallback，不静默跳过；
#   - 版本取自 pyproject.toml / package.json 既有声明，脚本不引入新版本。
#
# 用法：
#   python scripts/run_local_ci.py               # lint + pytest 收集
#   python scripts/run_local_ci.py --frontend    # 追加前端 vitest 单文件
#   python scripts/run_local_ci.py --full-pytest # pytest 全量替代收集段
#   python scripts/run_local_ci.py --vitest-file src/xxx.test.ts  # 指定前端单测文件
#
# 退出码：0 = 全部通过；1 = 任一步失败；2 = 环境异常（工具缺失且无兜底）。
# =============================================================================

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"

# 前端单测文件默认口径（相对 web/；--vitest-file 可覆盖）
DEFAULT_VITEST_FILE = "src/api/__tests__/orchestrationApiClient.auth.test.ts"

# 单步超时（秒；§13.2 单命令 ≤180s 口径）
STEP_TIMEOUT_SECONDS = 180

# pytest 9.x 收集数提取（-q --collect-only 输出为「路径: 数量」逐文件行，GK-11 口径 ≥2797）
_COLLECT_LINE_RE = re.compile(r"^\S+: (\d+)\s*$")


def _run_step(
    title: str, cmd: list, cwd: Path, env_extra: dict
) -> tuple[bool, str, subprocess.CompletedProcess | None]:
    """执行单步命令，返回 (是否成功, 摘要行, 完成对象)。"""
    env = os.environ.copy()
    env.update(env_extra)
    started = time.monotonic()
    print(f"[run_local_ci] >>> {title}")
    print(f"[run_local_ci]     $ {' '.join(str(c) for c in cmd)}  (cwd={cwd})")
    try:
        completed = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=STEP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"{title}: TIMEOUT（>{STEP_TIMEOUT_SECONDS}s，§13.2 分片口径重试）", None
    except OSError as exc:
        return False, f"{title}: 环境异常 {exc}", None
    elapsed = time.monotonic() - started
    ok = completed.returncode == 0
    tail = (completed.stdout or "").strip().splitlines()
    tail_line = tail[-1] if tail else (completed.stderr or "").strip().splitlines()[-1:] or [""]
    summary = f"{title}: {'PASS' if ok else 'FAIL'} ({elapsed:.1f}s) | {str(tail_line)[:120]}"
    print(f"[run_local_ci]     {'PASS' if ok else 'FAIL'} ({elapsed:.1f}s)")
    if not ok:
        # 失败时打印尾部输出便于定位
        combined = ((completed.stdout or "") + (completed.stderr or "")).strip().splitlines()
        for line in combined[-20:]:
            print(f"    | {line}")
    return ok, summary, completed


def _ruff_available() -> bool:
    probe = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return probe.returncode == 0


def _py_compile_sweep() -> tuple[bool, str]:
    """ruff 不可用时的兜底：py_compile 全量语法编译（HC-04 Python 3.10 边界）。"""
    targets: list[Path] = []
    for pattern in ("src/orchestration_platform/**/*.py", "tests/**/*.py", "scripts/**/*.py"):
        targets.extend(PROJECT_ROOT.glob(pattern))
    compiled = 0
    for path in targets:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return False, f"backend lint (fallback py_compile): FAIL @ {path}"
        compiled += 1
    return True, f"backend lint (fallback py_compile): PASS ({compiled} files)"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CI 本地等价脚本（AC-03 V 部分；与 .github/workflows/ci.yml 同序）"
    )
    parser.add_argument(
        "--frontend",
        action="store_true",
        help="追加前端 vitest 单文件口径（CI frontend job 的本地子集）",
    )
    parser.add_argument(
        "--full-pytest",
        action="store_true",
        help="pytest 全量替代收集段（默认仅 --collect-only 控制时长）",
    )
    parser.add_argument(
        "--vitest-file",
        default=DEFAULT_VITEST_FILE,
        help=f"前端单测文件（相对 web/；默认 {DEFAULT_VITEST_FILE}）",
    )
    args = parser.parse_args()

    summaries: list[tuple[bool, str]] = []
    all_ok = True
    pythonpath_env = {"PYTHONPATH": "src"}

    # ---------- 1. backend lint（CI: ruff check .） ----------
    if _ruff_available():
        ok, summary, _ = _run_step(
            "backend lint (ruff check)",
            [sys.executable, "-m", "ruff", "check", "."],
            PROJECT_ROOT,
            pythonpath_env,
        )
    else:
        print("[run_local_ci] ruff 不可用 → 降级 py_compile 全量语法编译（§13.2 兜底）")
        ok, summary = _py_compile_sweep()
        print(f"[run_local_ci]     {'PASS' if ok else 'FAIL'} | {summary.split('|')[-1].strip()}")
    all_ok = all_ok and ok
    summaries.append((ok, summary))

    # ---------- 2. engine import isolation（CI: check_engine_imports.py；M4 B10 design D5） ----------
    ok, summary, _ = _run_step(
        "engine import isolation (check_engine_imports)",
        [sys.executable, "scripts/check_engine_imports.py"],
        PROJECT_ROOT,
        {},
    )
    all_ok = all_ok and ok
    summaries.append((ok, summary))

    # ---------- 3. backend tests（CI: pytest -q；本地默认收集口径） ----------
    pytest_cmd = [sys.executable, "-m", "pytest"]
    pytest_cmd += ([] if args.full_pytest else ["--collect-only"]) + ["-q"]
    ok, summary, completed = _run_step(
        "backend tests (pytest full)" if args.full_pytest else "backend tests (pytest collect)",
        pytest_cmd,
        PROJECT_ROOT,
        pythonpath_env,
    )
    if ok and completed is not None and not args.full_pytest:
        # 汇总逐文件收集数（GK-11 口径：≥2797 不减）
        collected = 0
        for line in (completed.stdout or "").splitlines():
            match = _COLLECT_LINE_RE.match(line)
            if match:
                collected += int(match.group(1))
        if collected > 0:
            summary = f"{summary} → collected {collected}"
    all_ok = all_ok and ok
    summaries.append((ok, summary))

    # ---------- 4. frontend tests（可选；CI frontend job 的本地单文件子集） ----------
    if args.frontend:
        vitest_target = WEB_DIR / args.vitest_file
        if not vitest_target.is_file():
            print(f"[run_local_ci] 前端单测文件不存在：{args.vitest_file}")
            return 2
        # Windows 下 npx 为 npx.cmd，subprocess 不经 shell 无法直接解析——
        # 用 shutil.which 跨平台定位真实可执行文件
        npx_exe = shutil.which("npx")
        if npx_exe is None:
            print("[run_local_ci] npx 不可用（前端工具链缺失）→ frontend 步骤环境异常")
            return 2
        ok, summary, _ = _run_step(
            f"frontend tests (vitest run {args.vitest_file})",
            [npx_exe, "vitest", "run", args.vitest_file],
            WEB_DIR,
            {},
        )
        all_ok = all_ok and ok
        summaries.append((ok, summary))

    # ---------- 摘要（CI 同口径） ----------
    print()
    print("=" * 72)
    print("[run_local_ci] 结果摘要（CI 同口径）")
    print("=" * 72)
    for step_ok, line in summaries:
        print(f"  [{'PASS' if step_ok else 'FAIL'}] {line}")
    print("=" * 72)
    verdict = "ALL PASS" if all_ok else "FAILED"
    print(f"[run_local_ci] {verdict} — exit {'0' if all_ok else '1'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
