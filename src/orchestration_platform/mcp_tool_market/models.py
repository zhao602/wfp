# =============================================================================
# MCP 工具市场模型（mcp_tool_market/models.py）
#
# 职责：定义 MCP 工具注册、描述与调用方身份的领域模型。
# 设计意图（对应 spec 5.3、design 2.6）：
#   1. 工具注册含元数据声明、版本管理与权限标注；
#   2. 注册结果携带成功/冲突状态；
#   3. 调用方身份用于权限校验。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import McpTransportType, ToolPermission


def _utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class McpToolDescriptor(BaseModel):
    """MCP 工具描述符：声明工具的元数据。"""

    tool_identifier: str = Field(description="工具标识（全局唯一）")
    name: str = Field(description="工具显示名称")
    description: str = Field(default="", description="工具功能描述")
    version: str = Field(default="1.0.0", description="工具版本号")
    permission: ToolPermission = Field(
        default=ToolPermission.READONLY, description="权限级别"
    )
    input_schema: Dict[str, Any] = Field(
        default_factory=dict, description="输入参数 Schema（JSON Schema 格式）"
    )
    output_schema: Dict[str, Any] = Field(
        default_factory=dict, description="输出参数 Schema"
    )
    transport: McpTransportType = Field(
        default=McpTransportType.HTTP, description="传输协议"
    )
    endpoint: str = Field(default="", description="工具端点地址")
    tags: List[str] = Field(default_factory=list, description="标签（分类检索用）")


class CallerIdentity(BaseModel):
    """调用方身份：用于权限校验。"""

    caller_id: str = Field(description="调用方标识")
    caller_type: str = Field(default="agent", description="调用方类型：agent / external / platform")
    permissions: List[ToolPermission] = Field(
        default_factory=list, description="调用方持有的权限列表"
    )


class McpToolRegistration(BaseModel):
    """MCP 工具注册记录。"""

    descriptor: McpToolDescriptor = Field(description="工具描述符")
    registered_by: str = Field(description="注册者标识")
    registered_at: datetime = Field(default_factory=_utc_now, description="注册时间")
    is_active: bool = Field(default=True, description="是否启用")
    previous_version: Optional[str] = Field(default=None, description="前一版本号（升级时记录）")


class RegistrationResult(BaseModel):
    """工具注册结果。"""

    success: bool = Field(description="是否成功")
    tool_identifier: str = Field(description="工具标识")
    version: str = Field(description="注册版本")
    conflict_reason: Optional[str] = Field(default=None, description="冲突原因（失败时提供）")
