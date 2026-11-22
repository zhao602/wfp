# =============================================================================
# 基准服务入口（scripts/perf/_server_main.py，B8c 性能基线）
#
# 职责：uvicorn 程序化启动平台应用；可选 tracemalloc 全程跟踪并在退出时
#       将峰值落盘（服务进程内存口径，见 docs/benchmark.md）。
# 设计意图（design D2/D3/D4）：
#   1. 独立入口而非 `uvicorn ...:create_app --factory`——需要进程内
#      tracemalloc 起停与峰值落盘钩子；
#   2. 优雅停机：posix SIGTERM / windows CTRL_BREAK(SIGBREAK) → SystemExit
#      → uvicorn.run 返回或直接退栈，atexit 兜底写峰值文件；
#   3. 环境变量（STORAGE_BACKEND/LOCAL_STORAGE_ROOT/WORKFLOW_MAX_NODES 等）
#      由调用方（_bench_common.BenchServer）注入进程环境，本入口不读取 .env。
# =============================================================================

import argparse
import atexit
import json
import signal
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="B8c 基准服务入口（uvicorn + 可选 tracemalloc 峰值落盘）"
    )
    parser.add_argument("--port", type=int, required=True, help="监听端口")
    parser.add_argument(
        "--tracemalloc",
        action="store_true",
        help="开启 tracemalloc 跟踪（内存遍用；计时遍勿开——会拖慢分配路径）",
    )
    parser.add_argument(
        "--peak-file", default="", help="退出时写入 tracemalloc 峰值 JSON 的路径"
    )
    args = parser.parse_args()

    tracemalloc = None
    if args.tracemalloc:
        import tracemalloc as _tracemalloc

        tracemalloc = _tracemalloc
        tracemalloc.start()

    from orchestration_platform.api.application_factory import create_app

    app = create_app()

    import uvicorn

    def _write_peak() -> None:
        if not args.peak_file:
            return
        payload = {
            "tracemalloc_enabled": tracemalloc is not None,
            "current_bytes": None,
            "peak_bytes": None,
        }
        if tracemalloc is not None:
            current, peak = tracemalloc.get_traced_memory()
            payload["current_bytes"] = current
            payload["peak_bytes"] = peak
        with open(args.peak_file, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    atexit.register(_write_peak)

    # 信号兜底：本 handler 先于 uvicorn.run 注册；uvicorn 仅接管
    # SIGINT/SIGTERM，windows 下 SIGBREAK 由本 handler 兜底（SystemExit
    # 退栈后 atexit 落盘）。posix SIGTERM 若被 uvicorn 接管则走优雅退出，
    # run 返回后同样触发 atexit——两条路径均落盘。
    def _graceful(signum, frame):  # noqa: ANN001
        raise SystemExit(0)

    for _sig_name in ("SIGBREAK", "SIGTERM"):
        if hasattr(signal, _sig_name):
            signal.signal(getattr(signal, _sig_name), _graceful)

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
