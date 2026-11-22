# =============================================================================
# 评估框架 REST API（api/routers/governance/evaluation_router.py）
#
# 职责：评估场景集、评估任务、基线、回归对比的 REST 端点。
# 设计意图（对应偏差修正 T191）：
#   - 场景集：POST/GET/DELETE /api/v1/governance/evaluation/scenario-sets
#   - 评估任务：POST/GET /api/v1/governance/evaluation/tasks
#   - 基线：POST/GET /api/v1/governance/evaluation/baselines
#   - 状态：GET /api/v1/governance/evaluation/status
#   - evaluation_enabled=False 时路由不注册（由 application_factory 控制）。
# =============================================================================

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import DomainError
from orchestration_platform.domain.models.enums import EvaluationType
from orchestration_platform.governance.evaluation.baseline_store import BaselineStore
from orchestration_platform.governance.evaluation.evaluation_repository_local import (
    LocalEvaluationRepository,
)
from orchestration_platform.governance.evaluation.evaluation_runner import (
    EvaluationRunner,
)
from orchestration_platform.governance.evaluation.models import (
    EvaluationScenario,
    EvaluationScenarioSet,
)
from orchestration_platform.governance.evaluation.scenario_set_service import (
    ScenarioSetService,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent 效能评估"])

# 单例仓储与服务（进程内）
_repository = LocalEvaluationRepository()
_scenario_set_service = ScenarioSetService(_repository)
_baseline_store = BaselineStore(_repository)
_evaluation_runner = EvaluationRunner(_repository)


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------

class ScenarioItem(BaseModel):
    """场景条目请求。"""
    name: str = Field(default="", description="场景名称")
    description: str = Field(default="", description="场景描述")
    input_prompt: str = Field(description="输入提示词")
    expected_output: Optional[str] = Field(default=None, description="期望输出")
    expected_keywords: List[str] = Field(default_factory=list, description="期望关键词")
    metadata: dict = Field(default_factory=dict, description="附加元数据")
    weight: float = Field(default=1.0, ge=0.0, description="场景权重")


class CreateScenarioSetRequest(BaseModel):
    """创建场景集请求。"""
    name: str = Field(description="场景集名称")
    tenant_id: str = Field(description="租户标识")
    description: str = Field(default="", description="场景集描述")
    scenarios: List[ScenarioItem] = Field(default_factory=list, description="场景条目")


class ScenarioSetResponse(BaseModel):
    """场景集响应。"""
    set_id: str
    name: str
    description: str
    tenant_id: str
    scenario_count: int
    created_at: str


class StartEvaluationRequest(BaseModel):
    """启动评估请求。"""
    scenario_set_id: str = Field(description="场景集标识")
    tenant_id: str = Field(description="租户标识")
    evaluation_type: str = Field(default="baseline", description="评估类型：baseline/regression")
    baseline_id: Optional[str] = Field(default=None, description="回归对比基线标识")


class EvaluationTaskResponse(BaseModel):
    """评估任务响应。"""
    task_id: str
    scenario_set_id: str
    tenant_id: str
    evaluation_type: str
    state: str
    progress_percentage: float
    passed_scenarios: int
    failed_scenarios: int
    total_scenarios: int
    degraded: bool = Field(default=False, description="是否退化（回归评估）")
    average_overall: float = Field(default=0.0, description="平均综合分")
    completed_at: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)


class CreateBaselineRequest(BaseModel):
    """创建基线请求（从已完成基线评估任务冻结）。"""
    task_id: str = Field(description="已完成的基线评估任务标识")
    description: str = Field(default="", description="基线描述")


class BaselineResponse(BaseModel):
    """基线响应。"""
    baseline_id: str
    scenario_set_id: str
    tenant_id: str
    description: str
    average_overall: float
    success_rate: float
    created_at: str


# ---------------------------------------------------------------------------
# 场景集端点
# ---------------------------------------------------------------------------

@router.post("/governance/evaluation/scenario-sets", response_model=ScenarioSetResponse)
async def create_scenario_set(request: CreateScenarioSetRequest):
    """创建评估场景集。"""
    try:
        scenarios = [
            EvaluationScenario(
                scenario_id="",
                scenario_set_id="",
                name=item.name,
                description=item.description,
                input_prompt=item.input_prompt,
                expected_output=item.expected_output,
                expected_keywords=item.expected_keywords,
                metadata=item.metadata,
                weight=item.weight,
            )
            for item in request.scenarios
        ]
        scenario_set = await _scenario_set_service.create_scenario_set(
            name=request.name,
            tenant_id=request.tenant_id,
            description=request.description,
            scenarios=scenarios,
        )
        return _to_scenario_set_response(scenario_set)
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())


@router.get("/governance/evaluation/scenario-sets", response_model=List[ScenarioSetResponse])
async def list_scenario_sets(tenant_id: str = Query(..., description="租户标识")):
    """列出租户全部场景集。"""
    sets = await _scenario_set_service.list_scenario_sets(tenant_id)
    return [_to_scenario_set_response(s) for s in sets]


@router.get("/governance/evaluation/scenario-sets/{set_id}", response_model=ScenarioSetResponse)
async def get_scenario_set(set_id: str):
    """查询场景集详情。"""
    try:
        scenario_set = await _scenario_set_service.get_scenario_set(set_id)
        return _to_scenario_set_response(scenario_set)
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict())


@router.delete("/governance/evaluation/scenario-sets/{set_id}")
async def delete_scenario_set(set_id: str, tenant_id: str = Query(...)):
    """删除场景集。"""
    success = await _scenario_set_service.delete_scenario_set(set_id, tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail={"error_message": "场景集不存在"})
    return {"success": True}


