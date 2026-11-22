# =============================================================================
# 权限缓存（governance/rbac/permission_cache.py）
#
# 职责：缓存用户权限数据，减少重复查询。
# 设计意图（对应 spec 5.1、任务 T51.2）：
#   1. TTL 过期自动刷新；
#   2. 支持主动失效（角色变更、用户禁用）；
#   3. 缓存键为 (tenant_id, user_id)。
# =============================================================================

import time
from typing import Dict, List, Optional, Tuple

from orchestration_platform.governance.rbac.models import PermissionItem


class PermissionCache:
    """权限缓存。

    按 (tenant_id, user_id) 缓存权限项列表，支持 TTL 过期与主动失效。
    """

    def __init__(self, ttl_seconds: int = 300) -> None:
        """初始化权限缓存。

        Args:
            ttl_seconds: 缓存存活时间（秒）。
        """
        self._ttl = ttl_seconds
        self._cache: Dict[Tuple[str, str], Tuple[float, List[PermissionItem]]] = {}

    def get_permissions(
        self,
        tenant_id: str,
        user_id: str,
    ) -> Optional[List[PermissionItem]]:
        """获取缓存的权限项列表。

        Returns:
            权限项列表，缓存不存在或过期时返回 None。
        """
        key = (tenant_id, user_id)
        entry = self._cache.get(key)
        if entry is None:
            return None
        cached_at, permissions = entry
        if (time.monotonic() - cached_at) > self._ttl:
            del self._cache[key]
            return None
        return permissions

    def set_permissions(
        self,
        tenant_id: str,
        user_id: str,
        permissions: List[PermissionItem],
    ) -> None:
        """存入权限项缓存。"""
        self._cache[(tenant_id, user_id)] = (time.monotonic(), permissions)

    def invalidate(self, tenant_id: str, user_id: str) -> bool:
        """使指定用户的权限缓存失效。"""
        return self._cache.pop((tenant_id, user_id), None) is not None

    def invalidate_tenant(self, tenant_id: str) -> int:
        """使指定租户的全部权限缓存失效。

        Returns:
            清除的缓存条数。
        """
        keys_to_remove = [
            key for key in self._cache
            if key[0] == tenant_id
        ]
        for key in keys_to_remove:
            del self._cache[key]
        return len(keys_to_remove)

    def clear(self) -> int:
        """清空全部缓存。"""
        count = len(self._cache)
        self._cache.clear()
        return count

    @property
    def size(self) -> int:
        """当前缓存条数。"""
        return len(self._cache)
