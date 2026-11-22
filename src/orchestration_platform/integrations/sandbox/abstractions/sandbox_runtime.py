# =============================================================================
# 沙箱运行时抽象接口（integrations/sandbox/abstractions/sandbox_runtime.py）
#
# 职责：定义沙箱运行时与容器运行时的抽象接口。
# 设计意图（对应偏差修正 design 2.2.2.1）：
#   - ISandboxRuntime：沙箱运行时（创建会话/执行/回收）
#   - IContainerRuntime：容器物理隔离运行时（提交/接收/回收三段协议）
# =============================================================================

from typing import Protocol, runtime_checkable

from orchestration_platform.integrations.sandbox.models import (
    ContainerExecutionRequest,
    ContainerExecutionResult,
    SandboxExecutionResult,
    SandboxInvocation,
    SandboxPolicy,
    SandboxSession,
)


@runtime_checkable
class ISandboxRuntime(Protocol):
    """沙箱运行时抽象接口。

    前置条件：策略已通过 SandboxPolicyManager 校验。
    后置条件：执行结果携带资源用量与违规标记；违规非空时 success=False。
    异常映射：
        SANDBOX_POLICY_INVALID —— 策略格式非法
        SANDBOX_TIMEOUT —— 执行超时
        SANDBOX_LIMIT_EXCEEDED —— 资源超限
        SANDBOX_VIOLATION_FILE —— 路径穿越违规
        SANDBOX_VIOLATION_NETWORK —— 网络违规
        SANDBOX_INTERNAL_ERROR —— 沙箱内部错误
    """

    async def create_session(self, policy: SandboxPolicy) -> SandboxSession:
        """创建沙箱会话。

        加载策略并准备隔离环境，返回会话实例。
        """
        ...

    async def execute(
        self,
        session: SandboxSession,
        invocation: SandboxInvocation,
    ) -> SandboxExecutionResult:
        """在沙箱会话中执行调用。

        三守卫（路径/网络/资源）组合执行，违规时返回违规记录。
        """
        ...

    async def recycle(self, session: SandboxSession) -> None:
        """回收沙箱会话，释放资源。"""
        ...


@runtime_checkable
class IContainerRuntime(Protocol):
    """容器物理隔离运行时抽象接口（接口预留）。

    三段协议：提交→接收→回收。
    前置条件：容器运行时可用（is_available 返回 True）。
    后置条件：提交返回容器引用；接收返回执行结果或超时；回收释放容器。
    异常映射：
        SANDBOX_TIMEOUT —— 容器执行超时
        SANDBOX_INTERNAL_ERROR —— 容器内部错误
    """

    async def submit(
        self,
        request: ContainerExecutionRequest,
    ) -> str:
        """提交容器执行请求，返回容器引用标识。"""
        ...

    async def fetch_result(
        self,
        container_ref: str,
        timeout_seconds: int,
    ) -> ContainerExecutionResult:
        """接收容器执行结果。

        超时抛出 SANDBOX_TIMEOUT。
        """
        ...

    async def reclaim(self, container_ref: str) -> None:
        """回收容器实例。"""
        ...

    def is_available(self) -> bool:
        """探测容器运行时是否可用。"""
        ...
