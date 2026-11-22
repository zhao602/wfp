# =============================================================================
# 逻辑隔离运行时（integrations/sandbox/logical_isolation_runtime.py）
#
# 职责：实现进程内沙箱隔离执行——三守卫（路径/网络/资源）组合执行。
# 设计意图（对应偏差修正 spec 5.1.1 / design 2.2.2.1）：
#   - create_session 加载策略准备隔离环境
#   - execute 三守卫组合执行，违规出口统一断言
#   - asyncio.wait_for 包裹实现超时终止
#   - 沙箱运行时自身异常返回 SANDBOX_INTERNAL_ERROR 并强制回收
#   - 未启用/无策略时直通既有调用器（零开销）
# =============================================================================

import asyncio
import logging
import time
import uuid
from typing import Any, Callable, Dict, Optional

from orchestration_platform.domain.errors import (
    SANDBOX_INTERNAL_ERROR,
    SANDBOX_TIMEOUT,
)
from orchestration_platform.integrations.sandbox.abstractions.sandbox_runtime import (
    ISandboxRuntime,
)
from orchestration_platform.integrations.sandbox.models import (
    SandboxExecutionResult,
    SandboxInvocation,
    SandboxPolicy,
    SandboxSession,
    SandboxViolationRecord,
)
from orchestration_platform.integrations.sandbox.network_filter import NetworkFilter
from orchestration_platform.integrations.sandbox.path_guard import PathGuard
from orchestration_platform.integrations.sandbox.resource_meter import ResourceMeter

_logger = logging.getLogger(__name__)

# 工具执行函数类型：接收 (tool_name, arguments) 返回 (output, output_bytes)
ToolExecutor = Callable[[str, Dict[str, Any]], Any]


