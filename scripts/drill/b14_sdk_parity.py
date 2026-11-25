# -*- coding: utf-8 -*-
# =============================================================================
# B14 T1c drill：SDK 契约对拍（scripts/drill/b14_sdk_parity.py）
#
# 职责（roadmap B14 T2「SDK 对拍」；docs/sdk-contract.md §1 冻结契约面）：
#   真实后端（BenchServer uvicorn 子进程）+ wfp_sdk WorkflowClient 9 方法
#   全序列 wire 对拍——SDK 模型 from_dict 逐响应解析即 wire 同构断言；
#   cancel 走契约 §1-7 终态冲突分支（409 INSTANCE_CANCEL_CONFLICT）。
# 序列：
#   validate（合法→valid True / 非法→valid False 不抛错）→ submit →
#   start_instance（播种 inputs）→ stream_events（SSE 全量回放 + STREAM_END
#   收尾，execution_instance_router.event_generator :126-144）→ poll_events →
#   wait_for_completion → get_results → cancel（终态 409）→ get_instance。
# 断言（留档 JSON checks 块）：
#   1. 非法定义恒返报告不抛错（validate 200 语义）；
#   2. SSE 与轮询事件数逐字一致（同一 read_events 源，wire 同构）；
#   3. 实例终态 succeeded，outputs 携带 d2（线性链末端输出）；
#   4. cancel 已终态实例 → WfpStateConflictError(409, INSTANCE_CANCEL_CONFLICT)。
# 用法：python scripts/drill/b14_sdk_parity.py
# 产物：.codeartsdoer/specs/roadmap_b14_contract_fault_batch/_drills/
#       b14_sdk_parity_record.json
# =============================================================================

import json
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PERF_DIR = _REPO_ROOT / "scripts" / "perf"
if str(_PERF_DIR) not in sys.path:
    sys.path.insert(0, str(_PERF_DIR))

from _bench_common import (  # noqa: E402
    BenchServer,
    build_linear_definition,
    definition_to_yaml,
    make_rows,
)
from wfp_sdk import WorkflowClient  # noqa: E402  # _bench_common 已前置 sdk 路径
from wfp_sdk.errors import (  # noqa: E402
    INSTANCE_CANCEL_CONFLICT,
    WfpStateConflictError,
)
import yaml  # noqa: E402

_ARCHIVE = (
    _REPO_ROOT
    / ".codeartsdoer"
    / "specs"
    / "roadmap_b14_contract_fault_batch"
    / "_drills"
)

