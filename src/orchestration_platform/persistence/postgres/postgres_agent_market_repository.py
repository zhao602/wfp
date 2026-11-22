# =============================================================================
# PostgreSQL Agent 市场仓储（persistence/postgres/postgres_agent_market_repository.py）
#
# 职责：以 PostgreSQL 持久化 Agent 市场条目，单条目单行 JSONB。
# 设计意图（对应 design DC-02 / CHG-06、任务 T5.1、T5.3）：
#   1. psycopg 仅在方法内延迟导入——模块顶层零 psycopg（INV-D07）；
#      真实 PG 分支复跑见 T20.8（非沙箱）；
#   2. 无驱动或连接不可用时异常向上抛出，由服务层按 DC-12 降级；
#   3. 连接参数经既有 settings 的 postgres_* 字段组装，不硬编码凭据（T5.3）；
#   4. 零禁用驱动引用（INV-D06 禁用清单内驱动不在本文件出现）。
# =============================================================================

from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.market.models import AgentMarketEntry


class PostgresAgentMarketRepository:
    """Agent 市场条目仓储——PostgreSQL 实现（IAgentMarketRepository）。

    使用 psycopg（方法内延迟导入）访问 PostgreSQL。
    """

    def __init__(self, connection_string: Optional[str] = None) -> None:
        """构造仓储实例（不触发驱动导入）。

        参数：
            connection_string —— PostgreSQL 连接串；缺省在首次调用时由既有
            settings 的 postgres_* 字段组装（不硬编码凭据）。
        """
        self._connection_string = connection_string

    def _conninfo(self) -> str:
        """解析连接串：显式传入优先，否则从既有 settings 组装。"""
        if self._connection_string:
            return self._connection_string
        settings = get_settings()
        return (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_db} user={settings.postgres_user} "
            f"password={settings.postgres_password}"
        )

    @staticmethod
    def _parse_entry(data: object) -> AgentMarketEntry:
        """兼容 psycopg JSONB 自动反序列化（dict）与原始字符串两种形态。"""
        if isinstance(data, str):
            return AgentMarketEntry.model_validate_json(data)
        return AgentMarketEntry.model_validate(data)

    async def save(self, entry: AgentMarketEntry) -> None:
        """保存条目（同标识幂等覆盖）。"""
        import psycopg  # 延迟导入：无驱动时向上抛错，由上层降级

        async with await psycopg.AsyncConnection.connect(self._conninfo()) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_market_entries (
                        entry_id TEXT PRIMARY KEY,
                        data JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await cur.execute(
                    """
                    INSERT INTO agent_market_entries (entry_id, data, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (entry_id)
                    DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
                    """,
                    (entry.entry_id, entry.model_dump_json()),
                )

    async def get(self, entry_id: str) -> Optional[AgentMarketEntry]:
        """按标识读取条目；不存在返回 None。"""
        import psycopg  # 延迟导入：无驱动时向上抛错，由上层降级

        async with await psycopg.AsyncConnection.connect(self._conninfo()) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT data FROM agent_market_entries WHERE entry_id = %s",
                    (entry_id,),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        return self._parse_entry(row[0])

    async def list_all(self) -> List[AgentMarketEntry]:
        """列出全部条目（按 entry_id 排序）。"""
        import psycopg  # 延迟导入：无驱动时向上抛错，由上层降级

        async with await psycopg.AsyncConnection.connect(self._conninfo()) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT data FROM agent_market_entries ORDER BY entry_id"
                )
                rows = await cur.fetchall()
        return [self._parse_entry(row[0]) for row in rows]

    async def exists(self, entry_id: str) -> bool:
        """检查条目是否存在。"""
        import psycopg  # 延迟导入：无驱动时向上抛错，由上层降级

        async with await psycopg.AsyncConnection.connect(self._conninfo()) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM agent_market_entries WHERE entry_id = %s",
                    (entry_id,),
                )
                row = await cur.fetchone()
        return row is not None
