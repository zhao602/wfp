# =============================================================================
# 基准 2/4：并行 8 分支（scripts/perf/bench_parallel_8.py，B8c 性能基线）
# 形状：trigger → fork → 8 × 分支 aggregate（严格扇出，B6 五形状规则）
#       → parallel_join(wait_all/fast_fail) → output_display
# 数据：合成 10,000 行（确定性，design D6）；每分支全量聚合扫描。
# 口径：同 bench_linear_50（start → 终态墙钟；两遍法）。
# 运行：python scripts/perf/bench_parallel_8.py --json
# =============================================================================

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _bench_common as common  # noqa: E402

BRANCH_COUNT = 8
ROW_COUNT = 10000
DEFINITION_ID = "bench_parallel_8"

_DEFINITION = common.build_parallel_definition(DEFINITION_ID, BRANCH_COUNT)
YAML_TEXT = common.definition_to_yaml(_DEFINITION)
INPUTS = {"raw_data": common.make_rows(ROW_COUNT)}


def _round(client) -> dict:
    elapsed, _instance_id = common.run_execution_round(
        client, DEFINITION_ID, YAML_TEXT, INPUTS
    )
    return {"metric_s": elapsed}


def main() -> int:
    args = common.make_arg_parser(
        f"并行 {BRANCH_COUNT} 分支（{ROW_COUNT} 行播种）", default_rounds=5
    ).parse_args()
    result = {
        "bench": "parallel_8",
        "shape": (
            "trigger -> fork -> 8x aggregate branches (strict fanout, "
            "B6 shape rules) -> parallel_join(wait_all) -> output_display"
        ),
        "rows": ROW_COUNT,
        "branches": BRANCH_COUNT,
        "mode": args.mode,
        "meta": common.bench_meta(),
    }
    if args.mode in ("timing", "both"):
        stats, _rounds = common.run_timing_pass(
            "parallel8",
            args.port,
            args.rounds,
            _round,
            setup_callable=lambda client: common.submit_once(client, YAML_TEXT),
            keep_temp=args.keep_temp,
        )
        result["timing"] = stats
        print(
            f"[parallel_8] timing: p50={stats['p50_ms']}ms "
            f"p95={stats['p95_ms']}ms ({stats['rounds']} rounds)"
        )
    if args.mode in ("memory", "both"):
        result["memory"] = common.run_memory_pass(
            "parallel8",
            args.port,
            _round,
            setup_callable=lambda client: common.submit_once(client, YAML_TEXT),
            keep_temp=args.keep_temp,
        )
        mem = result["memory"]
        print(
            f"[parallel_8] memory: client={mem['client_peak_mb']}MB "
            f"server={mem['server_peak_mb']}MB peak={mem['memory_peak_mb']}MB"
        )
    common.emit_result(result, args.json, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