# ---------------------------------------------------------------------------
# 评估任务端点
# ---------------------------------------------------------------------------

@router.post("/governance/evaluation/tasks", response_model=EvaluationTaskResponse)
async def start_evaluation(request: StartEvaluationRequest):
    """启动评估任务。"""
    try:
        eval_type = EvaluationType(request.evaluation_type)
    except ValueError:
        raise HTTPException(status_code=422, detail={
            "error_code": "EVAL_SCENARIO_INVALID",
            "error_message": f"未知评估类型: {request.evaluation_type}",
        })

    try:
        task = await _evaluation_runner.start_evaluation(
            scenario_set_id=request.scenario_set_id,
            tenant_id=request.tenant_id,
            evaluation_type=eval_type,
            baseline_id=request.baseline_id,
        )
        return _to_task_response(task)
    except DomainError as exc:
        status_code = 404 if exc.code == "EVAL_BASELINE_MISSING" else 422
        raise HTTPException(status_code=status_code, detail=exc.to_dict())


@router.get("/governance/evaluation/tasks", response_model=List[EvaluationTaskResponse])
async def list_tasks(
    tenant_id: str = Query(..., description="租户标识"),
    scenario_set_id: Optional[str] = Query(None, description="场景集过滤"),
):
    """列出评估任务。"""
    tasks = await _evaluation_runner.list_tasks(tenant_id, scenario_set_id)
    return [_to_task_response(t) for t in tasks]


@router.get("/governance/evaluation/tasks/{task_id}", response_model=EvaluationTaskResponse)
async def get_task(task_id: str):
    """查询评估任务详情。"""
    task = await _evaluation_runner.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail={"error_message": "任务不存在"})
    return _to_task_response(task)


@router.get("/governance/evaluation/tasks/{task_id}/report")
async def get_task_report(task_id: str):
    """查询评估任务完整报告（含轨迹评分与回归对比）。"""
    task = await _evaluation_runner.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail={"error_message": "任务不存在"})
    if task.report is None:
        raise HTTPException(status_code=409, detail={"error_message": "任务尚未产出报告"})
    return task.report.model_dump(mode="json")


# ---------------------------------------------------------------------------
# 基线端点
# ---------------------------------------------------------------------------

@router.post("/governance/evaluation/baselines", response_model=BaselineResponse)
async def create_baseline(request: CreateBaselineRequest):
    """从已完成的基线评估任务冻结基线。"""
    task = await _evaluation_runner.get_task(request.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail={"error_message": "任务不存在"})
    try:
        baseline = await _evaluation_runner.freeze_baseline(task, request.description)
        return BaselineResponse(
            baseline_id=baseline.baseline_id,
            scenario_set_id=baseline.scenario_set_id,
            tenant_id=baseline.tenant_id,
            description=baseline.description,
            average_overall=baseline.metrics.average_clear_score.overall,
            success_rate=baseline.metrics.success_rate,
            created_at=baseline.created_at.isoformat(),
        )
    except DomainError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict())


@router.get("/governance/evaluation/baselines", response_model=List[BaselineResponse])
async def list_baselines(
    tenant_id: str = Query(..., description="租户标识"),
    scenario_set_id: Optional[str] = Query(None, description="场景集过滤"),
):
    """列出基线记录。"""
    baselines = await _baseline_store.list_baselines(tenant_id, scenario_set_id)
    return [
        BaselineResponse(
            baseline_id=b.baseline_id,
            scenario_set_id=b.scenario_set_id,
            tenant_id=b.tenant_id,
            description=b.description,
            average_overall=b.metrics.average_clear_score.overall,
            success_rate=b.metrics.success_rate,
            created_at=b.created_at.isoformat(),
        )
        for b in baselines
    ]


# ---------------------------------------------------------------------------
# 状态端点
# ---------------------------------------------------------------------------

@router.get("/governance/evaluation/status")
async def evaluation_status():
    """评估框架状态。"""
    settings = get_settings()
    return {
        "evaluation_enabled": settings.evaluation_enabled,
        "llm_scoring_enabled": settings.evaluation_llm_scoring_enabled,
        "random_seed": settings.evaluation_random_seed,
        "regression_degrade_threshold": settings.evaluation_regression_degrade_threshold,
        "max_scenarios": settings.evaluation_max_scenarios,
    }


# ---------------------------------------------------------------------------
# 响应构造工具
# ---------------------------------------------------------------------------

def _to_scenario_set_response(scenario_set: EvaluationScenarioSet) -> ScenarioSetResponse:
    """构造场景集响应。"""
    return ScenarioSetResponse(
        set_id=scenario_set.set_id,
        name=scenario_set.name,
        description=scenario_set.description,
        tenant_id=scenario_set.tenant_id,
        scenario_count=len(scenario_set.scenarios),
        created_at=scenario_set.created_at.isoformat(),
    )


def _to_task_response(task: Any) -> EvaluationTaskResponse:
    """构造评估任务响应。"""
    degraded = False
    average_overall = 0.0
    if task.report is not None:
        average_overall = task.report.metrics.average_clear_score.overall
        if task.report.regression_comparison is not None:
            degraded = task.report.regression_comparison.degraded

    return EvaluationTaskResponse(
        task_id=task.task_id,
        scenario_set_id=task.scenario_set_id,
        tenant_id=task.tenant_id,
        evaluation_type=task.evaluation_type.value,
        state=task.state.value,
        progress_percentage=task.progress.percentage,
        passed_scenarios=task.progress.completed_scenarios,
        failed_scenarios=task.progress.failed_scenarios,
        total_scenarios=task.progress.total_scenarios,
        degraded=degraded,
        average_overall=average_overall,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
        error_message=task.error_message,
    )
