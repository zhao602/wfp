# =============================================================================
# A2 子进程通道 runner（engine/nodes/code_execution/subprocess_runner.py）
#
# 职责：代码节点脚本的受限子进程执行通道（llm_code_nodes_v2 任务 4.5 /
#      DC-02 / FR-24 / AC-24 / RISK-03/RISK-05）。
# 设计意图：
#   1. 单载荷 JSON stdio 协议：父进程一次写入请求（script/params/
#      output_max_bytes；timeout 不下发——超时属父侧执行语义），子进程一次
#      写回响应；stderr 仅诊断截断采集（≤512）不作协议通道；
#   2. bootstrap 内嵌：子进程引导源码为模块内常量（经 python -I -c 启动，
#      不落盘临时文件），白名单清单由 whitelist 常量生成——单一定义双端
#      同源（design B.3 / DC-02，parity 测试锁定）；
#   3. 信任边界：父进程不信子进程自报成功——成功载荷复跑 A1 同一
#      guard_result_value 护栏；失败报告 fail-closed 采信；响应解析失败 →
#      CODE_RUNTIME_ERROR；
#   4. 超时：wait_for(communicate, timeout) 超时分支 proc.kill() →
#      CODE_TIMEOUT + killed=true（win32 无 rlimit，资源上限不可强制，
#      RISK-03；仅承诺超时可强杀，FR-24）；
#   5. 无进程池复用（FR-28）；无网络出网通道、无 rlimit 依赖。
# 边界声明：A2 真实子进程运行在本沙箱不可验证（asyncio socketpair 拦截，
# N 级登记，RISK-05）——本模块 V 级仅覆盖协议纯函数与白名单 parity。
# =============================================================================

import asyncio
import json
import sys
import time
from typing import Dict, Tuple

