# =============================================================================
# 基准共享 harness（scripts/perf/_bench_common.py，B8c 性能基线）
#
# 职责：4 个基准脚本（bench_*.py）的公共设施——
#   1. 服务生命周期 BenchServer：uvicorn 子进程（env 覆盖 / healthz 就绪 /
#      优雅停机 / tracemalloc 峰值读取），design D4；
#   2. 两遍法编排 run_timing_pass / run_memory_pass（design D2/D3）；
#   3. 合成数据与工作流 YAML 构建（design D5/D6，全确定性无随机）；
#   4. 统计（p50/p95 最近秩）与 JSON 输出。
# 纪律：
#   1. 基准只经公开 API 面（wfp_sdk 9 方法），零 import orchestration_platform；
#   2. stdlib + httpx/pyyaml（wfp_sdk 传递依赖所在环境必有），不引 psutil；
#   3. 计时遍全程无 tracemalloc（D2），内存遍双进程 tracemalloc。
# =============================================================================

import argparse
import json
import os
import platform
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"
_SDK_DIR = _REPO_ROOT / "sdk"

if str(_SDK_DIR) not in sys.path:
    sys.path.insert(0, str(_SDK_DIR))

from wfp_sdk import WorkflowClient  # noqa: E402

DEFAULT_PORT = 8320
WORKFLOW_MAX_NODES_OVERRIDE = "600"
_HEALTHZ_TIMEOUT_SECONDS = 60.0
# 实例受理响应受执行期同步 I/O 阻塞（见 docs/benchmark.md「已知特性」），
# 单实例 POST /instances 墙钟 ≈ 实例执行时长——客户端超时须覆盖整执行。
CLIENT_TIMEOUT_SECONDS = 120.0


# ---------------------------------------------------------------- statistics

def percentile_nearest_rank(values: List[float], pct: float) -> float:
    """最近秩百分位（无插值——样本量小且口径透明）。"""
    if not values:
        raise ValueError("percentile_nearest_rank: 空样本")
    ordered = sorted(values)
    k = max(1, int(pct / 100.0 * len(ordered) + 0.999999))
    return ordered[min(k, len(ordered)) - 1]


def summarize_seconds(samples: List[float]) -> Dict[str, Any]:
    """秒样本 → 毫秒统计块（benchmark.md 表格同口径）。"""
    ms = [v * 1000.0 for v in samples]
    return {
        "rounds": len(samples),
        "p50_ms": round(percentile_nearest_rank(ms, 50), 1),
        "p95_ms": round(percentile_nearest_rank(ms, 95), 1),
        "min_ms": round(min(ms), 1),
        "max_ms": round(max(ms), 1),
        "mean_ms": round(statistics.fmean(ms), 1),
    }


def bench_meta() -> Dict[str, Any]:
    """环境摘要（benchmark.md 硬件/OS/Python 声明的数据来源）。"""
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ------------------------------------------------------- 合成数据与 YAML 构建

def make_rows(count: int, with_nulls: bool = False) -> List[Dict[str, Any]]:
    """确定性合成行集（design D6）：{id, category(8 桶), value}。

    with_nulls=True 时按 id 周期注入 null（每 20 行 value、每 25 行
    category）→ handle_nulls(drop_row) 实际剔除 ~9% 行，清洗非空转。
    """
    rows: List[Dict[str, Any]] = []
    for i in range(count):
        row: Dict[str, Any] = {
            "id": i,
            "category": f"cat_{i % 8}",
            "value": (i * 37) % 1000,
        }
        if with_nulls:
            if i % 20 == 0:
                row["value"] = None
            if i % 25 == 0:
                row["category"] = None
        rows.append(row)
    return rows


_HANDLE_NULLS_PARAMS: Dict[str, Any] = {
    "null_fields": ["category", "value"],
    "null_value_scope": ["none", "empty", "missing"],
    "strategy": "drop_row",
}


def _trigger_node() -> Dict[str, Any]:
    return {
        "node_id": "trigger_start",
        "name": "手动触发",
        "node_type": "trigger",
        "configuration": {"trigger_type": "manual", "input_template": {}},
    }


def _transform_node(node_id: str, output_name: str, reference: str) -> Dict[str, Any]:
    return {
        "node_id": node_id,
        "name": f"清洗_{node_id}",
        "node_type": "data_transform",
        "output_name": output_name,
        "configuration": {
            "operation": "handle_nulls",
            "input_dataset_reference": reference,
            "params": dict(_HANDLE_NULLS_PARAMS),
        },
    }


