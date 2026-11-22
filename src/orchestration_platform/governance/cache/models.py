# =============================================================================
# 缓存领域模型（governance/cache/models.py）
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CacheKey(BaseModel):
    """缓存键。"""
    namespace: str = Field(description="命名空间（区分权限/会话/热点）")
    key: str = Field(description="键名")
    full_key: str = Field(description="完整键（namespace:key）")


class LockHolder(BaseModel):
    """锁持有者信息。"""
    lock_key: str = Field(description="锁键")
    holder_id: str = Field(description="持有者标识")
    acquired_at: datetime = Field(default_factory=_utc_now, description="获取时间")
    ttl_seconds: int = Field(description="过期时间（秒）")


class RateLimitResult(BaseModel):
    """限流计数结果。"""
    allowed: bool = Field(description="是否允许")
    current_count: int = Field(description="当前计数")
    threshold: int = Field(description="阈值")
    reset_at: Optional[datetime] = Field(default=None, description="窗口重置时间")
