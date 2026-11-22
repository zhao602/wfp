# =============================================================================
# 容器物理隔离运行时（integrations/sandbox/container_isolation_runtime.py）
#
# 职责：容器级物理隔离执行，接口预留与自动降级。
# 设计意图（对应偏差修正 spec 5.1.1.6 / design 2.2.2.1）：
#   - 实现 ISandboxRuntime，内部依赖 IContainerRuntime 契约
#   - 容器驱动延迟导入；缺失/未配置/不可达时降级 LogicalIsolationRuntime
#   - 容器执行超时终止并回退逻辑隔离
#   - select_sandbox_runtime 装配函数
#   - 本阶段不实现真实 Docker 驱动（仅契约+Fake 测试）
# =============================================================================

import asyncio
import logging
import uuid
from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import SANDBOX_DEGRADED, SANDBOX_TIMEOUT
from orchestration_platform.integrations.sandbox.abstractions.sandbox_runtime import (
    IContainerRuntime,
    ISandboxRuntime,
)
from orchestration_platform.integrations.sandbox.logical_isolation_runtime import (
    LogicalIsolationRuntime,
    ToolExecutor,
)
from orchestration_platform.integrations.sandbox.models import (
    ContainerExecutionRequest,
    SandboxExecutionResult,
    SandboxInvocation,
    SandboxPolicy,
    SandboxSession,
)

_logger = logging.getLogger(__name__)


class ContainerIsolationRuntime:
    """容器物理隔离运行时（ISandboxRuntime 实现）。

    内部依赖 IContainerRuntime 契约（提交/接收/回收三段协议）。
    容器驱动缺失/未配置/不可达时自动降级 LogicalIsolationRuntime。
    """

    def __init__(
        self,
        container_runtime: Optional[IContainerRuntime] = None,
        tool_executor: Optional[ToolExecutor] = None,
        fallback: Optional[LogicalIsolationRuntime] = None,
    ) -> None:
        """初始化容器隔离运行时。

        参数：
            container_runtime：容器运行时（None 表示未配置）
            tool_executor：工具执行函数（降级时使用）
            fallback：降级逻辑隔离运行时（None 则自动创建）
        """
        self._container_runtime = container_runtime
        self._fallback = fallback or LogicalIsolationRuntime(tool_executor=tool_executor)
        self._is_degraded = container_runtime is None or not container_runtime.is_available()

        if self._is_degraded:
            _logger.warning(
                "容器物理隔离降级（SANDBOX_DEGRADED）: "
                "容器运行时未配置或不可用，回退逻辑隔离"
            )

    @property
    def is_degraded(self) -> bool:
        """是否降级模式。"""
        return self._is_degraded

    async def create_session(self, policy: SandboxPolicy) -> SandboxSession:
        """创建沙箱会话。

        降级时委托逻辑隔离运行时。
        """
        if self._is_degraded:
            return await self._fallback.create_session(policy)

        return SandboxSession(
            session_id=f"ctr_{uuid.uuid4().hex[:12]}",
            policy_id=policy.policy_id,
            tenant_id=policy.tenant_id,
            metadata={"isolation": "physical"},
        )

    async def execute(
        self,
        session: SandboxSession,
        invocation: SandboxInvocation,
    ) -> SandboxExecutionResult:
        """在容器中执行调用。

        降级时委托逻辑隔离运行时。
        """
        if self._is_degraded:
            result = await self._fallback.execute(session, invocation)
            result.degraded = True
            result.degraded_reason = "容器运行时不可用，降级逻辑隔离"
            return result

        # 容器执行
        try:
            assert self._container_runtime is not None

            # 构造容器执行请求
            request = ContainerExecutionRequest(
                request_id=f"req_{uuid.uuid4().hex[:12]}",
                session_id=session.session_id,
                tool_name=invocation.tool_name,
                arguments=invocation.arguments,
                workdir=session.metadata.get("workdir", ""),
                cpu_seconds_limit=10.0,
                memory_mb_limit=512,
                output_bytes_limit=1048576,
                timeout_seconds=60,
            )

            # 提交→接收→回收三段协议
            container_ref = await self._container_runtime.submit(request)
            settings = get_settings()
            result = await self._container_runtime.fetch_result(
                container_ref,
                settings.sandbox_container_timeout_seconds,
            )

            return SandboxExecutionResult(
                success=result.success,
                output=result.output,
                resource_usage=result.resource_usage,
                error_code=result.error_code,
                error_message=result.error_message,
            )

        except asyncio.TimeoutError:
            _logger.warning("容器执行超时，回退逻辑隔离")
            return await self._fallback.execute(session, invocation)
        except Exception as exc:
            _logger.error("容器执行异常，回退逻辑隔离: %s", exc)
            result = await self._fallback.execute(session, invocation)
            result.degraded = True
            result.degraded_reason = f"容器执行异常: {exc}"
            return result
        finally:
            try:
                if not self._is_degraded and self._container_runtime is not None:
                    await self._container_runtime.reclaim(container_ref)
            except Exception:
                pass

    async def recycle(self, session: SandboxSession) -> None:
        """回收沙箱会话。"""
        if self._is_degraded:
            await self._fallback.recycle(session)


def select_sandbox_runtime(
    tool_executor: Optional[ToolExecutor] = None,
    container_runtime: Optional[IContainerRuntime] = None,
) -> ISandboxRuntime:
    """装配沙箱运行时。

    根据配置选择容器物理隔离或逻辑隔离。
    物理隔离配置且容器可用→ContainerIsolationRuntime
    否则→LogicalIsolationRuntime

    参数：
        tool_executor：工具执行函数
        container_runtime：容器运行时（None 则按配置判断）

    返回：
        ISandboxRuntime 实例
    """
    settings = get_settings()

    if (
        settings.sandbox_enabled
        and settings.sandbox_default_isolation == "physical"
        and settings.sandbox_container_endpoint
    ):
        # 尝试容器物理隔离
        if container_runtime is not None and container_runtime.is_available():
            return ContainerIsolationRuntime(
                container_runtime=container_runtime,
                tool_executor=tool_executor,
            )
        # 容器不可用，降级逻辑隔离
        _logger.warning(
            "容器物理隔离不可用（SANDBOX_DEGRADED）: "
            "端点=%s, 回退逻辑隔离",
            settings.sandbox_container_endpoint,
        )

    # 默认逻辑隔离
    return LogicalIsolationRuntime(tool_executor=tool_executor)
