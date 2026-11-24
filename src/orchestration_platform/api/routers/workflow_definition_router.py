# =============================================================================
# 工作流定义路由（api/routers/workflow_definition_router.py）
#
# 职责：定义工作流定义相关 REST 接口。
# 设计意图（对应 spec 5.1、design 2.2.2.6）：
#   POST /workflows/validate — 校验 YAML 定义
#   POST /workflows — 保存定义
#   GET /workflows/{id} — 查询定义
# =============================================================================

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orchestration_platform.domain.errors import WorkflowValidationError
from orchestration_platform.domain.validation.validation_report_builder import (
    ValidationReportBuilder,
)
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

router = APIRouter(tags=["工作流定义"])

_parser = YamlWorkflowParser()
_report_builder = ValidationReportBuilder()


class ValidateRequest(BaseModel):
    yaml_text: str


class SaveRequest(BaseModel):
    yaml_text: str


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
    """保存工作流定义。"""
    try:
        definition = _parser.parse(request.yaml_text)
        report = _report_builder.validate(definition)
        if not report.valid:
            raise WorkflowValidationError(
                "WF_VALIDATION_FAILED",
                "工作流定义校验未通过",
                {"issues": [i.model_dump(mode="json") for i in report.issues]},
            )
        return {
            "definition_id": definition.definition_id,
            "definition_version": definition.definition_version,
        }
    except WorkflowValidationError as e:
        raise HTTPException(status_code=422, detail=e.to_dict())


@router.get("/workflows/{definition_id}")
async def get_workflow(definition_id: str, version: int = None) -> dict:
    """查询工作流定义。"""
    from orchestration_platform.persistence.repository_factory import RepositoryFactory
    repo = RepositoryFactory.create_workflow_repository()
    try:
        import asyncio
        if asyncio.get_event_loop_policy().get_event_loop().is_running():
            definition = await repo.get(definition_id, version)
        else:
            definition = repo.get(definition_id, version).send(None)
        return definition.model_dump(mode="json")
    except Exception as e:
        raise HTTPException(status_code=404, detail={"code": "WF_NOT_FOUND", "message": str(e)})
