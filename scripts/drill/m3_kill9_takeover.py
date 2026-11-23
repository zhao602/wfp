# =============================================================================
# M3 drill 1：kill -9 强杀接管（scripts/drill/m3_kill9_takeover.py）
#
# 职责（roadmap T8 / M2 移交项 ①）：
#   真实进程强杀（Windows taskkill /F /T，SIGKILL 等价：无清理、无
#   finally、无心跳停止）→ 租约到期 → 副本 B 启动扫描接管（attempts=2）
#   → 从最近节点检查点续跑 → 作业完成。
# 断言（留档 JSON）：
#   1. 作业终态 completed，attempts=2（A 认领 1 + B 接管 1）；
#   2. 已检查点节点零重放（read_dataset 仅 1 次，trigger 为真实执行器
#      不落 calls）；
#   3. 中断节点 at-least-once 重放（analyze_data 恰 2 次：A 中断 1 次 +
#      B 续跑 1 次）；
#   4. 后续节点单次执行（build_report 1 次，仅 B）；
#   5. A/B 为不同进程（calls 落 pid）。
# 用法：python scripts/drill/m3_kill9_takeover.py
# 产物：.codeartsdoer/specs/roadmap_m3_cancel_semantics/_drills/
#       m3_kill9_takeover_record.json
# =============================================================================

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = str(_REPO_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from orchestration_platform.domain.models.execution_job import (  # noqa: E402
    ExecutionJob,
)
from orchestration_platform.persistence.local_file.local_file_execution_queue import (  # noqa: E402
    LocalFileExecutionQueue,
)

_ARCHIVE = (
    _REPO_ROOT
    / ".codeartsdoer"
    / "specs"
    / "roadmap_m3_cancel_semantics"
    / "_drills"
)
_RUNNER = Path(__file__).resolve().parent / "_m3_drill_worker.py"
_YAML = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "valid_workflow_definitions"
    / "valid_sequential_four_nodes.yaml"
)

# 时序参数（秒）
AGENT_BLOCK = 3.0
HEARTBEAT = 0.4
LEASE_TTL = 2
LEASE_MARGIN = 0.6
POLL = 0.1
CLAIM_TIMEOUT = 20.0
AGENT_START_TIMEOUT = 20.0
COMPLETE_TIMEOUT = 40.0


