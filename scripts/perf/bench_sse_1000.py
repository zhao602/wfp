# =============================================================================
# 基准 4/4：SSE 1000 事件（scripts/perf/bench_sse_1000.py，B8c 性能基线）
# 形状：trigger → 500 × data_transform（handle_nulls 链，空数据集）→ output_display
#       （spec_version 1.0：spec 1.1 节点数硬上限 200，1.0 上限读
#       WORKFLOW_MAX_NODES=600，harness 注入；事件 ≈ 2N+2 = 1002）
# 口径（design D5/D7）：
#   - setup 段：单实例一次性执行至终态（exec_s 信息项，不入计时窗——
#     local_file 检查点 O(n²) 重解析使大节点数执行本身负重，
#     见 docs/benchmark.md「已知特性」）；
#   - 计时段：同一终态实例的 SSE 回放整流消费，N 轮重复（事件集恒定，
#     执行抖动隔离），事件数断言 ≥1000，度量为 SSE 序列化/分块传输+
#     客户端解析吞吐；
#   - 空数据集依据：检查点体积随链级累缩 O(i×dataset)（已知特性#2），
#     且 local_file append 每步全量重解析（已知特性#1）——空列表合法
#     装配（data_transform_node_executor FR-14/AC-06），数据内容与 SSE
#     吞吐度量无关。
# 运行：python scripts/perf/bench_sse_1000.py --json
# =============================================================================

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _bench_common as common  # noqa: E402

NODE_COUNT = 500
ROW_COUNT = 0  # 空数据集（设计见文件头）
MIN_EVENTS = 1000
DEFINITION_ID = "bench_sse_1000"
# setup 段 POST /instances 受执行期同步 I/O 阻塞（已知特性#3），
# 墙钟 ≈ 实例执行时长（含 O(n²) 重解析），客户端超时须覆盖整执行。
CLIENT_TIMEOUT_SECONDS = 900.0

_DEFINITION = common.build_linear_definition(
    DEFINITION_ID, NODE_COUNT, spec_version="1.0"
)
YAML_TEXT = common.definition_to_yaml(_DEFINITION)
INPUTS = {"raw_data": common.make_rows(ROW_COUNT)}

_holder: dict = {}


def _setup(client) -> None:
    """一次性提交+执行 500 节点链至终态（setup 段，不入计时窗）。"""
    common.submit_once(client, YAML_TEXT)
    t0 = time.perf_counter()
    info = client.start_instance(DEFINITION_ID, YAML_TEXT, inputs=INPUTS)
    final = client.wait_for_completion(
        info.instance_id, timeout=1200.0, poll_interval=0.05
    )
    if final.status != "succeeded":
        raise AssertionError(
            f"实例未成功：{final.status}（instance_id={info.instance_id}）"
        )
    _holder["instance_id"] = info.instance_id
    _holder["exec_s"] = round(time.perf_counter() - t0, 3)


def _round(client) -> dict:
    """单轮计时：终态实例 SSE 回放整流消费（事件集恒定）。"""
    stream_s, event_count, event_types = common.replay_events(
        client, _holder["instance_id"]
    )
    if event_count < MIN_EVENTS:
        raise AssertionError(
            f"回放事件数 {event_count} < {MIN_EVENTS}"
            f"（instance_id={_holder['instance_id']}）"
        )
    return {"metric_s": stream_s, "events": event_count, "event_types": dict(event_types)}


def main() -> int:
    args = common.make_arg_parser(
        f"SSE 1000 事件（{NODE_COUNT} 节点链，单实例多轮回放）", default_rounds=3
    ).parse_args()
    result = {
        "bench": "sse_1000",
        "shape": (
            "trigger -> 500x data_transform(handle_nulls chain, empty dataset) "
            "-> output_display; metric = terminal replay SSE consumption"
        ),
        "rows": ROW_COUNT,
        "nodes": NODE_COUNT,
        "min_events": MIN_EVENTS,
        "mode": args.mode,
        "meta": common.bench_meta(),
    }
    if args.mode in ("timing", "both"):
        stats, rounds = common.run_timing_pass(
            "sse1000",
            args.port,
            args.rounds,
            _round,
            setup_callable=_setup,
            client_timeout=CLIENT_TIMEOUT_SECONDS,
            keep_temp=args.keep_temp,
        )
        result["timing"] = stats
        result["setup_exec_s"] = _holder.get("exec_s")
        result["rounds_detail"] = [
            {"stream_s": round(r["metric_s"], 3), "events": r["events"]}
            for r in rounds
        ]
        result["event_types"] = rounds[-1]["event_types"] if rounds else None
        print(
            f"[sse_1000] setup exec={_holder.get('exec_s')}s; "
            f"stream: p50={stats['p50_ms']}ms p95={stats['p95_ms']}ms "
            f"({stats['rounds']} rounds, {rounds[-1]['events']} events)"
        )
    if args.mode in ("memory", "both"):
        result["memory"] = common.run_memory_pass(
            "sse1000",
            args.port,
            _round,
            setup_callable=_setup,
            client_timeout=CLIENT_TIMEOUT_SECONDS,
            keep_temp=args.keep_temp,
        )
        mem = result["memory"]
        print(
            f"[sse_1000] memory: client={mem['client_peak_mb']}MB "
            f"server={mem['server_peak_mb']}MB peak={mem['memory_peak_mb']}MB"
        )
    common.emit_result(result, args.json, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