def _aggregate_node(node_id: str, output_name: str, reference: str) -> Dict[str, Any]:
    return {
        "node_id": node_id,
        "name": f"聚合_{node_id}",
        "node_type": "data_transform",
        "output_name": output_name,
        "configuration": {
            "operation": "aggregate",
            "input_dataset_reference": reference,
            "params": {
                "group_by": ["category"],
                "aggregate_function": "sum",
                "aggregate_field": "value",
            },
        },
    }


def _display_node(references: List[str]) -> Dict[str, Any]:
    return {
        "node_id": "display_result",
        "name": "输出展示",
        "node_type": "output_display",
        "configuration": {
            "input_reference": list(references),
            "display_title": "基准输出",
        },
    }


def _edge(index: int, source: str, target: str) -> Dict[str, Any]:
    return {"edge_id": f"e{index}", "source_node_id": source, "target_node_id": target}


def _definition(
    definition_id: str,
    name: str,
    outputs: List[str],
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    spec_version: str = "1.1",
) -> Dict[str, Any]:
    """定义文档构建。spec_version 可覆盖——SSE 基准 500 节点链走 1.0：
    spec 1.1 节点数硬上限 200（yaml_workflow_parser:109），1.0 上限读
    WORKFLOW_MAX_NODES（harness 已覆盖 600，design D4）。"""
    return {
        "spec_version": spec_version,
        "definition_id": definition_id,
        "name": name,
        "definition_version": 1,
        "inputs": [
            {
                "name": "raw_data",
                "type": "array",
                "required": True,
                "description": "播种数据集（合成，start_instance inputs 注入）",
            }
        ],
        "outputs": [
            {"name": n, "type": "object", "description": "基准输出"} for n in outputs
        ],
        "nodes": nodes,
        "edges": edges,
    }


def build_linear_definition(
    definition_id: str,
    node_count: int,
    spec_version: str = "1.1",
) -> Dict[str, Any]:
    """线性链：trigger → t1..tN（handle_nulls 逐级引用）→ output_display。

    t1 引用播种数据集 raw_data（定义级 inputs 声明，静态登记表合法，
    design D5），t_i 引用 t_{i-1} 输出 d_{i-1}。
    """
    nodes: List[Dict[str, Any]] = [_trigger_node()]
    edges: List[Dict[str, Any]] = []
    prev = "trigger_start"
    for i in range(1, node_count + 1):
        reference = "raw_data" if i == 1 else f"d{i - 1}"
        nodes.append(_transform_node(f"t{i}", f"d{i}", reference))
        edges.append(_edge(i, prev, f"t{i}"))
        prev = f"t{i}"
    final_output = f"d{node_count}"
    nodes.append(_display_node([final_output]))
    edges.append(_edge(node_count + 1, prev, "display_result"))
    return _definition(
        definition_id,
        f"线性链基准（{node_count} 节点）",
        [final_output],
        nodes,
        edges,
        spec_version=spec_version,
    )


def build_parallel_definition(definition_id: str, branch_count: int) -> Dict[str, Any]:
    """严格扇出（B6 五形状规则，design D5）：

    trigger → fork（handle_nulls→d0）→ b1..bN（aggregate，单节点分支）
    → join（parallel_join/wait_all/fast_fail，parallel_branches=[b1..bN]）
    → output_display。
    """
    nodes: List[Dict[str, Any]] = [_trigger_node(), _transform_node("fork_data", "d0", "raw_data")]
    edges: List[Dict[str, Any]] = [_edge(1, "trigger_start", "fork_data")]
    branch_ids = [f"b{i}" for i in range(1, branch_count + 1)]
    edge_index = 2
    outputs: List[str] = []
    for i, branch_id in enumerate(branch_ids, start=1):
        output_name = f"a{i}"
        outputs.append(output_name)
        nodes.append(_aggregate_node(branch_id, output_name, "d0"))
        edges.append(_edge(edge_index, "fork_data", branch_id))
        edge_index += 1
    nodes.append(
        {
            "node_id": "parallel_join",
            "name": "并行汇聚",
            "node_type": "control_flow",
            "configuration": {
                "control_flow_kind": "parallel_join",
                "parallel_branches": list(branch_ids),
                "wait_policy": "wait_all",
                "parallel_failure_strategy": "fast_fail",
            },
        }
    )
    for branch_id in branch_ids:
        edges.append(_edge(edge_index, branch_id, "parallel_join"))
        edge_index += 1
    nodes.append(_display_node(outputs))
    edges.append(_edge(edge_index, "parallel_join", "display_result"))
    return _definition(
        definition_id, f"并行扇出基准（{branch_count} 分支）", outputs, nodes, edges
    )


