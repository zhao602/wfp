# =============================================================================
# Redis 缓存管理路由（api/routers/governance/cache_router.py）
# =============================================================================

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

router = APIRouter(tags=["缓存管理"])


class CacheStatusResponse(BaseModel):
    backend: str = Field(description="后端类型：redis/in_memory")
    is_degraded: bool = Field(description="是否降级")


_cache_manager = None


def _get_cache_manager():
    global _cache_manager
    if _cache_manager is not None:
        return _cache_manager
    from orchestration_platform.governance.cache.cache_manager import CacheManager
    _cache_manager = CacheManager()
    return _cache_manager


@router.get("/governance/cache/status")
async def cache_status() -> CacheStatusResponse:
    """查询缓存后端状态。"""
    mgr = _get_cache_manager()
    return CacheStatusResponse(
        backend="in_memory" if mgr.is_degraded else "redis",
        is_degraded=mgr.is_degraded,
    )


@router.get("/governance/cache/health")
async def cache_health() -> dict:
    """缓存健康检查。"""
    mgr = _get_cache_manager()
    return {"healthy": True, "degraded": mgr.is_degraded}