def _spawn(base: Path, log_name: str) -> subprocess.Popen:
    log = open(base / log_name, "w", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC
    env["PYTHONUNBUFFERED"] = "1"
    # D9 规避：heartbeat 续租走 settings 默认租约（接口无 TTL 参），
    # drill 经 env 对齐全链租约 = LEASE_TTL（接管时延即 lease_ttl 设计值）
    env["EXECUTION_QUEUE_LEASE_SECONDS"] = str(LEASE_TTL)
    return subprocess.Popen(
        [
            sys.executable,
            str(_RUNNER),
            str(base / "queue"),
            str(base / "calls"),
            str(base / "checkpoints"),
            str(base / "events"),
            str(_YAML),
            str(AGENT_BLOCK),
            str(HEARTBEAT),
            str(LEASE_TTL),
            "0.2",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=str(_REPO_ROOT),
    )


def _read_calls(base: Path) -> list:
    lines: list = []
    calls_dir = base / "calls"
    if not calls_dir.exists():
        return lines
    for f in sorted(calls_dir.glob("calls-*.jsonl")):
        for raw in f.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                instance, node, pid, ts = raw.split("|")
                lines.append(
                    {"instance": instance, "node": node,
                     "pid": int(pid), "ts": float(ts)}
                )
    lines.sort(key=lambda x: x["ts"])
    return lines


async def main() -> int:
    started_at = datetime.now(timezone.utc)
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    base = _ARCHIVE / f"tmp-kill9-{stamp}"
    for sub in ("queue", "calls", "checkpoints", "events"):
        (base / sub).mkdir(parents=True, exist_ok=True)

    queue = LocalFileExecutionQueue(queue_dir=str(base / "queue"))
    job = ExecutionJob(
        job_id="job-drill-kill9",
        instance_id="inst-drill-kill9",
        definition_id="sequential_four_nodes",
        definition_version=1,
        initial_inputs={"topic": "drill-kill9"},
        trigger_source="api",
        trace_id="trace-drill-kill9",
        idempotency_key="inst-drill-kill9",
        yaml_text=_YAML.read_text(encoding="utf-8"),
    )
    await queue.enqueue(job)

    t0 = time.monotonic()
    proc_a = _spawn(base, "worker-a.log")
    # 1) A 认领
    while True:
        if time.monotonic() - t0 > CLAIM_TIMEOUT:
            print("FAIL: A 未在时限内认领")
            return 1
        current = await queue.get("job-drill-kill9")
        if current is not None and current.status == "running":
            break
        await asyncio.sleep(POLL)
    claim_a_at = time.monotonic() - t0

    # 2) 等 agent 节点开始执行（中途强杀点：无检查点 → at-least-once 重放）
    t1 = time.monotonic()
    while True:
        if time.monotonic() - t1 > AGENT_START_TIMEOUT:
            print("FAIL: agent 节点未在时限内开始")
            return 1
        calls = _read_calls(base)
        if any(c["node"] == "analyze_data" for c in calls):
            break
        await asyncio.sleep(POLL)
    agent_started_after = time.monotonic() - t1

    # 3) taskkill /F /T（SIGKILL 等价强杀：无清理无心跳停止）
    kill_started = time.monotonic()
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(proc_a.pid)],
        capture_output=True,
        check=False,
    )
    proc_a.wait(timeout=10)
    kill_duration = time.monotonic() - kill_started

    # 4) 等租约到期（B 启动扫描接管前提）
    await asyncio.sleep(LEASE_TTL + LEASE_MARGIN)

    t2 = time.monotonic()
    proc_b = _spawn(base, "worker-b.log")
    # 5) B 接管 + 续跑 + 完成
    while True:
        if time.monotonic() - t2 > COMPLETE_TIMEOUT:
            print("FAIL: 作业未在时限内完成")
            return 1
        current = await queue.get("job-drill-kill9")
        if current is not None and current.status == "completed":
            break
        await asyncio.sleep(POLL)
    complete_b_after = time.monotonic() - t2
    proc_b.terminate()
    proc_b.wait(timeout=10)

    final = await queue.get("job-drill-kill9")
    calls = _read_calls(base)

    def _count(node: str) -> int:
        return sum(1 for c in calls if c["node"] == node)

    def _pids(node: str) -> list:
        return [c["pid"] for c in calls if c["node"] == node]

    node_counts = {
        "read_dataset": _count("read_dataset"),
        "analyze_data": _count("analyze_data"),
        "build_report": _count("build_report"),
    }
    checks = {
        "final_completed": final.status == "completed",
        "attempts_2": final.attempts == 2,
        "checkpointed_node_not_replayed": node_counts["read_dataset"] == 1,
        "interrupted_node_at_least_once_replay": (
            node_counts["analyze_data"] == 2
        ),
        "downstream_node_single_exec": node_counts["build_report"] == 1,
        "distinct_worker_processes": (
            proc_a.pid != proc_b.pid
            and set(_pids("analyze_data")) == {proc_a.pid, proc_b.pid}
        ),
    }
    record = {
        "drill": "m3_kill9_takeover",
        "platform": f"{sys.platform} ({os.name})",
        "kill_method": "taskkill /F /T (SIGKILL equivalent, no cleanup)",
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "timing_seconds": {
            "a_claim": round(claim_a_at, 3),
            "agent_started_after_claim": round(agent_started_after, 3),
            "kill_duration": round(kill_duration, 3),
            "lease_ttl": LEASE_TTL,
            "lease_expiry_wait": LEASE_TTL + LEASE_MARGIN,
            "b_takeover_to_complete": round(complete_b_after, 3),
        },
        "worker_a_pid": proc_a.pid,
        "worker_b_pid": proc_b.pid,
        "job": {
            "job_id": final.job_id,
            "instance_id": final.instance_id,
            "final_status": final.status,
            "attempts": final.attempts,
        },
        "node_execution_counts": node_counts,
        "node_execution_pids": {
            "read_dataset": _pids("read_dataset"),
            "analyze_data": _pids("analyze_data"),
            "build_report": _pids("build_report"),
        },
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
    }
    out = _ARCHIVE / "m3_kill9_takeover_record.json"
    out.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
