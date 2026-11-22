# =============================================================================
# 基准 1/4：线性 50 节点（scripts/perf/bench_linear_50.py，B8c 性能基线）
# 形状：trigger → 50 × data_transform（handle_nulls 链）→ output_display
# 数据：合成 100 行小数据集（确定性，design D5/D6——侧测每节点固定开销）。
# 口径：start_instance → 终态 succeeded 墙钟（p50/p95，预热 1 轮）；
#       内存遍双进程 tracemalloc 峰值（design D2/D3）。
# 运行：python scripts/perf/bench_linear_50.py --json
# =============================================================================

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _bench_common as common  # noqa: E402

NODE_COUNT = 50
# 小数据集口径（design D5）：线性基准测每节点调度/检查点固定开销，
# 数据量敏感性由 bench_pipeline_100k 覆盖；检查点随链级累缩 O(i×dataset)
# （docs/benchmark.md「已知特性」），行数过大将使 O(n²) I/O 二次膨胀。
ROW_COUNT = 100
DEFINITION_ID = "bench_linear_50"

_DEFINITION = common.build_linear_definition(DEFINITION_ID, NODE_COUNT)
YAML_TEXT = common.definition_to_yaml(_DEFINITION)
INPUTS = {"raw_data": common.make_rows(ROW_COUNT)}


def _round(client) -> dict:
    elapsed, _instance_id = common.run_execution_round(
        client, DEFINITION_ID, YAML_TEXT, INPUTS
    )
    return {"metric_s": elapsed}


def main() -> int:
    args = common.make_arg_parser(
        f"线性 {NODE_COUNT} 节点（{ROW_COUNT} 行播种）", default_rounds=5
    ).parse_args()
    result = {
        "bench": "linear_50",
        "shape": "trigger -> 50x data_transform(handle_nulls chain) -> output_display",
        "rows": ROW_COUNT,
        "nodes": NODE_COUNT,
        "mode": args.mode,
        "meta": common.bench_meta(),
    }
    if args.mode in ("timing", "both"):
        stats, _rounds = common.run_timing_pass(
            "linear50",
            args.port,
            args.rounds,
            _round,
            setup_callable=lambda client: common.submit_once(client, YAML_TEXT),
            keep_temp=args.keep_temp,
        )
        result["timing"] = stats
        print(
            f"[linear_50] timing: p50={stats['p50_ms']}ms "
            f"p95={stats['p95_ms']}ms ({stats['rounds']} rounds)"
        )
    if args.mode in ("memory", "both"):
        result["memory"] = common.run_memory_pass(
            "linear50",
            args.port,
            _round,
            setup_callable=lambda client: common.submit_once(client, YAML_TEXT),
            keep_temp=args.keep_temp,
        )
        mem = result["memory"]
        print(
            f"[linear_50] memory: client={mem['client_peak_mb']}MB "
            f"server={mem['server_peak_mb']}MB peak={mem['memory_peak_mb']}MB"
        )
    common.emit_result(result, args.json, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
