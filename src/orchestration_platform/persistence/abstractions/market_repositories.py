# =============================================================================
# 市场仓储抽象接口（persistence/abstractions/market_repositories.py）
#
# 职责：定义 Agent 市场与 Tool 市场仓储接口（agent_tool_marketplace DC-02 / T3）。
# 设计意图：
#   1. 对齐既有 IMcpToolMarketRepository 风格（@runtime_checkable Protocol，异步方法）；
#   2. 不改动 persistence/abstractions/repositories.py（既有三类 Protocol 零变更）；
#   3. PostgreSQL 与本地文件两套实现共享此接口，经 RepositoryFactory 按配置注入。
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.market.models import AgentMarketEntry, ToolMarketEntry


@runtime_checkable
class IAgentMarketRepository(Protocol):
    """Agent 市场条目仓储接口（save / get / list_all / exists）。"""

    async def save(self, entry: AgentMarketEntry) -> None:
        """保存市场条目（entry_id 唯一存储，重复保存覆盖更新）。"""
        ...

    async def get(self, entry_id: str) -> Optional[AgentMarketEntry]:
        """按 entry_id 获取市场条目，不存在返回 None。"""
        ...

    async def list_all(self) -> List[AgentMarketEntry]:
        """列出全部市场条目。"""
        ...

    async def exists(self, entry_id: str) -> bool:
        """检查条目是否已存在。"""
        ...


@runtime_checkable
class IToolMarketRepository(Protocol):
    """Tool 市场条目仓储接口（save / list_all / exists）。

    Tool 市场的注册落盘复用既有 McpToolMarket 仓储（零改动）；
    本接口仅承载市场侧元数据扩展位（本期可为零实现）。
    """

    async def save(self, entry: ToolMarketEntry) -> None:
        """保存市场侧工具元数据条目。"""
        ...

    async def list_all(self) -> List[ToolMarketEntry]:
        """列出全部市场侧工具元数据条目。"""
        ...

    async def exists(self, tool_identifier: str) -> bool:
        """检查工具标识是否已存在。"""
        ...