class LogicalIsolationRuntime:
    """逻辑隔离运行时（ISandboxRuntime 默认实现）。

    进程内执行，三守卫（路径/网络/资源）组合隔离。
    构造注入工具执行函数，execute 在沙箱内调用执行函数。
    """

    def __init__(
        self,
        tool_executor: Optional[ToolExecutor] = None,
        path_guard: Optional[PathGuard] = None,
        network_filter: Optional[NetworkFilter] = None,
        resource_meter: Optional[ResourceMeter] = None,
    ) -> None:
        """初始化逻辑隔离运行时。

        参数：
            tool_executor：工具执行函数（tool_name, arguments → output）
            path_guard：路径守卫（默认创建）
            network_filter：网络过滤器（默认创建）
            resource_meter：资源计量器（默认创建）
        """
        self._tool_executor = tool_executor
        self._path_guard = path_guard or PathGuard()
        self._network_filter = network_filter or NetworkFilter()
        self._resource_meter = resource_meter or ResourceMeter()
        self._sessions: Dict[str, tuple] = {}

    async def create_session(self, policy: SandboxPolicy) -> SandboxSession:
        """创建沙箱会话。

        加载策略并准备隔离环境（创建工作目录）。
        """
        import os
        os.makedirs(policy.workdir, exist_ok=True)

        session = SandboxSession(
            session_id=f"sess_{uuid.uuid4().hex[:12]}",
            policy_id=policy.policy_id,
            tenant_id=policy.tenant_id,
        )
        self._sessions[session.session_id] = (policy, session)
        _logger.info("沙箱会话已创建: session=%s, policy=%s", session.session_id, policy.policy_id)
        return session

    async def execute(
        self,
        session: SandboxSession,
        invocation: SandboxInvocation,
    ) -> SandboxExecutionResult:
        """在沙箱会话中执行调用。

        流程：路径守卫→网络过滤器→资源计量→工具执行→违规出口统一断言。
        """
        start_time = time.monotonic()
        violations = []

        session_data = self._sessions.get(session.session_id)
        if session_data is None:
            return SandboxExecutionResult(
                success=False,
                error_code=SANDBOX_INTERNAL_ERROR,
                error_message=f"会话不存在: {session.session_id}",
            )

        policy, _ = session_data
        timeout = invocation.timeout_seconds or policy.execution_timeout_seconds

        try:
            # 1. 路径守卫
            path_violations = self._path_guard.check(invocation.arguments, policy)
            violations.extend(path_violations)

            # 2. 网络过滤器（检查 arguments 中的网络目标）
            for key, value in invocation.arguments.items():
                if key in ("url", "endpoint", "host", "target_url") and isinstance(value, str):
                    net_violation = self._network_filter.check(value, policy)
                    if net_violation is not None:
                        violations.append(net_violation)

            # 3. 如果有违规，直接返回（不执行工具）
            if violations:
                return self._build_violation_result(violations, start_time)

            # 4. 执行工具（带超时）
            if self._tool_executor is None:
                return SandboxExecutionResult(
                    success=True,
                    output=None,
                    resource_usage=self._resource_meter.measure_usage(start_time, 0),
                    execution_duration_seconds=round(time.monotonic() - start_time, 4),
                )

            try:
                raw_output = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        self._tool_executor,
                        invocation.tool_name,
                        invocation.arguments,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                violations.append(SandboxViolationRecord(
                    violation_type="timeout",
                    detail=f"执行超时: {timeout}s",
                ))
                return self._build_violation_result(violations, start_time, timed_out=True)

            # 5. 资源计量
            output_bytes = self._measure_output_bytes(raw_output)
            usage = self._resource_meter.measure_usage(start_time, output_bytes)

            resource_violation = self._resource_meter.check_all(usage, policy)
            if resource_violation is not None:
                violations.append(resource_violation)
                return self._build_violation_result(violations, start_time, output=raw_output)

            # 6. 成功结果
            duration = round(time.monotonic() - start_time, 4)
            return SandboxExecutionResult(
                success=True,
                output=raw_output,
                resource_usage=usage,
                execution_duration_seconds=duration,
            )

        except Exception as exc:
            _logger.error("沙箱内部错误: %s", exc, exc_info=True)
            return SandboxExecutionResult(
                success=False,
                error_code=SANDBOX_INTERNAL_ERROR,
                error_message=f"沙箱内部错误: {type(exc).__name__}: {exc}",
                violations=violations,
                execution_duration_seconds=round(time.monotonic() - start_time, 4),
            )

    async def recycle(self, session: SandboxSession) -> None:
        """回收沙箱会话，释放资源。"""
        self._sessions.pop(session.session_id, None)
        _logger.info("沙箱会话已回收: session=%s", session.session_id)

    def _build_violation_result(
        self,
        violations: list,
        start_time: float,
        timed_out: bool = False,
        output: Any = None,
    ) -> SandboxExecutionResult:
        """构造违规结果（违规出口统一断言：success=False）。"""
        error_code = SANDBOX_TIMEOUT if timed_out else None
        if not timed_out and violations:
            from orchestration_platform.domain.errors import (
                SANDBOX_VIOLATION_FILE,
                SANDBOX_VIOLATION_NETWORK,
                SANDBOX_LIMIT_EXCEEDED,
            )
            first = violations[0]
            if first.violation_type.value == "file_escape":
                error_code = SANDBOX_VIOLATION_FILE
            elif first.violation_type.value == "network_denied":
                error_code = SANDBOX_VIOLATION_NETWORK
            elif first.violation_type.value == "resource_exceeded":
                error_code = SANDBOX_LIMIT_EXCEEDED

        return SandboxExecutionResult(
            success=False,
            output=output,
            violations=violations,
            timed_out=timed_out,
            error_code=error_code,
            error_message=violations[0].detail if violations else None,
            execution_duration_seconds=round(time.monotonic() - start_time, 4),
        )

    @staticmethod
    def _measure_output_bytes(output: Any) -> int:
        """测量输出字节数。"""
        if output is None:
            return 0
        if isinstance(output, bytes):
            return len(output)
        if isinstance(output, str):
            return len(output.encode("utf-8"))
        try:
            import json
            return len(json.dumps(output, ensure_ascii=False).encode("utf-8"))
        except Exception:
            return len(str(output).encode("utf-8"))
