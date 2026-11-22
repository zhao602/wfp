# =============================================================================
# 沙箱化工具调用器（integrations/tools/sandboxed_tool_invoker.py）
#
# 职责：装饰既有 LocalFunctionToolInvoker，透明加沙箱隔离。
# 设计意图（对应偏差修正 spec 5.1.2 / design 2.2.2.8）：
#   - 实现同一 invoke 调用面签名，透明加隔离
#   - sandbox_enabled=False 或无策略标识时直通既有调用器
#   - 沙箱执行结果映射回 ToolResult（metadata 承载资源用量与违规标记）
#   - LocalFunctionToolInvoker 文件本身零修改
# =============================================================================

import logging
import uuid
from typing import Any, Dict, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.integrations.abstractions.external_clients import ToolResult
from orchestration_platform.integrations.sandbox.abstractions.sandbox_runtime import (
    ISandboxRuntime,
)
from orchestration_platform.integrations.sandbox.models import (
    SandboxInvocation,
    SandboxPolicy,
)
from orchestration_platform.integrations.sandbox.policy_manager import (
    SandboxPolicyManager,
)
from orchestration_platform.integrations.tools.local_function_tool_invoker import (
    LocalFunctionToolInvoker,
)

_logger = logging.getLogger(__name__)


class SandboxedToolInvoker:
    """沙箱化工具调用器。

    装饰既有 LocalFunctionToolInvoker，透明加沙箱隔离。
    sandbox_enabled=False 或无策略标识时直通既有调用器。
    """

    def __init__(
        self,
        inner_invoker: LocalFunctionToolInvoker,
        sandbox_runtime: ISandboxRuntime,
        policy_manager: SandboxPolicyManager,
    ) -> None:
        """初始化沙箱化工具调用器。

        参数：
            inner_invoker：被装饰的既有工具调用器
            sandbox_runtime：沙箱运行时
            policy_manager：策略管理器
        """
        self._inner = inner_invoker
        self._runtime = sandbox_runtime
        self._policy_manager = policy_manager
        self._settings = get_settings()

    @property
    def tool_identifier(self) -> str:
        """工具标识（与被装饰调用器一致）。"""
        return self._inner.tool_identifier

    async def invoke(
        self,
        arguments: Dict[str, Any],
        timeout_seconds: int,
        sandbox_policy_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> ToolResult:
        """调用工具（沙箱隔离）。

        参数：
            arguments：工具入参
            timeout_seconds：超时秒数
            sandbox_policy_id：沙箱策略标识（None 则直通）
            tenant_id：租户标识

        返回：
            工具调用结果
        """
        # 未启用沙箱或无策略标识 → 直通既有调用器
        if not self._settings.sandbox_enabled or sandbox_policy_id is None:
            return await self._inner.invoke(arguments, timeout_seconds)

        # 加载策略
        policy, error_msg = await self._policy_manager.load_policy(
            sandbox_policy_id, tenant_id or "default"
        )
        if policy is None:
            return ToolResult(
                success=False,
                error_code="SANDBOX_POLICY_INVALID",
                error_message=error_msg or "策略加载失败",
            )

        # 创建沙箱会话
        session = await self._runtime.create_session(policy)

        try:
            # 构造沙箱调用
            invocation = SandboxInvocation(
                invocation_id=f"inv_{uuid.uuid4().hex[:12]}",
                session_id=session.session_id,
                tool_name=self._inner.tool_identifier,
                arguments=arguments,
                timeout_seconds=timeout_seconds,
            )

            # 执行
            result = await self._runtime.execute(session, invocation)

            # 映射回 ToolResult
            return ToolResult(
                success=result.success,
                output=result.output,
                error_code=result.error_code,
                error_message=result.error_message,
            )

        finally:
            await self._runtime.recycle(session)
