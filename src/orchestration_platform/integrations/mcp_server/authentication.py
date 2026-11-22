# =============================================================================
# MCP 鉴权（integrations/mcp_server/authentication.py）
#
# 职责：校验 MCP 调用方身份与权限。
# 设计意图（对应 spec 5.3.1.3、任务 T32.3）：
#   1. 复用既有 API 鉴权逻辑；
#   2. 未授权调用返回 MCP 鉴权失败错误。
# =============================================================================

from typing import List

from orchestration_platform.domain.errors import MCP_TOOL_PERMISSION_DENIED, DomainError
from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.mcp_tool_market.models import CallerIdentity


class McpAuthenticator:
    """MCP 调用鉴权器。"""

    def __init__(self, valid_tokens: List[str] | None = None) -> None:
        """初始化鉴权器。

        Args:
            valid_tokens: 合法的访问令牌列表（None 表示不校验令牌）。
        """
        self._valid_tokens = set(valid_tokens) if valid_tokens else None

    def authenticate(self, token: str) -> CallerIdentity:
        """校验访问令牌并返回调用方身份。

        Args:
            token: 访问令牌。

        Returns:
            调用方身份。

        Raises:
            DomainError: 令牌无效时抛出 MCP_TOOL_PERMISSION_DENIED。
        """
        if self._valid_tokens is not None and token not in self._valid_tokens:
            raise DomainError(MCP_TOOL_PERMISSION_DENIED, "MCP 访问令牌无效")
        return CallerIdentity(
            caller_id=token[:8] if token else "anonymous",
            caller_type="external",
            permissions=[ToolPermission.READONLY, ToolPermission.READWRITE],
        )

    def check_permission(
        self,
        identity: CallerIdentity,
        required_permission: ToolPermission,
    ) -> None:
        """校验调用方是否具备所需权限。

        Args:
            identity: 调用方身份。
            required_permission: 工具所需权限。

        Raises:
            DomainError: 权限不足时抛出 MCP_TOOL_PERMISSION_DENIED。
        """
        if required_permission not in identity.permissions:
            raise DomainError(
                MCP_TOOL_PERMISSION_DENIED,
                f"权限不足: 需要 {required_permission.value}",
            )
