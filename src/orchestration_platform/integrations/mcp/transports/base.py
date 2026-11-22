# =============================================================================
# MCP 传输抽象接口（integrations/mcp/transports/base.py）
#
# 职责：定义 MCP 传输层统一接口。
# 设计意图（对应 spec 5.3.1.5、任务 T33.2）：
#   stdio / http / sse 三种传输共享此接口。
# =============================================================================

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class IMcpTransport(Protocol):
    """MCP 传输层抽象接口。"""

    async def connect(self) -> None:
        """建立连接。"""
        ...

    async def disconnect(self) -> None:
        """断开连接。"""
        ...

    async def send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """发送 JSON-RPC 请求并等待响应。"""
        ...

    @property
    def is_connected(self) -> bool:
        """是否已连接。"""
        ...
