# =============================================================================
# Agent 绩效聚合查询路由（api/routers/governance/agent_performance_router.py）
#
# 职责：暴露 Agent 维度绩效四维聚合查询 HTTP 接口（第五阶段 G3/T229）。
# 设计意图（对应 spec 5.3.3、design D5）：
#   1. GET /api/v1/governance/agent-performance：agent_id + 时间范围入参，
#      一次聚合调用返回完整结果（禁止前端逐条拉取拼装，4.1.3）；
#   2. 参数校验：起始晚于结束/格式非法 → 422 PERF_TIME_RANGE_INVALID；
#      agent_id 缺失 → 422（A3 异常场景 2）；
#   3. 只读查询无副作用；
#   4. 路由仅在 settings.agent_performance_enabled=True 时注册（关闭时 404）。
# =============================================================================

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from orchestration_platform.observability.agent_performance.abstractions import (
    IPerformanceRepository,
)

router = APIRouter(tags=["Agent绩效聚合"])

# 仓储单例（由 application_factory 注入）
_repository: Optional[IPerformanceRepository] = None


def set_performance_repository(repository: IPerformanceRepository) -> None:
    """注入绩效仓储实例（由 application_factory 调用）。"""
    global _repository
    _repository = repository


def _get_repository() -> IPerformanceRepository:
    if _repository is None:
        raise HTTPException(status_code=503, detail="绩效仓储未初始化")
    return _repository


@router.get("/governance/agent-performance")
async def get_agent_performance(
    agent_id: str = Query(description="Agent 标识（必填）"),
    start: datetime = Query(description="统计起始时间（ISO8601，UTC，含）"),
    end: datetime = Query(description="统计结束时间（ISO8601，UTC，含）"),
) -> dict:
    """查询 Agent 绩效四维聚合（A3.1/A3.2）。

    一次聚合调用返回完整结果：success_rate/avg_duration_ms/total_tokens/
    avg_quality_score/sample_count/data_source。
    """
    # agent_id 缺失校验（FastAPI Query 必填已拦截空缺，此处防御空串）
    if not agent_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "PERF_TIME_RANGE_INVALID", "message": "agent_id 缺失"},
        )
    # 时间范围校验：起始晚于结束 → 422 PERF_TIME_RANGE_INVALID（A3 异常场景 2）
    if start > end:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PERF_TIME_RANGE_INVALID",
                "message": f"时间范围非法：起始 {start.isoformat()} 晚于结束 {end.isoformat()}",
            },
        )
    repository = _get_repository()
    aggregate = await repository.aggregate(agent_id, start, end)
    # 只读查询无副作用：直接返回聚合模型字段
    return aggregate.model_dump(mode="json")
