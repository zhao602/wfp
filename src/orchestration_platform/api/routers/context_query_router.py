# =============================================================================
# 上下文查询路由（api/routers/context_query_router.py）
#
# 职责：提供上下文检查点查询的 REST API 端点。
# 设计意图（对应 spec 4.6、任务 T41.3）：
#   1. 查询实例的上下文检查点列表与详情；
#   2. 错误码 INSTANCE_NOT_FOUND / CONTEXT_CHECKPOINT_CORRUPTED。
# =============================================================================

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["上下文查询"])


@router.get("/instances/{instance_id}/context")
async def get_context_checkpoints(instance_id: str) -> list:
    """查询实例的上下文检查点列表。"""
    return []


@router.get("/instances/{instance_id}/context/{checkpoint_id}")
async def get_context_checkpoint_detail(instance_id: str, checkpoint_id: str) -> dict:
    """查询上下文检查点详情。"""
    return {"instance_id": instance_id, "checkpoint_id": checkpoint_id, "context": {}}
