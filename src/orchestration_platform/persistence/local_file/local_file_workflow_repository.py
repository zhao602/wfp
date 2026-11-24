# =============================================================================
# 本地文件工作流定义仓储（persistence/local_file/local_file_workflow_repository.py）
#
# 职责：以 JSON 文件持久化工作流定义，按 definition_id + definition_version 组织。
# 设计意图（对应需求规格 4.6 降级要求、design 2.2.2.3）：
#   1. 目录结构：{root}/workflow_definitions/{definition_id}/v{version}.json
#   2. 保留全部历史版本——每次保存按版本号独立文件，不覆盖旧版本；
#   3. 同一版本重复保存为幂等覆盖（避免重复创建文件）；
#   4. B11 定义版本与发布态：draft/published 状态机落 JSON 字段；
#      发布态 save 守卫（WF_STATE_CONFLICT/409 写边界强制）；
#      get_latest_version/get_published/publish 三方法（协议扩展）。
#      单进程无锁——并发改写竞态 best-effort（与配额准入同档诚实口径）。
# =============================================================================

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
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


def _utc_now() -> datetime:
    """返回当前 UTC 时间（发布时刻统一时间基准）。"""
    return datetime.now(timezone.utc)


class LocalFileWorkflowRepository:
    """工作流定义仓储——本地文件实现。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        """构造仓储实例。

        参数：
            storage_root —— 存储根目录；缺省读取配置 local_storage_root。
        """
        root = storage_root or get_settings().local_storage_root
        self._base_dir = Path(root) / "workflow_definitions"

    def _definition_dir(self, definition_id: str) -> Path:
        """返回指定定义的目录路径。"""
        return self._base_dir / definition_id

    def _version_file(self, definition_id: str, version: int) -> Path:
        """返回指定版本的文件路径。"""
        return self._definition_dir(definition_id) / f"v{version}.json"

    @staticmethod
    def _ref(definition: WorkflowDefinition) -> DefinitionRef:
        """由定义构造富化 DefinitionRef（status/published_at 随行）。"""
        return DefinitionRef(
            definition_id=definition.definition_id,
            definition_version=definition.definition_version,
            status=definition.status,
            published_at=definition.published_at,
            created_at=definition.created_at,
        )

    async def save(self, definition: WorkflowDefinition) -> DefinitionRef:
        """保存工作流定义，按版本号独立文件存储。

        B11 发布态不可变守卫：目标版本文件已存在且 status=published 时
        抛 InstanceStateConflictError(WF_STATE_CONFLICT)——发布态版本
        禁止改写（写边界强制；draft 同号幂等覆盖沿协议既有语义）。
        """
        file_path = self._version_file(definition.definition_id, definition.definition_version)
        if file_path.exists():
            existing = self._load_definition(file_path)
            if existing.status == DEFINITION_STATUS_PUBLISHED:
                raise InstanceStateConflictError(
                    WF_STATE_CONFLICT,
                    f"工作流定义 {definition.definition_id} 版本 "
                    f"{definition.definition_version} 已发布，发布态不可改写",
                    {
                        "definition_id": definition.definition_id,
                        "definition_version": definition.definition_version,
                    },
                )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            definition.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return self._ref(definition)

    async def get(
        self, definition_id: str, version: Optional[int] = None
    ) -> WorkflowDefinition:
        """按标识查询定义；version 缺省返回最新版本。"""
        def_dir = self._definition_dir(definition_id)

        if version is not None:
            file_path = self._version_file(definition_id, version)
            if not file_path.exists():
                raise ResourceNotFoundError(
                    WF_NOT_FOUND,
                    f"工作流定义 {definition_id} 版本 {version} 不存在",
                )
            return self._load_definition(file_path)

        # 查询最新版本：扫描目录下所有 v*.json，取最大版本号
        if not def_dir.exists():
            raise ResourceNotFoundError(
                WF_NOT_FOUND,
                f"工作流定义 {definition_id} 不存在",
            )

        version_files = sorted(
            def_dir.glob("v*.json"),
            key=lambda p: int(p.stem[1:]),
        )
        if not version_files:
            raise ResourceNotFoundError(
                WF_NOT_FOUND,
                f"工作流定义 {definition_id} 不存在",
            )

        return self._load_definition(version_files[-1])

    async def list_versions(self, definition_id: str) -> List[DefinitionRef]:
        """列出指定定义的全部历史版本，按版本号降序排列。

        B11：refs 富化 status/published_at（旧版 JSON 缺字段回填
        draft/None——与 v1_10 迁移 DEFAULT 'draft' 口径一致）。
        """
        def_dir = self._definition_dir(definition_id)
        if not def_dir.exists():
            return []

        refs: List[DefinitionRef] = []
        for file_path in def_dir.glob("v*.json"):
            data = json.loads(file_path.read_text(encoding="utf-8"))
            refs.append(
                DefinitionRef(
                    definition_id=data["definition_id"],
                    definition_version=data["definition_version"],
                    status=data.get("status", "draft"),
                    published_at=data.get("published_at"),
                )
            )
        refs.sort(key=lambda r: r.definition_version, reverse=True)
        return refs

    async def get_latest_version(self, definition_id: str) -> Optional[int]:
        """查询指定定义的当前最大版本号；无任何版本返回 None。"""
        def_dir = self._definition_dir(definition_id)
        if not def_dir.exists():
            return None
        versions = [int(p.stem[1:]) for p in def_dir.glob("v*.json")]
        return max(versions) if versions else None

    async def get_published(
        self, definition_id: str
    ) -> Optional[WorkflowDefinition]:
        """查询指定定义的最新已发布版本；无已发布版本返回 None。"""
        def_dir = self._definition_dir(definition_id)
        if not def_dir.exists():
            return None
        best: Optional[WorkflowDefinition] = None
        for file_path in def_dir.glob("v*.json"):
            definition = self._load_definition(file_path)
            if definition.status != DEFINITION_STATUS_PUBLISHED:
                continue
            if best is None or definition.definition_version > best.definition_version:
                best = definition
        return best

    async def publish(
        self, definition_id: str, version: int
    ) -> DefinitionRef:
        """将指定版本置为发布态（draft → published；已发布幂等返回）。"""
        file_path = self._version_file(definition_id, version)
        if not file_path.exists():
            raise ResourceNotFoundError(
                WF_NOT_FOUND,
                f"工作流定义 {definition_id} 版本 {version} 不存在",
            )
        definition = self._load_definition(file_path)
        if definition.status == DEFINITION_STATUS_PUBLISHED:
            return self._ref(definition)
        now = _utc_now()
        definition.status = DEFINITION_STATUS_PUBLISHED
        definition.published_at = now
        definition.updated_at = now
        file_path.write_text(
            definition.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return self._ref(definition)

    def _load_definition(self, file_path: Path) -> WorkflowDefinition:
        """从 JSON 文件加载 WorkflowDefinition。"""
        return WorkflowDefinition.model_validate_json(
            file_path.read_text(encoding="utf-8")
        )
