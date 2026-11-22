# =============================================================================
# PostgreSQL 工作流定义仓储（persistence/postgres/postgres_workflow_repository.py）
#
# 职责：以 PostgreSQL 持久化工作流定义，支持版本化保存与历史查询。
# 设计意图（对应 spec 4.5.3、design 2.2.2.3）：
#   1. 按 definition_id + definition_version 唯一存储，保留全部历史版本；
#   2. 使用 asyncpg 异步驱动，INSERT ON CONFLICT 实现幂等保存；
#   3. 多租户强隔离（roadmap B1）：可选 tenant_id 参数缺省回退默认租户
#      （settings.default_tenant_id）；仓储基类注入 RLS 会话变量为第二道保险；
#      upsert 的 ON CONFLICT DO UPDATE 不重指派 tenant_id（所有权稳定）。
# =============================================================================

from typing import List, Optional

from orchestration_platform.domain.errors import WF_NOT_FOUND, ResourceNotFoundError
from orchestration_platform.domain.models.execution_instance import DefinitionRef
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


class PostgresWorkflowRepository(TenantScopedPostgresRepository):
    """工作流定义仓储——PostgreSQL 实现。"""

    async def save(
        self, definition: WorkflowDefinition, tenant_id: Optional[str] = None
    ) -> DefinitionRef:
        """保存工作流定义（幂等：同版本覆盖；tenant_id 缺省回退默认租户）。"""
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            await conn.execute(
                """
                INSERT INTO workflow_definition
                    (tenant_id, definition_id, definition_version, name, spec_version,
                     definition_body, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
                ON CONFLICT (definition_id, definition_version)
                DO UPDATE SET definition_body = $6, updated_at = NOW()
                """,
                resolved_tenant,
                definition.definition_id,
                definition.definition_version,
                definition.name,
                definition.spec_version,
                definition.model_dump_json(),
            )
        return DefinitionRef(
            definition_id=definition.definition_id,
            definition_version=definition.definition_version,
        )

    async def get(
        self,
        definition_id: str,
        version: Optional[int] = None,
        tenant_id: Optional[str] = None,
    ) -> WorkflowDefinition:
        """按标识查询定义；version 缺省返回最新版本。"""
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            if version is not None:
                row = await conn.fetchrow(
                    "SELECT definition_body FROM workflow_definition "
                    "WHERE definition_id=$1 AND definition_version=$2 AND tenant_id=$3",
                    definition_id, version, resolved_tenant,
                )
            else:
                row = await conn.fetchrow(
                    "SELECT definition_body FROM workflow_definition "
                    "WHERE definition_id=$1 AND tenant_id=$2 "
                    "ORDER BY definition_version DESC LIMIT 1",
                    definition_id, resolved_tenant,
                )
        if row is None:
            raise ResourceNotFoundError(WF_NOT_FOUND, f"工作流定义 {definition_id} 不存在")
        return WorkflowDefinition.model_validate_json(row["definition_body"])

    async def list_versions(
        self, definition_id: str, tenant_id: Optional[str] = None
    ) -> List[DefinitionRef]:
        """列出全部历史版本，按版本号降序。"""
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            rows = await conn.fetch(
                "SELECT definition_id, definition_version FROM workflow_definition "
                "WHERE definition_id=$1 AND tenant_id=$2 "
                "ORDER BY definition_version DESC",
                definition_id, resolved_tenant,
            )
        return [
            DefinitionRef(definition_id=r["definition_id"], definition_version=r["definition_version"])
            for r in rows
        ]
