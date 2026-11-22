# =============================================================================
# PostgreSQL 用户开发提交仓储（persistence/postgres/postgres_dev_submission_repository.py）
#
# 职责（agent_market_user_development T10.3 / CHG-10 / DC-03）：
#   以 PostgreSQL 持久化用户开发提交（单条目单行 JSONB）。
# 设计意图：
#   1. psycopg 仅在方法内延迟导入——模块顶层零 psycopg / 零 asyncpg
#      （INV-DEV-08/11）；无驱动不可用时异常向上抛出，由上层降级；
#   2. 连接参数经既有 settings 的 postgres_* 字段组装，不硬编码凭据；
#   3. 可构造实例（无驱动不崩溃）。
# 约束：Python 3.10 兼容；零第三方新增依赖。
# =============================================================================

from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.market.dev_models import DevSubmission


class PostgresDevSubmissionRepository:
    """用户开发提交仓储——PostgreSQL 实现（IDevSubmissionRepository，DC-03）。

    使用 psycopg（方法内延迟导入）访问 PostgreSQL。
    """

    def __init__(self, connection_string: Optional[str] = None) -> None:
        """构造仓储实例（不触发驱动导入）。"""
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
    def _parse_submission(data: object) -> DevSubmission:
        """兼容 psycopg JSONB 自动反序列化（dict）与原始字符串两种形态。"""
        if isinstance(data, str):
            return DevSubmission.model_validate_json(data)
        return DevSubmission.model_validate(data)

    async def save(self, submission: DevSubmission) -> None:
        """保存提交（同标识幂等覆盖）。"""
        import psycopg  # 延迟导入：无驱动时向上抛错，由上层降级

        async with await psycopg.AsyncConnection.connect(self._conninfo()) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_market_dev_submissions (
                        submission_id TEXT PRIMARY KEY,
                        developer_id TEXT NOT NULL,
                        node_name TEXT NOT NULL,
                        version TEXT NOT NULL,
                        state TEXT NOT NULL,
                        data JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await cur.execute(
                    """
                    INSERT INTO agent_market_dev_submissions
                        (submission_id, developer_id, node_name, version,
                         state, data, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, NOW())
                    ON CONFLICT (submission_id)
                    DO UPDATE SET
                        developer_id = EXCLUDED.developer_id,
                        node_name = EXCLUDED.node_name,
                        version = EXCLUDED.version,
                        state = EXCLUDED.state,
                        data = EXCLUDED.data,
                        updated_at = NOW()
                    """,
                    (
                        submission.submission_id,
                        submission.developer_id,
                        submission.node_name,
                        submission.version,
                        submission.state.value,
                        submission.model_dump_json(),
                    ),
                )

    async def get(self, submission_id: str) -> Optional[DevSubmission]:
        """按标识读取提交；不存在返回 None。"""
        import psycopg  # 延迟导入：无驱动时向上抛错，由上层降级

        async with await psycopg.AsyncConnection.connect(self._conninfo()) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT data FROM agent_market_dev_submissions"
                    " WHERE submission_id = %s",
                    (submission_id,),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        return self._parse_submission(row[0])

    async def list_all(self) -> List[DevSubmission]:
        """列出全部提交（按 submission_id 排序）。"""
        import psycopg  # 延迟导入：无驱动时向上抛错，由上层降级

        async with await psycopg.AsyncConnection.connect(self._conninfo()) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT data FROM agent_market_dev_submissions"
                    " ORDER BY submission_id"
                )
                rows = await cur.fetchall()
        return [self._parse_submission(row[0]) for row in rows]

    async def list_by_state(
        self, state: ContributionState
    ) -> List[DevSubmission]:
        """按状态列出提交（按 submission_id 排序）。"""
        import psycopg  # 延迟导入：无驱动时向上抛错，由上层降级

        async with await psycopg.AsyncConnection.connect(self._conninfo()) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT data FROM agent_market_dev_submissions"
                    " WHERE state = %s ORDER BY submission_id",
                    (state.value,),
                )
                rows = await cur.fetchall()
        return [self._parse_submission(row[0]) for row in rows]

    async def exists_by_node_version(
        self, developer_id: str, node_name: str, version: str
    ) -> bool:
        """按开发者 + 节点名 + 版本判重。"""
        import psycopg  # 延迟导入：无驱动时向上抛错，由上层降级

        async with await psycopg.AsyncConnection.connect(self._conninfo()) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM agent_market_dev_submissions"
                    " WHERE developer_id = %s AND node_name = %s"
                    " AND version = %s LIMIT 1",
                    (developer_id, node_name, version),
                )
                row = await cur.fetchone()
        return row is not None