def build_pipeline_definition(definition_id: str) -> Dict[str, Any]:
    """10 万行管道：trigger → handle_nulls(drop_row) → aggregate → display。"""
    nodes: List[Dict[str, Any]] = [
        _trigger_node(),
        _transform_node("clean_data", "cleaned_data", "raw_data"),
        _aggregate_node("aggregate_data", "aggregate_result", "cleaned_data"),
        _display_node(["aggregate_result"]),
    ]
    edges: List[Dict[str, Any]] = [
        _edge(1, "trigger_start", "clean_data"),
        _edge(2, "clean_data", "aggregate_data"),
        _edge(3, "aggregate_data", "display_result"),
    ]
    return _definition(
        definition_id, "10 万行管道基准", ["aggregate_result"], nodes, edges
    )


def definition_to_yaml(definition: Dict[str, Any]) -> str:
    return yaml.safe_dump(definition, allow_unicode=True, sort_keys=False)


# --------------------------------------------------------------- 服务生命周期

class BenchServer:
    """基准用 uvicorn 服务子进程（design D4）。

    env 覆盖（真实环境变量 > .env，pydantic-settings 优先级）：
    PYTHONPATH(src 前置) / STORAGE_BACKEND / LOCAL_STORAGE_ROOT / WORKFLOW_MAX_NODES。
    优雅停机：windows CTRL_BREAK_EVENT（进程组）/ posix SIGTERM；超时兜底 kill。
    """

    def __init__(self, port: int, workdir: Path, tracemalloc: bool = False) -> None:
        self.port = port
        self.workdir = workdir
        self.tracemalloc = tracemalloc
        self.data_dir = workdir / "data"
        self.log_path = workdir / "server.log"
        self.peak_path = workdir / "server_peak.json"
        self._proc: Optional[subprocess.Popen] = None
        self._log_fh = None

    def start(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        env["STORAGE_BACKEND"] = "local_file"
        env["LOCAL_STORAGE_ROOT"] = str(self.data_dir)
        env["WORKFLOW_MAX_NODES"] = WORKFLOW_MAX_NODES_OVERRIDE
        cmd = [
            sys.executable,
            str(_REPO_ROOT / "scripts" / "perf" / "_server_main.py"),
            "--port",
            str(self.port),
            "--peak-file",
            str(self.peak_path),
        ]
        if self.tracemalloc:
            cmd.append("--tracemalloc")
        self._log_fh = open(self.log_path, "w", encoding="utf-8")
        popen_kwargs: Dict[str, Any] = {
            "env": env,
            "cwd": str(_REPO_ROOT),
            "stdout": self._log_fh,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        self._proc = subprocess.Popen(cmd, **popen_kwargs)
        self._wait_healthz()

    def _wait_healthz(self) -> None:
        deadline = time.monotonic() + _HEALTHZ_TIMEOUT_SECONDS
        url = f"http://127.0.0.1:{self.port}/healthz"
        while time.monotonic() < deadline:
            if self._proc is None:
                raise RuntimeError("BenchServer 未启动")
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"基准服务进程提前退出（码 {self._proc.returncode}），"
                    f"日志：{self.log_path}"
                )
            try:
                resp = httpx.get(url, timeout=1.0)
                if resp.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise TimeoutError(
            f"基准服务 {_HEALTHZ_TIMEOUT_SECONDS}s 内未就绪，日志：{self.log_path}"
        )

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            if os.name == "nt":
                os.kill(self._proc.pid, signal.CTRL_BREAK_EVENT)
            else:
                self._proc.terminate()
            try:
                self._proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=10)
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None

    def read_server_peak(self) -> Optional[int]:
        """读服务进程 tracemalloc 峰值（stop 之后调用；缺失返回 None）。"""
        if not self.peak_path.exists():
            return None
        with open(self.peak_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        peak = payload.get("peak_bytes")
        return int(peak) if peak is not None else None


# ------------------------------------------------------------------- 轮次原语

def submit_once(client: WorkflowClient, yaml_text: str) -> str:
    """每服务会话提交一次定义（正典 demo 路径）；轮次仅 start_instance。"""
    submission = client.submit_definition(yaml_text)
    return submission.definition_id


def run_execution_round(
    client: WorkflowClient,
    definition_id: str,
    yaml_text: str,
    inputs: Dict[str, Any],
    wait_poll_interval: float = 0.05,
    wait_timeout: float = 900.0,
) -> Tuple[float, str]:
    """单轮执行计时：start_instance 发起 → 终态 succeeded（design D7）。"""
    t0 = time.perf_counter()
    info = client.start_instance(definition_id, yaml_text, inputs=inputs)
    final = client.wait_for_completion(
        info.instance_id, timeout=wait_timeout, poll_interval=wait_poll_interval
    )
    elapsed = time.perf_counter() - t0
    if final.status != "succeeded":
        raise AssertionError(
            f"实例未成功：{final.status}（instance_id={info.instance_id}）"
        )
    return elapsed, info.instance_id


def replay_events(
    client: WorkflowClient, instance_id: str
) -> Tuple[float, int, Counter]:
    """终态回放消费计时：流开始 → 迭代自然结束（STREAM_END 后，design D5/D7）。"""
    t0 = time.perf_counter()
    counts: Counter = Counter()
    for event in client.stream_events(instance_id):
        counts[event.event_type] += 1
    elapsed = time.perf_counter() - t0
    return elapsed, sum(counts.values()), counts


# ----------------------------------------------------------------- 两遍法编排

RoundCallable = Callable[[WorkflowClient], Dict[str, Any]]
SetupCallable = Callable[[WorkflowClient], None]


def _fresh_workdir(tag: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"wfp_bench_{tag}_"))


