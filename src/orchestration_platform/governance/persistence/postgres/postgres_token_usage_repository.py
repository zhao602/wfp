# TODO(tech-debt): B档候选孤儿（族群 B2），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# PostgreSQL 用量仓储（governance/persistence/postgres/postgres_token_usage_repository.py）
#
# 职责：以 PostgreSQL 持久化 Token 用量记录。
# 设计意图（对应 spec 5.2、design 2.5、任务 T58.2）：
#   1. 按 tenant_id 分区存储，延迟导入驱动；
#   2. 全部查询注入 tenant_id 过滤条件保证隔离（第一道保险）；
#   3. 仓储基类向连接注入 app.current_tenant 会话变量（RLS 第二道保险，
#      roadmap B1，见 persistence/postgres/tenant_scoped_base.py）。
# =============================================================================

from datetime import datetime
from typing import Any, Dict, List, Optional

from orchestration_platform.governance.metering.models import TokenUsageRecord
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


class PostgresTokenUsageRepository(TenantScopedPostgresRepository):
    """Token 用量仓储——PostgreSQL 实现。"""

    async def append(self, record: TokenUsageRecord) -> str:
        """追加用量记录。"""
        async with self._tenant_connection(record.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO governance_token_usage
                    (record_id, tenant_id, instance_id, node_id, agent_node_id,
                     model_identifier, prompt_tokens, completion_tokens, total_tokens,
                     recorded_at, workflow_definition_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (tenant_id, record_id) DO NOTHING
                """,
                record.record_id, record.tenant_id, record.instance_id,
                record.node_id, record.agent_node_id, record.model_identifier,
                record.prompt_tokens, record.completion_tokens, record.total_tokens,
                record.recorded_at, record.workflow_definition_id,
            )
        return record.record_id

    async def append_batch(
        self, records: List[TokenUsageRecord]
    ) -> List[str]:
        """批量追加用量记录。"""
        if not records:
            return []
        async with self._tenant_connection(records[0].tenant_id) as conn:
            await conn.executemany(
                """
                INSERT INTO governance_token_usage
                    (record_id, tenant_id, instance_id, node_id, agent_node_id,
                     model_identifier, prompt_tokens, completion_tokens, total_tokens,
                     recorded_at, workflow_definition_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (tenant_id, record_id) DO NOTHING
                """,
                [
                    (r.record_id, r.tenant_id, r.instance_id, r.node_id,
                     r.agent_node_id, r.model_identifier, r.prompt_tokens,
                     r.completion_tokens, r.total_tokens, r.recorded_at,
                     r.workflow_definition_id)
                    for r in records
                ],
            )
        return [r.record_id for r in records]

    async def list_by_instance(
        self, tenant_id: str, instance_id: str
    ) -> List[TokenUsageRecord]:
        """按实例查询用量记录。"""
        async with self._tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT * FROM governance_token_usage "
                "WHERE tenant_id = $1 AND instance_id = $2 ORDER BY recorded_at",
                tenant_id, instance_id,
            )
        return [self._row_to_record(r) for r in rows]

    async def list_by_node(
        self, tenant_id: str, instance_id: str, node_id: str
    ) -> List[TokenUsageRecord]:
        """按节点查询用量记录。"""
        async with self._tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT * FROM governance_token_usage "
                "WHERE tenant_id = $1 AND instance_id = $2 AND node_id = $3 "
                "ORDER BY recorded_at",
                tenant_id, instance_id, node_id,
            )
        return [self._row_to_record(r) for r in rows]

    async def list_by_agent(
        self, tenant_id: str, agent_node_id: str
    ) -> List[TokenUsageRecord]:
        """按 Agent 查询用量记录。"""
        async with self._tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT * FROM governance_token_usage "
                "WHERE tenant_id = $1 AND agent_node_id = $2",
                tenant_id, agent_node_id,
            )
        return [self._row_to_record(r) for r in rows]

    async def list_by_tenant(
        self, tenant_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[TokenUsageRecord]:
        """按租户查询用量明细记录（roadmap B7 W1a：usage 端点聚合数据源）。"""
        async with self._tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT * FROM governance_token_usage WHERE tenant_id = $1 "
                "AND ($2::timestamp IS NULL OR recorded_at >= $2) "
                "AND ($3::timestamp IS NULL OR recorded_at < $3) "
                "ORDER BY recorded_at",
                tenant_id, start_time, end_time,
            )
        return [self._row_to_record(r) for r in rows]

    async def aggregate_by_instance(
        self, tenant_id: str, instance_id: str
    ) -> Dict[str, Any]:
        """按实例汇总用量。"""
        async with self._tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(SUM(prompt_tokens), 0) AS total_prompt, "
                "COALESCE(SUM(completion_tokens), 0) AS total_completion, "
                "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
                "COUNT(*) AS record_count "
                "FROM governance_token_usage WHERE tenant_id = $1 AND instance_id = $2",
                tenant_id, instance_id,
            )
        return {
            "total_prompt_tokens": row["total_prompt"],
            "total_completion_tokens": row["total_completion"],
            "total_tokens": row["total_tokens"],
            "record_count": row["record_count"],
        }

    async def aggregate_by_agent(
        self, tenant_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """按 Agent 汇总用量。"""
        async with self._tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT agent_node_id, COALESCE(SUM(prompt_tokens), 0) AS total_prompt, "
                "COALESCE(SUM(completion_tokens), 0) AS total_completion, "
                "COALESCE(SUM(total_tokens), 0) AS total_tokens, COUNT(*) AS record_count "
                "FROM governance_token_usage WHERE tenant_id = $1 "
                "AND ($2::timestamp IS NULL OR recorded_at >= $2) "
                "AND ($3::timestamp IS NULL OR recorded_at < $3) "
                "GROUP BY agent_node_id",
                tenant_id, start_time, end_time,
            )
        return {
            r["agent_node_id"]: {
                "total_prompt_tokens": r["total_prompt"],
                "total_completion_tokens": r["total_completion"],
                "total_tokens": r["total_tokens"],
                "record_count": r["record_count"],
            }
            for r in rows
        }

    async def aggregate_by_tenant(
        self, tenant_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """按租户汇总用量。"""
        async with self._tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(SUM(prompt_tokens), 0) AS total_prompt, "
                "COALESCE(SUM(completion_tokens), 0) AS total_completion, "
                "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
                "COUNT(*) AS record_count "
                "FROM governance_token_usage WHERE tenant_id = $1 "
                "AND ($2::timestamp IS NULL OR recorded_at >= $2) "
                "AND ($3::timestamp IS NULL OR recorded_at < $3)",
                tenant_id, start_time, end_time,
            )
        return {
            "total_prompt_tokens": row["total_prompt"],
            "total_completion_tokens": row["total_completion"],
            "total_tokens": row["total_tokens"],
            "record_count": row["record_count"],
        }

    @staticmethod
    def _row_to_record(row) -> TokenUsageRecord:
        """将数据库行映射为 TokenUsageRecord。"""
        return TokenUsageRecord(
            record_id=row["record_id"],
            tenant_id=row["tenant_id"],
            instance_id=row["instance_id"],
            node_id=row["node_id"],
            agent_node_id=row["agent_node_id"],
            model_identifier=row["model_identifier"],
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
            total_tokens=row["total_tokens"],
            recorded_at=row["recorded_at"],
            workflow_definition_id=row["workflow_definition_id"],
        )
