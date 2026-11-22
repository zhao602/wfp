# =============================================================================
# 令牌管理器（governance/tenant/token_manager.py）
#
# 职责：管理访问令牌的签发、校验与撤销。
# 设计意图（对应 spec 5.1、任务 T54）：
#   1. 令牌绑定用户，有有效期；
#   2. 禁用用户时令牌立即失效；
#   3. 令牌哈希存储，不保存原文。
# =============================================================================

import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from orchestration_platform.domain.models.enums import TokenStatus
from orchestration_platform.governance.tenant.models import AccessToken


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TokenManager:
    """访问令牌管理器。"""

    def __init__(self, default_ttl_seconds: int = 3600) -> None:
        self._ttl = default_ttl_seconds
        self._tokens: Dict[str, AccessToken] = {}
        self._hash_index: Dict[str, str] = {}

    async def issue_token(
        self,
        user_id: str,
        tenant_id: str,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        """签发访问令牌。

        返回令牌原文（仅此一次），内部仅存储哈希。
        令牌格式: "tenant_id:user_id:expires_timestamp:random_nonce"
        """
        ttl = ttl_seconds or self._ttl
        expires_at_ts = int(time.time()) + ttl
        nonce = uuid.uuid4().hex
        raw_token = f"{tenant_id}:{user_id}:{expires_at_ts}:{nonce}"

        token_id = f"tok_{uuid.uuid4().hex[:12]}"
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = datetime.fromtimestamp(expires_at_ts, tz=timezone.utc)

        token = AccessToken(
            token_id=token_id,
            user_id=user_id,
            tenant_id=tenant_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self._tokens[token_id] = token
        self._hash_index[token_hash] = token_id
        return raw_token

    def validate_token(self, raw_token: str) -> Optional[AccessToken]:
        """校验令牌有效性。

        Returns:
            令牌记录（有效时），None（无效或过期时）。
        """
        try:
            parts = raw_token.split(":")
            if len(parts) < 4:
                return None
            expires_at_ts = int(parts[2])
            if time.time() > expires_at_ts:
                return None

            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            token_id = self._hash_index.get(token_hash)
            if token_id is None:
                return None

            token = self._tokens[token_id]
            if token.status != TokenStatus.ACTIVE:
                return None

            token.last_used_at = _utc_now()
            return token
        except Exception:
            return None

    async def revoke_token(self, token_id: str) -> None:
        """撤销令牌。"""
        token = self._tokens.get(token_id)
        if token:
            token.status = TokenStatus.REVOKED
            token.revoked_at = _utc_now()
            self._hash_index.pop(token.token_hash, None)

    async def revoke_for_user(self, user_id: str) -> int:
        """撤销用户的所有令牌。

        用户禁用时调用。
        """
        count = 0
        for token in self._tokens.values():
            if token.user_id == user_id and token.status == TokenStatus.ACTIVE:
                token.status = TokenStatus.REVOKED
                token.revoked_at = _utc_now()
                self._hash_index.pop(token.token_hash, None)
                count += 1
        return count
