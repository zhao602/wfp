# =============================================================================
# A1 受限进程内执行核心（engine/nodes/code_execution/runner.py）
#
# 职责：代码节点脚本受限进程内执行的同步核心（llm_code_nodes_v2 任务
#      4.2~4.4 / DC-01 / FR-14~17/22/31 / AC-16~20/22/27/30/31/33）。
# 设计意图：
#   1. 同步函数锚点：_run_code_node_script 为本沙箱唯一可同步验证的执行
#      通道（T7/T8 直调，不经 asyncio，G-05）；
#   2. 单次编译单次执行：每次执行独立 globals，禁脚本缓存跨实例复用
#      （FR-28）；
#   3. 一次性 daemon 工作线程 + join(timeout)：超时放弃、不向线程注入终止
#      （Python 无安全线程终止机制；RISK-02 已知边界——CPU 死循环线程
#      不可强杀，daemon 保证进程退出不被阻塞，abandoned_thread 可观测；
#      实际表现 N 级登记不测）；
#   4. 输出护栏 A1/A2 共用：result is None → CODE_OUTPUT_MISSING（显式置
#      None 与未定义统一，D-07）；序列化失败 → CODE_RUNTIME_ERROR「输出
#      不可序列化」；字节超限 → CODE_OUTPUT_TOO_LARGE（附上界与实际字节
#      数，禁截断部分成功）；成功 JSON 往返为纯 JSON 形态（FR-16/17）；
#   5. 消息卫生：运行期异常消息「类型名: 文本」截断 ≤512，不含脚本全文
#      （AC-30）。
# 边界声明（INV-06/07）：「受限进程内执行，非进程级隔离」；白名单与静态
# 校验不构成运行期安全性证明。
# =============================================================================

import json
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, NoReturn, Union

