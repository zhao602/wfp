# =============================================================================
# 工作流定义路由（api/routers/workflow_definition_router.py）
#
# 职责：工作流定义生命周期 REST 接口（B11 定义版本与发布态）。
# 设计意图（spec 5.1、design 2.2.2.6、B11 design D3/D5/D7/D8）：
#   POST /workflows/validate — 校验 YAML 定义
#   POST /workflows — 保存定义（真落库：版本号 = max+1，draft 态）
#   GET  /workflows/{id} — 查询定义（version 可选，缺省最新；404 全局处理器）
#   POST /workflows/{id}/publish — 发布指定版本（缺省最新）
#   GET  /workflows/{id}/versions — 版本历史（富化：status/published_at）
#   POST /workflows/{id}/rollback — 发布回滚（旧内容发新号，历史线性不可变）
# 版本语义（用户裁决 2026-11-24）：
#   1. 每次保存分配新版本号（version = max+1，draft 态）——API 面结构性
#      不触发 published 改写；发布态不可变守卫在仓储写边界（409 不可绕过）；
#   2. 发布回滚：将 to_version 内容以新版本号重新发布（status=published、
#      draft_of=to_version 溯源），不改写不删除任何历史版本；
#   3. draft_of：保存时指向当前最新 published 版本（无则 None）。
# 错误映射（全局处理器）：ResourceNotFoundError→404；
#   InstanceStateConflictError(WF_STATE_CONFLICT)→409；校验失败→422。
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orchestration_platform.domain.errors import (
    WF_NOT_FOUND,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.workflow_definition import (
    DEFINITION_STATUS_DRAFT,
    DEFINITION_STATUS_PUBLISHED,
)
from orchestration_platform.domain.validation.validation_report_builder import (
    ValidationReportBuilder,
)
from orchestration_platform.persistence.repository_factory import RepositoryFactory
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

router = APIRouter(tags=["工作流定义"])

_parser = YamlWorkflowParser()
_report_builder = ValidationReportBuilder()


def _utc_now() -> datetime:
    """返回当前 UTC 时间（回滚发布时刻统一时间基准）。"""
    return datetime.now(timezone.utc)


def _workflow_repo():
    """经工厂获取工作流定义仓储（按 STORAGE_BACKEND 配置注入实现）。"""
    return RepositoryFactory.create_workflow_repository()


class ValidateRequest(BaseModel):
    yaml_text: str


class SaveRequest(BaseModel):
    yaml_text: str


class PublishRequest(BaseModel):
    version: Optional[int] = None


class RollbackRequest(BaseModel):
    to_version: int


@router.post("/workflows/validate")
async def validate_workflow(request: ValidateRequest) -> dict:
    """校验 YAML 工作流定义。"""
    try:
        definition = _parser.parse(request.yaml_text)
        report = _report_builder.validate(definition)
        return report.model_dump(mode="json")
    except WorkflowValidationError as e:
        return {"valid": False, "issues": [{"code": e.code, "message": e.message, "severity": "error"}], "definition_id": None}


@router.post("/workflows")
async def save_workflow(request: SaveRequest) -> dict:
    """保存工作流定义（B11：真落库，版本号 = max+1，draft 态）。

    返回富化 DefinitionRef dump（definition_id/definition_version/created_at/
    status/published_at）——前端 CreateWorkflowResponse 期望字段为超集兼容。
    """
    try:
        definition = _parser.parse(request.yaml_text)
        report = _report_builder.validate(definition)
        if not report.valid:
            raise WorkflowValidationError(
                "WF_VALIDATION_FAILED",
                "工作流定义校验未通过",
                {"issues": [i.model_dump(mode="json") for i in report.issues]},
            )
    except WorkflowValidationError as e:
        raise HTTPException(status_code=422, detail=e.to_dict())

    repo = _workflow_repo()
    latest = await repo.get_latest_version(definition.definition_id)
    published = await repo.get_published(definition.definition_id)
    definition.definition_version = (latest or 0) + 1
    definition.status = DEFINITION_STATUS_DRAFT
    definition.draft_of = published.definition_version if published else None
    definition.published_at = None
    ref = await repo.save(definition)
    return ref.model_dump(mode="json")


@router.post("/workflows/{definition_id}/publish")
async def publish_workflow(definition_id: str, request: PublishRequest) -> dict:
    """发布工作流定义版本（draft → published；缺省发布最新版本）。

    已发布版本重复发布幂等返回（非改写，不 409）。
    """
    repo = _workflow_repo()
    version = request.version
    if version is None:
        version = await repo.get_latest_version(definition_id)
        if version is None:
            raise ResourceNotFoundError(
                WF_NOT_FOUND,
                f"工作流定义 {definition_id} 不存在，无可发布版本",
            )
    ref = await repo.publish(definition_id, version)
    return ref.model_dump(mode="json")


@router.get("/workflows/{definition_id}")
async def get_workflow(definition_id: str, version: Optional[int] = None) -> dict:
    """查询工作流定义（version 缺省最新；不存在 404 全局处理器）。"""
    repo = _workflow_repo()
    definition = await repo.get(definition_id, version)
    return definition.model_dump(mode="json")


@router.get("/workflows/{definition_id}/versions")
async def list_workflow_versions(definition_id: str) -> list:
    """列出工作流定义版本历史（富化 status/published_at，按版本号降序）。"""
    repo = _workflow_repo()
    refs = await repo.list_versions(definition_id)
    return [ref.model_dump(mode="json") for ref in refs]


@router.post("/workflows/{definition_id}/rollback")
async def rollback_workflow(definition_id: str, request: RollbackRequest) -> dict:
    """发布回滚：将 to_version 内容以新版本号重新发布。

    历史线性不可变——不改写不删除既有版本；回滚产物 status=published、
    draft_of=to_version（溯源）；to_version 不存在 404。
    """
    repo = _workflow_repo()
    source = await repo.get(definition_id, request.to_version)
    latest = await repo.get_latest_version(definition_id)
    now = _utc_now()
    restored = source.model_copy(deep=True)
    restored.definition_version = (latest or 0) + 1
    restored.status = DEFINITION_STATUS_PUBLISHED
    restored.draft_of = request.to_version
    restored.published_at = now
    restored.created_at = now
    restored.updated_at = now
    ref = await repo.save(restored)
    return ref.model_dump(mode="json")
