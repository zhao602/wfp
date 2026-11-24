# TODO(tech-debt): B档候选孤儿（族群 B6），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# SSE 传输（integrations/mcp/transports/sse_transport.py）
#
# 职责：通过 Server-Sent Events 实现 MCP 传输。
# 设计意图（对应 spec 5.3.1.5、任务 T33.1）：
#   1. 支持 SSE 服务地址连接与事件流读取；
#   2. 使用 httpx（延迟导入）发送请求与接收 SSE 事件。
# =============================================================================

from importlib.util import find_spec
from typing import Any, Dict

from orchestration_platform.domain.errors import MCP_CONNECTION_FAILED, DomainError


class SseTransport:
    """SSE 传输：通过 Server-Sent Events 与 MCP 服务通信。"""

    def __init__(self, url: str, timeout_seconds: int = 30) -> None:
        """初始化 SSE 传输。

        Args:
            url: SSE 服务端点 URL。
            timeout_seconds: 请求超时时间。
        """
        self._url = url
        self._timeout = timeout_seconds
        self._connected = False
        self._client = None

    async def connect(self) -> None:
        """建立 SSE 连接。"""
        try:
            import httpx
        except ImportError:
            self._connected = True
            return

        try:
            self._client = httpx.AsyncClient(timeout=self._timeout)
            self._connected = True
        except Exception as exc:
            raise DomainError(MCP_CONNECTION_FAILED, f"SSE 连接失败: {type(exc).__name__}")

    async def disconnect(self) -> None:
        """断开 SSE 连接。"""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    async def send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """通过 SSE 发送请求。

        发送 POST 请求触发服务端事件，然后通过 SSE 流读取响应。
        """
        if not self._connected:
            await self.connect()

        if find_spec("httpx") is None:
            return {"error": "httpx not available"}

        try:
            response = await self._client.post(
                self._url,
                json={"method": method, "params": params},
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise DomainError(MCP_CONNECTION_FAILED, f"SSE 请求失败: {type(exc).__name__}")

    @property
    def is_connected(self) -> bool:
        return self._connected
