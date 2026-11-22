# TODO(tech-debt): B档候选孤儿（族群 B6），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# Stdio 传输（integrations/mcp/transports/stdio_transport.py）
#
# 职责：通过标准输入输出实现 MCP 传输。
# 设计意图（对应 spec 5.3.1.5、任务 T33.2）：
#   将第一阶段分散的 stdio 传输逻辑独立化为统一传输模块。
# =============================================================================

import asyncio
import json
from typing import Any, Dict

from orchestration_platform.domain.errors import MCP_CONNECTION_FAILED, DomainError


class StdioTransport:
    """Stdio 传输：通过子进程标准输入输出与 MCP 服务通信。"""

    def __init__(self, command: str, timeout_seconds: int = 30) -> None:
        """初始化 stdio 传输。

        Args:
            command: MCP 服务启动命令。
            timeout_seconds: 请求超时时间。
        """
        self._command = command
        self._timeout = timeout_seconds
        self._connected = False
        self._process: asyncio.subprocess.Process | None = None

    async def connect(self) -> None:
        """启动子进程建立 stdio 连接。"""
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command.split(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._connected = True
        except Exception as exc:
            raise DomainError(MCP_CONNECTION_FAILED, f"stdio 连接失败: {type(exc).__name__}")

    async def disconnect(self) -> None:
        """终止子进程断开连接。"""
        if self._process:
            self._process.terminate()
            await self._process.wait()
            self._process = None
        self._connected = False

    async def send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """通过 stdin 发送 JSON-RPC 请求，从 stdout 读取响应。"""
        if not self._connected:
            await self.connect()

        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise DomainError(MCP_CONNECTION_FAILED, "stdio 进程未就绪")

        request = json.dumps({"method": method, "params": params}) + "\n"
        self._process.stdin.write(request.encode("utf-8"))
        await self._process.stdin.drain()

        try:
            line = await asyncio.wait_for(
                self._process.stdout.readline(), timeout=self._timeout
            )
            return json.loads(line.decode("utf-8"))
        except asyncio.TimeoutError:
            from orchestration_platform.domain.errors import MCP_TOOL_TIMEOUT, DomainError
            raise DomainError(MCP_TOOL_TIMEOUT, "stdio 请求超时")
        except Exception as exc:
            raise DomainError(MCP_CONNECTION_FAILED, f"stdio 响应读取失败: {type(exc).__name__}")

    @property
    def is_connected(self) -> bool:
        return self._connected