_DEFINITION_ID = "b14_parity_drill"
_ROW_COUNT = 50
_CLIENT_TIMEOUT_SECONDS = 120.0
_WAIT_TIMEOUT_SECONDS = 60.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    started_at = datetime.now(timezone.utc)
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    base = _ARCHIVE / f"tmp-parity-{stamp}"
    base.mkdir(parents=True, exist_ok=True)

    port = _free_port()
    server = BenchServer(port=port, workdir=base)
    timings: dict = {}
    record: dict = {
        "drill": "b14_sdk_parity",
        "started_at_utc": started_at.isoformat(),
        "platform": sys.platform,
        "port": port,
        "workflow": {
            "definition_id": _DEFINITION_ID,
            "shape": "linear(2 transform) + display",
            "rows": _ROW_COUNT,
        },
    }

    try:
        t0 = time.monotonic()
        server.start()
        timings["server_start"] = round(time.monotonic() - t0, 3)

        definition = build_linear_definition(_DEFINITION_ID, 2)
        yaml_text = definition_to_yaml(definition)
        rows = make_rows(_ROW_COUNT)
        invalid_yaml = yaml.safe_dump(
            {"spec_version": "1.1", "definition_id": "b14_invalid_drill"},
            allow_unicode=True,
        )

        with WorkflowClient(
            f"http://127.0.0.1:{port}", timeout=_CLIENT_TIMEOUT_SECONDS
        ) as client:
            # 1/2. 校验域：合法 → valid；非法 → 恒返报告不抛错
            t = time.monotonic()
            report = client.validate_definition(yaml_text)
            timings["validate_valid"] = round(time.monotonic() - t, 3)
            t = time.monotonic()
            invalid_report = client.validate_definition(invalid_yaml)
            timings["validate_invalid"] = round(time.monotonic() - t, 3)

            # 3. 提交定义
            t = time.monotonic()
            submission = client.submit_definition(yaml_text)
            timings["submit"] = round(time.monotonic() - t, 3)

            # 4. 启动实例（inputs 注入播种数据集 raw_data）
            t = time.monotonic()
            info = client.start_instance(
                submission.definition_id, yaml_text, inputs={"raw_data": rows}
            )
            timings["start_instance"] = round(time.monotonic() - t, 3)
            record["start_returned_status"] = info.status

            # 5. SSE 订阅（全量回放 + STREAM_END 收尾，生成器自然结束）
            t = time.monotonic()
            stream_events = list(client.stream_events(info.instance_id))
            timings["stream_events"] = round(time.monotonic() - t, 3)

            # 6. 轮询降级通道（同一 read_events 源 → 数量逐字一致）
            t = time.monotonic()
            polled_events = client.poll_events(info.instance_id)
            timings["poll_events"] = round(time.monotonic() - t, 3)

            # 7. 轮询直至终态
            t = time.monotonic()
            final_info = client.wait_for_completion(
                info.instance_id, timeout=_WAIT_TIMEOUT_SECONDS
            )
            timings["wait_for_completion"] = round(time.monotonic() - t, 3)

            # 8. 取结果
            t = time.monotonic()
            results = client.get_results(info.instance_id)
            timings["get_results"] = round(time.monotonic() - t, 3)

            # 9. 取消已终态实例 → 契约 §1-7：409 INSTANCE_CANCEL_CONFLICT
            cancel_error = None
            t = time.monotonic()
            try:
                client.cancel_instance(info.instance_id)
            except WfpStateConflictError as exc:
                cancel_error = exc
            timings["cancel_conflict"] = round(time.monotonic() - t, 3)

            # 10. 终态快照
            snapshot = client.get_instance(info.instance_id)

        stream_types = [e.event_type for e in stream_events]
        checks = {
            "validate_valid_true": report.valid is True,
            "validate_invalid_reported_not_raised": (
                invalid_report.valid is False
                and len(invalid_report.issues) >= 1
            ),
            "submit_definition_id_echoed": (
                submission.definition_id == _DEFINITION_ID
            ),
            "start_instance_id_returned": bool(info.instance_id),
            "stream_events_nonempty": len(stream_events) >= 1,
            "stream_terminal_event_present": (
                "INSTANCE_COMPLETED" in stream_types
            ),
            "poll_events_count_matches_stream": (
                len(polled_events) == len(stream_events)
            ),
            "wait_terminal_succeeded": final_info.status == "succeeded",
            "results_outputs_present": (
                results.outputs is not None and "d2" in results.outputs
            ),
            "cancel_conflict_409": (
                cancel_error is not None
                and cancel_error.code == INSTANCE_CANCEL_CONFLICT
                and cancel_error.http_status == 409
            ),
            "get_instance_final_succeeded": snapshot.status == "succeeded",
        }
        record.update(
            {
                "instance_id": info.instance_id,
                "stream_event_count": len(stream_events),
                "poll_event_count": len(polled_events),
                "stream_event_types": stream_types,
                "final_status": final_info.status,
                "results_output_names": (
                    sorted(results.outputs.keys()) if results.outputs else []
                ),
                "cancel_conflict": (
                    {
                        "code": cancel_error.code,
                        "http_status": cancel_error.http_status,
                    }
                    if cancel_error is not None
                    else None
                ),
                "timings_seconds": timings,
                "checks": checks,
            }
        )
    except Exception as exc:  # noqa: BLE001  # drill 级兜底：留档后非零退出
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["checks"] = {}
        record["verdict"] = "FAIL"
        _write_record(record, started_at)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 1
    finally:
        server.stop()

    record["verdict"] = "PASS" if all(record["checks"].values()) else "FAIL"
    _write_record(record, started_at)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0 if record["verdict"] == "PASS" else 1


def _write_record(record: dict, started_at: datetime) -> None:
    record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    _ARCHIVE.mkdir(parents=True, exist_ok=True)
    out = _ARCHIVE / "b14_sdk_parity_record.json"
    out.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    sys.exit(main())