def run_timing_pass(
    bench_tag: str,
    port: int,
    rounds: int,
    round_callable: RoundCallable,
    setup_callable: Optional[SetupCallable] = None,
    warmup: int = 1,
    metric_key: str = "metric_s",
    client_timeout: float = CLIENT_TIMEOUT_SECONDS,
    keep_temp: bool = False,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """计时遍（design D2）：无 tracemalloc；1 预热轮 + N 计时轮。"""
    workdir = _fresh_workdir(f"{bench_tag}_time")
    server = BenchServer(port=port, workdir=workdir, tracemalloc=False)
    try:
        server.start()
        base_url = f"http://127.0.0.1:{port}"
        with WorkflowClient(base_url, timeout=client_timeout) as client:
            if setup_callable is not None:
                setup_callable(client)
            for _ in range(warmup):
                round_callable(client)
            round_results = [round_callable(client) for _ in range(rounds)]
    finally:
        server.stop()
        if not keep_temp:
            shutil.rmtree(workdir, ignore_errors=True)
    stats = summarize_seconds([float(r[metric_key]) for r in round_results])
    return stats, round_results


def run_memory_pass(
    bench_tag: str,
    port: int,
    round_callable: RoundCallable,
    setup_callable: Optional[SetupCallable] = None,
    client_timeout: float = CLIENT_TIMEOUT_SECONDS,
    keep_temp: bool = False,
) -> Dict[str, Any]:
    """内存遍（design D2/D3）：bench + 服务双进程 tracemalloc，1 轮。

    客户端峰值 = 被测轮次窗口内 bench 进程高水位（客户端构造在窗外）；
    服务端峰值 = 服务进程全程高水位（含应用装配——benchmark.md 口径注明）。
    """
    import tracemalloc

    workdir = _fresh_workdir(f"{bench_tag}_mem")
    server = BenchServer(port=port, workdir=workdir, tracemalloc=True)
    result: Dict[str, Any] = {}
    try:
        server.start()
        base_url = f"http://127.0.0.1:{port}"
        client = WorkflowClient(base_url, timeout=client_timeout)
        if setup_callable is not None:
            setup_callable(client)
        tracemalloc.start()
        t0 = time.perf_counter()
        round_callable(client)
        result["round_s"] = round(time.perf_counter() - t0, 3)
        _, client_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        client.close()
        result["client_peak_mb"] = round(client_peak / (1024 * 1024), 1)
    finally:
        server.stop()
        server_peak = server.read_server_peak()
        result["server_peak_mb"] = (
            round(server_peak / (1024 * 1024), 1) if server_peak is not None else None
        )
        if not keep_temp:
            shutil.rmtree(workdir, ignore_errors=True)
    peaks = [v for v in (result.get("client_peak_mb"), result.get("server_peak_mb")) if v is not None]
    result["memory_peak_mb"] = max(peaks) if peaks else None
    return result


# --------------------------------------------------------------------- CLI

def make_arg_parser(bench_label: str, default_rounds: int) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"B8c 性能基准：{bench_label}（口径见 docs/benchmark.md）"
    )
    parser.add_argument(
        "--rounds", type=int, default=default_rounds, help="计时轮数（另有 1 预热轮）"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="基准服务端口（默认 8320）"
    )
    parser.add_argument(
        "--mode",
        choices=("timing", "memory", "both"),
        default="both",
        help="timing=仅计时遍 / memory=仅内存遍 / both=先计时后内存（默认）",
    )
    parser.add_argument(
        "--json", action="store_true", help="末尾输出完整 JSON 结果（机器可读）"
    )
    parser.add_argument(
        "--out",
        default=None,
        help="将完整 JSON 结果另存到该路径（正式采集用，文件为纯 JSON）",
    )
    parser.add_argument(
        "--keep-temp", action="store_true", help="保留临时目录（排障：服务日志/峰值文件）"
    )
    return parser


def emit_result(
    result: Dict[str, Any], as_json: bool, out_path: Optional[str] = None
) -> None:
    if out_path:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"[result] saved -> {out_path}")
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
