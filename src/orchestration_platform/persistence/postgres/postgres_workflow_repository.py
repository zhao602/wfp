# =============================================================================
# PostgreSQL 工作流定义仓储（persistence/postgres/postgres_workflow_repository.py）
#
# 职责：以 PostgreSQL 持久化工作流定义，支持版本化保存与历史查询。
# 设计意图（对应 spec 4.5.3、design 2.2.2.3）：
#   1. 按 definition_id + definition_version 唯一存储，保留全部历史版本；
#   2. 使用 asyncpg 异步驱动，INSERT ON CONFLICT 实现幂等保存；
#   3. 多租户强隔离（roadmap B1）：可选 tenant_id 参数缺省回退默认租户
#      （settings.default_tenant_id）；仓储基类注入 RLS 会话变量为第二道保险；
#      upsert 的 ON CONFLICT DO UPDATE 不重指派 tenant_id（所有权稳定）；
#   4. B11 定义版本与发布态：发布状态机三列（v1_10）随 save 写入；
#      发布态不可变守卫 = 单语句 ON CONFLICT DO UPDATE ... WHERE
#      status <> 'published' RETURNING——冲突且发布态 → WHERE 不满足 →
#      0 行 → 抛 WF_STATE_CONFLICT（语句级原子，无 SELECT-UPDATE 竞态窗口）；
#      get_latest_version/get_published/publish 三方法（协议扩展）。
# =============================================================================

from datetime import datetime, timezone
from typing import List, Optional

from orchestration_platform.domain.errors import (
    WF_NOT_FOUND,
    WF_STATE_CONFLICT,
    InstanceStateConflictError,
    ResourceNotFoundError,
)
from orchestration_platform.domain.models.execution_instance import DefinitionRef
from orchestration_platform.domain.models.workflow_definition import (
    DEFINITION_STATUS_PUBLISHED,
    WorkflowDefinition,
)
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


def _utc_now() -> datetime:
    """返回当前 UTC 时间（发布时刻统一时间基准）。"""
    return datetime.now(timezone.utc)


class PostgresWorkflowRepository(TenantScopedPostgresRepository):
    """工作流定义仓储——PostgreSQL 实现。"""

    async def save(
        self, definition: WorkflowDefinition, tenant_id: Optional[str] = None
    ) -> DefinitionRef:
        """保存工作流定义（幂等：同版本覆盖；tenant_id 缺省回退默认租户）。

        B11 发布态不可变守卫：目标版本已 published → ON CONFLICT 的
        WHERE 谓词不满足 → 0 行 → 抛 InstanceStateConflictError
        (WF_STATE_CONFLICT)（API 面 409）。
        """
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO workflow_definition
                    (tenant_id, definition_id, definition_version, name, spec_version,
                     definition_body, status, draft_of, published_at, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
                ON CONFLICT (definition_id, definition_version)
                DO UPDATE SET definition_body = $6, status = $7, draft_of = $8,
                              published_at = $9, updated_at = NOW()
                WHERE workflow_definition.status <> 'published'
                RETURNING status
                """,
                resolved_tenant,
                definition.definition_id,
                definition.definition_version,
                definition.name,
                definition.spec_version,
                definition.model_dump_json(),
                definition.status,
                definition.draft_of,
                definition.published_at,
            )
        if row is None:
            raise InstanceStateConflictError(
                WF_STATE_CONFLICT,
                f"工作流定义 {definition.definition_id} 版本 "
                f"{definition.definition_version} 已发布，发布态不可改写",
                {
                    "definition_id": definition.definition_id,
                    "definition_version": definition.definition_version,
                },
            )
        return DefinitionRef(
            definition_id=definition.definition_id,
            definition_version=definition.definition_version,
            status=definition.status,
            published_at=definition.published_at,
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
        """列出全部历史版本，按版本号降序（B11：富化 status/published_at）。"""
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            rows = await conn.fetch(
                "SELECT definition_id, definition_version, status, published_at "
                "FROM workflow_definition "
                "WHERE definition_id=$1 AND tenant_id=$2 "
                "ORDER BY definition_version DESC",
                definition_id, resolved_tenant,
            )
        return [
            DefinitionRef(
                definition_id=r["definition_id"],
                definition_version=r["definition_version"],
                status=r["status"],
                published_at=r["published_at"],
            )
            for r in rows
        ]

    async def get_latest_version(
        self, definition_id: str, tenant_id: Optional[str] = None
    ) -> Optional[int]:
        """查询指定定义的当前最大版本号；无任何版本返回 None（B11）。"""
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            row = await conn.fetchrow(
                "SELECT MAX(definition_version) AS latest_version "
                "FROM workflow_definition "
                "WHERE definition_id=$1 AND tenant_id=$2",
                definition_id, resolved_tenant,
            )
        if row is None or row["latest_version"] is None:
            return None
        return row["latest_version"]

    async def get_published(
        self, definition_id: str, tenant_id: Optional[str] = None
    ) -> Optional[WorkflowDefinition]:
        """查询指定定义的最新已发布版本；无已发布版本返回 None（B11）。"""
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            row = await conn.fetchrow(
                "SELECT definition_body FROM workflow_definition "
                "WHERE definition_id=$1 AND tenant_id=$2 AND status='published' "
                "ORDER BY definition_version DESC LIMIT 1",
                definition_id, resolved_tenant,
            )
        if row is None:
            return None
        return WorkflowDefinition.model_validate_json(row["definition_body"])

    async def publish(
        self,
        definition_id: str,
        version: int,
        tenant_id: Optional[str] = None,
    ) -> DefinitionRef:
        """将指定版本置为发布态（draft → published；已发布幂等返回；B11）。

        definition_body 与状态列同事务双写（get() 读 body 为权威——
        事件/快照消费方读 body 内嵌 status，列值供 SQL 谓词使用）。
        """
        resolved_tenant = self._resolve_tenant(tenant_id)
        async with self._tenant_connection(resolved_tenant) as conn:
            row = await conn.fetchrow(
                "SELECT definition_body FROM workflow_definition "
                "WHERE definition_id=$1 AND definition_version=$2 AND tenant_id=$3",
                definition_id, version, resolved_tenant,
            )
            if row is None:
                raise ResourceNotFoundError(
                    WF_NOT_FOUND,
                    f"工作流定义 {definition_id} 版本 {version} 不存在",
                )
            definition = WorkflowDefinition.model_validate_json(row["definition_body"])
            if definition.status == DEFINITION_STATUS_PUBLISHED:
                return DefinitionRef(
                    definition_id=definition_id,
                    definition_version=version,
                    status=definition.status,
                    published_at=definition.published_at,
                )
            now = _utc_now()
            definition.status = DEFINITION_STATUS_PUBLISHED
            definition.published_at = now
            definition.updated_at = now
            await conn.execute(
                "UPDATE workflow_definition "
                "SET definition_body=$4, status=$5, published_at=$6, updated_at=NOW() "
                "WHERE definition_id=$1 AND definition_version=$2 AND tenant_id=$3",
                definition_id,
                version,
                resolved_tenant,
                definition.model_dump_json(),
                definition.status,
                definition.published_at,
            )
        return DefinitionRef(
            definition_id=definition_id,
            definition_version=version,
            status=definition.status,
            published_at=definition.published_at,
        )
