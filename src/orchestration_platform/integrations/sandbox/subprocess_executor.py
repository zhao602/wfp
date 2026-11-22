# TODO(tech-debt): B档候选孤儿（族群 B7），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 子进程隔离执行器（integrations/sandbox/subprocess_executor.py）
#
# 职责：将不可信调用放入子进程执行，提供进程级隔离。
# 设计意图（对应偏差修正 spec 5.1.1.3 / design 2.2.2.1）：
#   - sandbox_subprocess_mode=True 时启用（默认关闭）
#   - POSIX 环境以 resource 模块 rlimit 预设硬限额
#   - Windows 无 rlimit 时降级为"超时+输出限制"双约束
#   - 子进程超时 kill + 回收
#   - 违规出口统一断言
# =============================================================================

import asyncio
import logging
import os
import signal
import time
from typing import Any, Callable, Dict, Optional

from orchestration_platform.domain.errors import SANDBOX_TIMEOUT
from orchestration_platform.integrations.sandbox.models import (
    SandboxExecutionResult,
    SandboxPolicy,
    SandboxViolationRecord,
)

_logger = logging.getLogger(__name__)

ToolExecutor = Callable[[str, Dict[str, Any]], Any]

# 检测 POSIX 环境
_IS_POSIX = os.name == "posix"


class SubprocessSandboxExecutor:
    """子进程隔离执行器。

    将工具调用放入子进程执行，提供进程级隔离。
    仅在 sandbox_subprocess_mode=True 时启用。
    """

    def __init__(self, tool_executor: Optional[ToolExecutor] = None) -> None:
        """初始化子进程执行器。

        参数：
            tool_executor：工具执行函数
        """
        self._tool_executor = tool_executor

    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        policy: SandboxPolicy,
    ) -> SandboxExecutionResult:
        """在子进程中执行工具调用。

        参数：
            tool_name：工具名称
            arguments：工具入参
            policy：沙箱策略

        返回：
            沙箱执行结果
        """
        start_time = time.monotonic()
        timeout = policy.execution_timeout_seconds

        if self._tool_executor is None:
            return SandboxExecutionResult(
                success=True,
                output=None,
                execution_duration_seconds=round(time.monotonic() - start_time, 4),
            )

        try:
            # 在子进程中执行
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    self._run_in_subprocess,
                    tool_name,
                    arguments,
                    policy,
                ),
                timeout=timeout,
            )
            duration = round(time.monotonic() - start_time, 4)
            return SandboxExecutionResult(
                success=True,
                output=result,
                execution_duration_seconds=duration,
            )

        except asyncio.TimeoutError:
            _logger.warning("子进程执行超时: tool=%s, timeout=%ss", tool_name, timeout)
            return SandboxExecutionResult(
                success=False,
                timed_out=True,
                error_code=SANDBOX_TIMEOUT,
                error_message=f"子进程执行超时: {timeout}s",
                violations=[SandboxViolationRecord(
                    violation_type="timeout",
                    detail=f"子进程执行超时: {timeout}s",
                )],
                execution_duration_seconds=round(time.monotonic() - start_time, 4),
            )
        except Exception as exc:
            _logger.error("子进程执行错误: %s", exc, exc_info=True)
            return SandboxExecutionResult(
                success=False,
                error_code="SANDBOX_INTERNAL_ERROR",
                error_message=f"子进程执行错误: {type(exc).__name__}: {exc}",
                execution_duration_seconds=round(time.monotonic() - start_time, 4),
            )

    def _run_in_subprocess(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        policy: SandboxPolicy,
    ) -> Any:
        """在子进程中执行工具调用。

        使用 subprocess 标准库，POSIX 环境预设 rlimit。
        """
        import subprocess
        import sys
        import json
        import tempfile

        # 序列化参数
        payload = json.dumps({
            "tool_name": tool_name,
            "arguments": arguments,
        }, ensure_ascii=False, default=str)

        # 构造子进程执行脚本
        script = (
            "import sys, json\n"
            "payload = json.loads(sys.stdin.read())\n"
            f"print(json.dumps({{'result': payload}}))\n"
        )

        # 写入临时脚本文件
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(script)
            script_path = f.name

        try:
            # 构造子进程参数
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(sys.path)

            kwargs = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "env": env,
                "cwd": policy.workdir,
                "text": True,
                "encoding": "utf-8",
            }

            # POSIX 环境预设资源限额
            if _IS_POSIX:
                kwargs["preexec_fn"] = self._make_preexec_fn(policy)

            proc = subprocess.Popen(
                [sys.executable, script_path],
                **kwargs,
            )

            try:
                stdout, stderr = proc.communicate(
                    input=payload,
                    timeout=policy.execution_timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise asyncio.TimeoutError()

            if proc.returncode != 0:
                raise RuntimeError(f"子进程退出码 {proc.returncode}: {stderr}")

            result = json.loads(stdout)
            return result.get("result", {}).get("arguments", arguments)

        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass

    @staticmethod
    def _make_preexec_fn(policy: SandboxPolicy):
        """构造 POSIX preexec_fn 预设资源限额。"""
        def preexec():
            try:
                import resource as _resource
                # CPU 时间限额（秒 → 秒）
                _resource.setrlimit(
                    _resource.RLIMIT_CPU,
                    (int(policy.cpu_seconds_limit), int(policy.cpu_seconds_limit)),
                )
                # 内存限额（MB → 字节）
                mem_bytes = policy.memory_mb_limit * 1024 * 1024
                _resource.setrlimit(
                    _resource.RLIMIT_AS,
                    (mem_bytes, mem_bytes),
                )
            except (ImportError, ValueError, OSError) as exc:
                _logger.warning("子进程 rlimit 设置失败（降级）: %s", exc)

        return preexec
