# =============================================================================
# M3 drill 2：双副本并发认领互斥（scripts/drill/m3_two_replica_claim.py）
#
# 职责（roadmap T8 / M2 移交项 ②）：
#   两副本（真实独立进程 A/B）同时运行，并发认领 6 个作业：
#   认领互斥（O_EXCL/SKIP LOCKED 单胜者语义）下每作业仅被一个副本执行，
#   全部完成、零失败、零双执行。
# 断言（留档 JSON）：
#   1. 6 个作业终态全部 completed、attempts 全部 == 1（无接管发生）；
#   2. 每作业每非触发节点恰 1 次执行（双副本无双执行铁证）；
#   3. 认领分布跨两副本（记录各 pid 认领数；并发下分布非确定性，
#      不作硬断言，仅留档）。
# 用法：python scripts/drill/m3_two_replica_claim.py
# 产物：.codeartsdoer/specs/roadmap_m3_cancel_semantics/_drills/
#       m3_two_replica_claim_record.json
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

JOB_COUNT = 6
NODE_BLOCK = 0.3
HEARTBEAT = 0.5
LEASE_TTL = 8
POLL = 0.1
COMPLETE_TIMEOUT = 60.0


def _spawn(base: Path, log_name: str) -> subprocess.Popen:
    log = open(base / log_name, "w", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC
    env["PYTHONUNBUFFERED"] = "1"
    # D9 规避：heartbeat 续租走 settings 默认租约，drill 经 env 对齐
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
            str(NODE_BLOCK),
            str(HEARTBEAT),
            str(LEASE_TTL),
            "0.1",
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
    base = _ARCHIVE / f"tmp-two-replica-{stamp}"
    for sub in ("queue", "calls", "checkpoints", "events"):
        (base / sub).mkdir(parents=True, exist_ok=True)

    queue = LocalFileExecutionQueue(queue_dir=str(base / "queue"))
    job_ids = []
    for i in range(JOB_COUNT):
        job_id = f"job-drill-2r-{i}"
        job_ids.append(job_id)
        await queue.enqueue(
            ExecutionJob(
                job_id=job_id,
                instance_id=f"inst-drill-2r-{i}",
                definition_id="sequential_four_nodes",
                definition_version=1,
                initial_inputs={"topic": f"drill-2r-{i}"},
                trigger_source="api",
                trace_id=f"trace-drill-2r-{i}",
                idempotency_key=f"inst-drill-2r-{i}",
                yaml_text=_YAML.read_text(encoding="utf-8"),
            )
        )

    # 双副本同时拉起（并发认领窗口重叠）
    t0 = time.monotonic()
    procs = [_spawn(base, "worker-a.log"), _spawn(base, "worker-b.log")]

    while True:
        if time.monotonic() - t0 > COMPLETE_TIMEOUT:
            print("FAIL: 作业未在时限内全部完成")
            return 1
        statuses = []
        for job_id in job_ids:
            current = await queue.get(job_id)
            statuses.append(current.status if current else "missing")
        if all(s == "completed" for s in statuses):
            break
        await asyncio.sleep(POLL)
    elapsed = time.monotonic() - t0
    for p in procs:
        p.terminate()
        p.wait(timeout=10)

    calls = _read_calls(base)
    finals = {}
    for job_id in job_ids:
        job = await queue.get(job_id)
        finals[job_id] = {
            "status": job.status,
            "attempts": job.attempts,
        }

    # 每作业每节点执行次数（互斥铁证：== 1）
    per_instance_node_counts: dict = {}
    claim_distribution: dict = {}
    for job_id in job_ids:
        instance = f"inst-{job_id[4:]}"  # job-drill-2r-i -> inst-drill-2r-i
        counts: dict = {}
        pids: dict = {}
        for node in ("read_dataset", "analyze_data", "build_report"):
            hits = [c for c in calls
                    if c["instance"] == instance and c["node"] == node]
            counts[node] = len(hits)
            pids[node] = sorted({h["pid"] for h in hits})
        per_instance_node_counts[instance] = counts
        claim_distribution[instance] = pids["analyze_data"]

    all_completed = all(v["status"] == "completed"
                        for v in finals.values())
    all_attempts_1 = all(v["attempts"] == 1 for v in finals.values())
    all_single_exec = all(
        all(c == 1 for c in counts.values())
        for counts in per_instance_node_counts.values()
    )
    both_replicas_active = all(
        pid in {p.pid for p in procs}
        for pids in claim_distribution.values()
        for pid in pids
    )
    distribution_summary: dict = {}
    for p in procs:
        distribution_summary[p.pid] = sum(
            1 for pids in claim_distribution.values() if p.pid in pids
        )

    checks = {
        "all_completed": all_completed,
        "all_attempts_1_no_takeover": all_attempts_1,
        "every_node_exactly_once_per_job": all_single_exec,
        "all_claims_from_spawned_replicas": both_replicas_active,
    }
    record = {
        "drill": "m3_two_replica_claim",
        "platform": f"{sys.platform} ({os.name})",
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "job_count": JOB_COUNT,
        "elapsed_seconds": round(elapsed, 3),
        "lease_ttl": LEASE_TTL,
        "replica_pids": [p.pid for p in procs],
        "claim_distribution_per_replica": distribution_summary,
        "final_jobs": finals,
        "per_instance_node_execution_counts": per_instance_node_counts,
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
    }
    out = _ARCHIVE / "m3_two_replica_claim_record.json"
    out.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
