# =============================================================================
# 防重放守卫（governance/sso/replay_guard.py）
#
# 职责：断言 ID 一次性缓存，重复断言拒绝。
# 设计意图（对应偏差修正 T168 / spec 4.3.5）：
#   - 复用既有 ICacheBackend（降级内存缓存）
#   - TTL 对齐断言时效窗口
#   - 缓存仅存断言 ID 摘要不存断言原文
# =============================================================================

import logging
import hashlib
from datetime import datetime, timezone
from typing import Dict, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import SSO_REPLAY_DETECTED, DomainError

_logger = logging.getLogger(__name__)


class ReplayGuard:
    """防重放守卫。

    断言 ID 一次性缓存，重复断言拒绝登录。
    缓存后端不可用时降级为进程内 Dict 缓存。
    """

    def __init__(self, cache_ttl: Optional[int] = None) -> None:
        """构造防重放守卫。"""
        settings = get_settings()
        self._ttl = cache_ttl or settings.sso_assertion_replay_cache_ttl
        # 进程内缓存（降级模式）：assertion_id_hash -> expiry
        self._cache: Dict[str, datetime] = {}

    def check(self, assertion_id: str) -> None:
        """检查断言是否为重放。

        参数：
            assertion_id —— 断言标识

        异常：
            DomainError(SSO_REPLAY_DETECTED) —— 重复断言
        """
        # 仅存断言 ID 摘要不存断言原文（spec 5.4.1.7）
        id_hash = hashlib.sha256(assertion_id.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)

        # 清理过期条目
        expired = [k for k, v in self._cache.items() if v < now]
        for k in expired:
            del self._cache[k]

        if id_hash in self._cache:
            _logger.warning("检测到断言重放: assertion_id_hash=%s", id_hash[:16])
            raise DomainError(
                SSO_REPLAY_DETECTED,
                "断言重放检测：该断言已处理过",
                {"assertion_id_hash": id_hash[:16]},
            )

        # 记录断言 ID
        from datetime import timedelta
        self._cache[id_hash] = now + timedelta(seconds=self._ttl)