from orchestration_platform.domain.errors import (
    CODE_OUTPUT_MISSING,
    CODE_OUTPUT_TOO_LARGE,
    CODE_RUNTIME_ERROR,
    CODE_TIMEOUT,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.engine.nodes.code_execution.whitelist import (
    WHITELISTED_BUILTINS,
)

# 运行期错误消息截断上限（与 A2 stderr 诊断采集口径一致）
SCRIPT_ERROR_MESSAGE_LIMIT = 512

# 受限执行命名空间固定模块名（防脚本伪装饰为真实模块）
EXECUTION_NAMESPACE_NAME = "__workflow_code__"


@dataclass
class CodeRunSuccess:
    """受限执行成功：result（JSON 往返后的纯 JSON 值）与执行时长（秒）。"""

    result: JsonValue
    duration_seconds: float


@dataclass
class CodeRunFailure:
    """受限执行失败：错误码 + 消息 + 执行时长 + 可观测标志（RISK-02/RISK-05）。"""

    error_code: str
    error_message: str
    duration_seconds: float
    abandoned_thread: bool = False  # A1：超时放弃的工作线程（daemon，进程退出回收）
    killed: bool = False            # A2：超时强杀的子进程


CodeRunOutcome = Union[CodeRunSuccess, CodeRunFailure]


class CodeRunGuardError(Exception):
    """输出护栏拒绝（A1/A2 共用）：(code, message) 形态，由调用方映射为失败。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_execution_namespace(params: Dict[str, JsonValue]) -> Dict[str, object]:
    """构造单次执行的独立 globals（任务 4.2）。

    __builtins__ 取白名单新拷贝（防脚本篡改后跨执行残留）；params 契约级
    只读（普通 dict + 文档约束，D-08）；result 预置 None（is None 统一判
    缺失，D-07）。
    """
    return {
        "__builtins__": dict(WHITELISTED_BUILTINS),
        "__name__": EXECUTION_NAMESPACE_NAME,
        "params": params,
        "result": None,
    }


def format_script_error(error_type_name: str, message: str) -> str:
    """运行期异常消息形态：「类型名: 文本」，截断 ≤512 字符（脱敏防日志膨胀）。"""
    text = f"{error_type_name}: {message}"
    if len(text) > SCRIPT_ERROR_MESSAGE_LIMIT:
        text = text[: SCRIPT_ERROR_MESSAGE_LIMIT - 1] + "…"
    return text


def guard_result_value(result_value: object, output_max_bytes: int) -> JsonValue:
    """result 输出护栏（任务 4.4，A1/A2 共用信任边界）。

    - result_value is None → CODE_OUTPUT_MISSING（显式置 None 与未定义统一，D-07）；
    - json.dumps(ensure_ascii=False, allow_nan=False, default=拒绝回调) 失败
      → CODE_RUNTIME_ERROR「输出不可序列化」（自定义对象/函数/NaN 等）；
    - 序列化字节长（UTF-8）> output_max_bytes → CODE_OUTPUT_TOO_LARGE
      （附上界与实际字节数；禁止截断后部分成功）；
    - 成功 → json.loads 往返后的纯 JSON 形态（JsonValue 契约，FR-16/17）。

    拒绝时抛 CodeRunGuardError，由调用方映射为 CodeRunFailure。
    """
    if result_value is None:
        raise CodeRunGuardError(
            CODE_OUTPUT_MISSING,
            "代码节点 result 输出缺失：脚本未对 result 赋值",
        )

    def _reject(value: object) -> NoReturn:
        raise ValueError(f"不可序列化类型：{type(value).__name__}")

    try:
        serialized = json.dumps(
            result_value,
            ensure_ascii=False,
            allow_nan=False,
            default=_reject,
        )
    except (TypeError, ValueError) as exc:
        raise CodeRunGuardError(
            CODE_RUNTIME_ERROR,
            format_script_error("代码节点输出不可序列化", str(exc)),
        ) from None

    actual_bytes = len(serialized.encode("utf-8"))
    if actual_bytes > output_max_bytes:
        raise CodeRunGuardError(
            CODE_OUTPUT_TOO_LARGE,
            f"代码节点输出超尺寸上限：上限 {output_max_bytes} 字节，"
            f"实际 {actual_bytes} 字节",
        )

    return json.loads(serialized)


def _run_code_node_script(
    script: str,
    params: Dict[str, JsonValue],
    timeout_seconds: float,
    output_max_bytes: int,
) -> CodeRunOutcome:
    """A1 受限进程内执行核心（任务 4.2~4.4，T7/T8 同步直调锚点）。

    一次性 daemon 工作线程内单次编译单次执行；join(timeout) 超时返回
    CODE_TIMEOUT（abandoned_thread=true，不注入线程终止，RISK-02）；正常
    结束后经 guard_result_value 输出护栏。语法错误经静态校验前置拦截
    （CODE_SYNTAX_ERROR）；直调路径兜底归 CODE_RUNTIME_ERROR（fail-closed）。
    """
    started = time.monotonic()
    namespace = build_execution_namespace(params)

    captured: List[BaseException] = []

    def _worker() -> None:
        try:
            compiled = compile(script, "<code_node>", "exec")
            exec(compiled, namespace)  # 受限白名单命名空间内执行（DC-01）
        except BaseException as exc:  # 工作线程全捕获：脚本级异常交主流程映射
            captured.append(exc)

    worker = threading.Thread(
        target=_worker,
        name="code-node-worker",
        daemon=True,
    )
    worker.start()
    worker.join(timeout_seconds)
    duration_seconds = time.monotonic() - started

    if worker.is_alive():
        return CodeRunFailure(
            error_code=CODE_TIMEOUT,
            error_message=(
                f"代码节点执行超时：已放弃工作线程（abandoned_thread），"
                f"时限 {timeout_seconds} 秒，实际 {duration_seconds:.3f} 秒"
            ),
            duration_seconds=duration_seconds,
            abandoned_thread=True,
        )

    if captured:
        exc = captured[0]
        return CodeRunFailure(
            error_code=CODE_RUNTIME_ERROR,
            error_message=format_script_error(type(exc).__name__, str(exc)),
            duration_seconds=duration_seconds,
        )

    try:
        result_value = guard_result_value(namespace["result"], output_max_bytes)
    except CodeRunGuardError as guard_error:
        return CodeRunFailure(
            error_code=guard_error.code,
            error_message=guard_error.message,
            duration_seconds=duration_seconds,
        )

    return CodeRunSuccess(
        result=result_value,
        duration_seconds=duration_seconds,
    )