from orchestration_platform.domain.errors import (
    CODE_OUTPUT_MISSING,
    CODE_OUTPUT_TOO_LARGE,
    CODE_RUNTIME_ERROR,
    CODE_TIMEOUT,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.engine.nodes.code_execution.runner import (
    SCRIPT_ERROR_MESSAGE_LIMIT,
    CodeRunFailure,
    CodeRunGuardError,
    CodeRunOutcome,
    CodeRunSuccess,
    format_script_error,
    guard_result_value,
)
from orchestration_platform.engine.nodes.code_execution.whitelist import (
    WHITELISTED_BUILTIN_NAMES,
)

# 子进程 bootstrap 模板（占位符 __WHITELIST_NAMES__ 构建时替换为白名单常量
# repr；模板内无网络/文件写入通道，与父进程仅 stdin/stdout/stderr 交互）
_BOOTSTRAP_TEMPLATE = '''\
"""代码节点 A2 子进程 bootstrap（父进程经 python -I -c 启动，不落盘）。"""
import builtins
import json
import sys

_NAMES = __WHITELIST_NAMES__

_MESSAGE_LIMIT = 512


def _emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _emit_failure(kind, error_type="", error_message="", actual_bytes=None):
    report = {"ok": False, "kind": kind}
    if error_type:
        report["error_type"] = error_type
    if error_message:
        report["error_message"] = error_message[:_MESSAGE_LIMIT]
    if actual_bytes is not None:
        report["actual_bytes"] = actual_bytes
    _emit(report)


def _main():
    request = json.loads(sys.stdin.read())
    script = request["script"]
    params = request["params"]
    output_max_bytes = request["output_max_bytes"]

    namespace = {
        "__builtins__": {name: getattr(builtins, name) for name in _NAMES},
        "__name__": "__workflow_code__",
        "params": params,
        "result": None,
    }
    try:
        exec(compile(script, "<code_node>", "exec"), namespace)
    except BaseException as exc:
        _emit_failure(
            "script_error",
            error_type=type(exc).__name__,
            error_message=f"{exc}",
        )
        return

    result = namespace["result"]
    if result is None:
        _emit_failure("result_missing")
        return

    def _reject(value):
        raise ValueError(f"不可序列化类型：{type(value).__name__}")

    try:
        serialized = json.dumps(
            result, ensure_ascii=False, allow_nan=False, default=_reject
        )
    except (TypeError, ValueError) as exc:
        _emit_failure("unserializable", error_message=f"{exc}")
        return

    actual_bytes = len(serialized.encode("utf-8"))
    if actual_bytes > output_max_bytes:
        _emit_failure("too_large", actual_bytes=actual_bytes)
        return

    _emit({"ok": True, "result": json.loads(serialized)})


if __name__ == "__main__":
    _main()
'''


def build_bootstrap_source() -> str:
    """生成 A2 子进程 bootstrap 源码（白名单常量内嵌，A1/A2 双端同源）。"""
    return _BOOTSTRAP_TEMPLATE.replace(
        "__WHITELIST_NAMES__", repr(WHITELISTED_BUILTIN_NAMES)
    )


def build_request_payload(
    script: str,
    params: Dict[str, JsonValue],
    output_max_bytes: int,
) -> Dict[str, JsonValue]:
    """构造子进程请求载荷（恰三键；timeout 不下发——超时属父侧执行语义）。"""
    return {
        "script": script,
        "params": params,
        "output_max_bytes": output_max_bytes,
    }


def parse_response_payload(stdout_bytes: bytes) -> Dict[str, JsonValue]:
    """解析子进程 stdout 单载荷 JSON；非 JSON / 非 JSON 对象 → ValueError。"""
    try:
        decoded: object = json.loads(stdout_bytes.decode("utf-8"))
    except ValueError as exc:
        raise ValueError(f"子进程响应不是合法 JSON：{exc}") from None
    if not isinstance(decoded, dict):
        raise ValueError("子进程响应不是 JSON 对象")
    report: Dict[str, JsonValue] = decoded
    return report


def map_timeout_failure(
    timeout_seconds: float,
    elapsed_seconds: float,
) -> CodeRunFailure:
    """A2 超时映射纯函数：CODE_TIMEOUT + killed=true（proc.kill 之后调用）。"""
    return CodeRunFailure(
        error_code=CODE_TIMEOUT,
        error_message=(
            f"代码节点执行超时：已强制终止子进程（killed），"
            f"时限 {timeout_seconds} 秒，实际 {elapsed_seconds:.3f} 秒；"
            f"已记录沙箱违规（不含脚本全文）"
        ),
        duration_seconds=elapsed_seconds,
        killed=True,
    )


def map_failure_report(
    report: Dict[str, JsonValue],
    output_max_bytes: int,
) -> Tuple[str, str]:
    """子进程失败报告 → (错误码, 消息)；fail-closed：未知 kind 归 CODE_RUNTIME_ERROR。"""
    kind = report.get("kind")
    if kind == "result_missing":
        return CODE_OUTPUT_MISSING, "代码节点 result 输出缺失：脚本未对 result 赋值"
    if kind == "unserializable":
        detail = report.get("error_message")
        message = "代码节点输出不可序列化"
        if isinstance(detail, str) and detail:
            message = f"{message}：{detail}"
        return CODE_RUNTIME_ERROR, message
    if kind == "too_large":
        actual = report.get("actual_bytes")
        actual_text = str(actual) if isinstance(actual, int) else "未知"
        return (
            CODE_OUTPUT_TOO_LARGE,
            f"代码节点输出超尺寸上限：上限 {output_max_bytes} 字节，"
            f"实际 {actual_text} 字节",
        )
    if kind == "script_error":
        error_type = str(report.get("error_type") or "")
        error_message = str(report.get("error_message") or "")
        return CODE_RUNTIME_ERROR, format_script_error(error_type, error_message)
    return CODE_RUNTIME_ERROR, "子进程失败报告类型未知（协议失败，fail-closed）"


def _append_diagnostic(message: str, stderr_tail: str) -> str:
    """stderr 诊断尾部附加（仅诊断非协议通道；空则原样返回）。"""
    if not stderr_tail:
        return message
    return f"{message}\n子进程诊断输出（stderr，仅诊断非协议通道）：{stderr_tail}"


async def run_script_subprocess(
    script: str,
    params: Dict[str, JsonValue],
    timeout_seconds: float,
    output_max_bytes: int,
) -> CodeRunOutcome:
    """A2 子进程通道执行入口（async；真实运行 N 级，V 级仅协议纯函数）。"""
    started = time.monotonic()
    payload = build_request_payload(script, params, output_max_bytes)
    try:
        request_bytes = json.dumps(
            payload, ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError):
        return CodeRunFailure(
            error_code=CODE_RUNTIME_ERROR,
            error_message="代码节点 params 载荷不可序列化（A2 通道要求纯 JSON 输入）",
            duration_seconds=time.monotonic() - started,
        )

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        build_bootstrap_source(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(request_bytes),
            timeout_seconds,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return map_timeout_failure(timeout_seconds, time.monotonic() - started)

    elapsed_seconds = time.monotonic() - started
    stderr_tail = stderr.decode("utf-8", "replace")[-SCRIPT_ERROR_MESSAGE_LIMIT:]

    try:
        report = parse_response_payload(stdout)
    except ValueError:
        return CodeRunFailure(
            error_code=CODE_RUNTIME_ERROR,
            error_message=_append_diagnostic(
                "子进程响应不可解析（协议失败）", stderr_tail
            ),
            duration_seconds=elapsed_seconds,
        )

    if report.get("ok") is True:
        # 信任边界：不信子进程自报成功——父进程复跑 A1 同一输出护栏
        try:
            result_value = guard_result_value(
                report.get("result"), output_max_bytes
            )
        except CodeRunGuardError as guard_error:
            return CodeRunFailure(
                error_code=guard_error.code,
                error_message=_append_diagnostic(guard_error.message, stderr_tail),
                duration_seconds=elapsed_seconds,
            )
        return CodeRunSuccess(
            result=result_value,
            duration_seconds=elapsed_seconds,
        )

    error_code, error_message = map_failure_report(report, output_max_bytes)
    return CodeRunFailure(
        error_code=error_code,
        error_message=_append_diagnostic(error_message, stderr_tail),
        duration_seconds=elapsed_seconds,
    )
