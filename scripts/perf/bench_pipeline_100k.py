# =============================================================================
# 基准 3/4：10 万行管道（scripts/perf/bench_pipeline_100k.py，B8c 性能基线）
# 形状：trigger → handle_nulls(drop_row，实际剔除 ~9% null 行)
#       → aggregate(group_by category / sum value) → output_display
# 数据：合成 100,000 行（确定性，~9% 周期注入 null，design D6），
#       经 start_instance inputs 播种（POST 体约 6MB JSON，计入计时窗）。
# 口径：同 bench_linear_50；另记 payload_mb。
# 运行：python scripts/perf/bench_pipeline_100k.py --json
# =============================================================================

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _bench_common as common  # noqa: E402

ROW_COUNT = 100000
DEFINITION_ID = "bench_pipeline_100k"

_DEFINITION = common.build_pipeline_definition(DEFINITION_ID)
YAML_TEXT = common.definition_to_yaml(_DEFINITION)
INPUTS = {"raw_data": common.make_rows(ROW_COUNT, with_nulls=True)}
PAYLOAD_MB = round(len(json.dumps(INPUTS)) / (1024 * 1024), 1)


def _round(client) -> dict:
    elapsed, _instance_id = common.run_execution_round(
        client, DEFINITION_ID, YAML_TEXT, INPUTS
    )
    return {"metric_s": elapsed}


def main() -> int:
    args = common.make_arg_parser(
        f"10 万行管道（{ROW_COUNT} 行播种，payload {PAYLOAD_MB}MB）", default_rounds=3
    ).parse_args()
    result = {
        "bench": "pipeline_100k",
        "shape": (
            "trigger -> handle_nulls(drop_row ~9%) -> "
            "aggregate(group_by category / sum value) -> output_display"
        ),
        "rows": ROW_COUNT,
        "payload_mb": PAYLOAD_MB,
        "mode": args.mode,
        "meta": common.bench_meta(),
    }
    if args.mode in ("timing", "both"):
        stats, _rounds = common.run_timing_pass(
            "pipeline100k",
            args.port,
            args.rounds,
            _round,
            setup_callable=lambda client: common.submit_once(client, YAML_TEXT),
            keep_temp=args.keep_temp,
        )
        result["timing"] = stats
        print(
            f"[pipeline_100k] timing: p50={stats['p50_ms']}ms "
            f"p95={stats['p95_ms']}ms ({stats['rounds']} rounds)"
        )
    if args.mode in ("memory", "both"):
        result["memory"] = common.run_memory_pass(
            "pipeline100k",
            args.port,
            _round,
            setup_callable=lambda client: common.submit_once(client, YAML_TEXT),
            keep_temp=args.keep_temp,
        )
        mem = result["memory"]
        print(
            f"[pipeline_100k] memory: client={mem['client_peak_mb']}MB "
            f"server={mem['server_peak_mb']}MB peak={mem['memory_peak_mb']}MB"
        )
    common.emit_result(result, args.json, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
