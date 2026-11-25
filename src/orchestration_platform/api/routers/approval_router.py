# =============================================================================
# 审批队列路由（api/routers/approval_router.py）——B16 F4 审批运营面
#
# 端点：
#   GET  /api/v1/approvals             —— waiting_review 实例聚合查询
#                                         （进程内注册表 + 事件仓储读穿透，
#                                         ApprovalQueueService 双模式数据源）
#   POST /api/v1/approvals/batch-review —— 批量复核（逐项复用 submit_review，
#                                         双模式零分叉：注册表命中走进程内
#                                         决策服务，未命中走 worker 决策路径）
#
# 设计依据：.codeartsdoer/specs/roadmap_b16_operations_plane/design.md
#   - D2：deadline 展示口径（行级 review_timeout_seconds/deadline，超时提醒
#     为 UI 级，无自动动作）
#   - D3：租户作用域——RBAC 激活取 request.state.tenant_id（鉴权链注入，
#     优先于查询参数；同 start_instance T7.3 先例），治理降级/cron 场景
#     可选 tenant_id 查询参数
#   - D4：批量复核——instance_ids 1..100（超限 422）；逐项调
#     rollback_replay_router.submit_review；4xx 归 skipped（竞争 409/未知
#     404/校验 422）、5xx 归 error、成功归 ok；重复 instance_id 幂等去重
#     归 skipped；响应 {results, summary} 逐项可审计
# =============================================================================

from typing import List, Literal, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from orchestration_platform.application.approval_queue_service import (
    ApprovalQueueService,
)

router = APIRouter(tags=["审批队列"])


def _get_registry():
    """调用期解析注册表（模块导入环规避 + monkeypatch 语义保持）。"""
    from orchestration_platform.api.routers.execution_instance_router import (
        _instance_registry,
    )

    return _instance_registry


@router.get("/approvals")
async def list_approvals(
    request: Request,
    tenant_id: Optional[str] = None,
) -> dict:
    """审批队列：waiting_review 实例聚合查询（挂起时刻降序）。

    行字段：instance_id/definition_id/definition_version（非 None 随行）/
    tenant_id/status/checkpoint_id/suspended_at/review_timeout_seconds/
    deadline/timeout_action/review_instructions。
    """
    service = ApprovalQueueService(_get_registry())
    # 租户作用域：RBAC 注入优先（不可被查询参数覆盖），降级可选查询参数
    scope: Optional[str] = getattr(request.state, "tenant_id", None) or tenant_id
    rows = await service.list_waiting_review(tenant_id=scope)
    return {"approvals": rows}


class BatchReviewBody(BaseModel):
    """批量复核请求体（design D4）。"""

    instance_ids: List[str] = Field(
        min_length=1,
        max_length=100,
        description="待复核实例标识列表（1..100；重复项幂等去重归 skipped）",
    )
    decision: Literal["approve", "reject", "timeout_action"] = Field(
        description="审核决策（三值取值域，同 ReviewBody）"
    )
    actor: str = Field(description="决策人标识")
    comment: str = ""


@router.post("/approvals/batch-review")
async def batch_review(body: BatchReviewBody) -> dict:
    """批量复核：逐项复用 submit_review（双模式零分叉）。

    结果分类（design D4）：成功 ok；4xx（409 竞争/404 未知/422 校验）
    skipped；5xx error；重复 instance_id skipped(duplicate)。
    """
    from orchestration_platform.api.routers.rollback_replay_router import (
        ReviewBody,
        submit_review,
    )

    results: List[dict] = []
    summary = {"total": 0, "ok": 0, "skipped": 0, "error": 0}
    seen: set = set()
    for instance_id in body.instance_ids:
        summary["total"] += 1
        if instance_id in seen:
            results.append(
                {
                    "instance_id": instance_id,
                    "status": "skipped",
                    "error": "duplicate",
                }
            )
            summary["skipped"] += 1
            continue
        seen.add(instance_id)
        try:
            result = await submit_review(
                instance_id,
                ReviewBody(
                    decision=body.decision,
                    actor=body.actor,
                    comment=body.comment,
                ),
            )
            results.append(
                {"instance_id": instance_id, "status": "ok", "result": result}
            )
            summary["ok"] += 1
        except Exception as exc:  # noqa: BLE001 —— 逐项容错，单实例失败不阻断批
            status_code = getattr(exc, "status_code", None)
            if isinstance(status_code, int) and status_code >= 500:
                results.append(
                    {
                        "instance_id": instance_id,
                        "status": "error",
                        "http_status": status_code,
                        "error": str(getattr(exc, "detail", exc)),
                    }
                )
                summary["error"] += 1
            else:
                results.append(
                    {
                        "instance_id": instance_id,
                        "status": "skipped",
                        "http_status": status_code,
                        "error": str(getattr(exc, "detail", exc)),
                    }
                )
                summary["skipped"] += 1
    return {"results": results, "summary": summary}
