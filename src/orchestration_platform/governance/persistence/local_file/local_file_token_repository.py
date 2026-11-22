# TODO(tech-debt): B档候选孤儿（族群 B2），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 本地文件令牌仓储（governance/persistence/local_file/local_file_token_repository.py）
#
# 职责：以 JSON 文件持久化访问令牌。
# 设计意图（对应 spec 5.1、design 2.4.2、任务 T55.3）：
#   1. 目录结构：{root}/governance/tokens/{token_id}.json
#   2. 哈希索引：{root}/governance/tokens/_hash_index.json（token_hash -> token_id 映射）
#   3. 令牌哈希存储，不保存原文。
# =============================================================================

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import TokenStatus
from orchestration_platform.governance.tenant.models import AccessToken


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LocalFileTokenRepository:
    """令牌仓储——本地文件实现。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        root = storage_root or get_settings().local_storage_root
        self._base_dir = Path(root) / "governance" / "tokens"

    def _token_file(self, token_id: str) -> Path:
        """返回令牌文件路径。"""
        return self._base_dir / f"{token_id}.json"

    def _hash_index_file(self) -> Path:
        """返回哈希索引文件路径。"""
        return self._base_dir / "_hash_index.json"

    def _load_hash_index(self) -> Dict[str, str]:
        """加载哈希索引。"""
        file_path = self._hash_index_file()
        if not file_path.exists():
            return {}
        return json.loads(file_path.read_text(encoding="utf-8"))

    def _save_hash_index(self, index: Dict[str, str]) -> None:
        """保存哈希索引。"""
        file_path = self._hash_index_file()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    async def save_token(self, token: AccessToken) -> AccessToken:
        """保存令牌记录。"""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._token_file(token.token_id)
        file_path.write_text(token.model_dump_json(indent=2), encoding="utf-8")

        # 更新哈希索引
        index = self._load_hash_index()
        index[token.token_hash] = token.token_id
        self._save_hash_index(index)
        return token

    async def get_token(
        self, tenant_id: str, token_id: str
    ) -> Optional[AccessToken]:
        """按标识查询令牌。"""
        file_path = self._token_file(token_id)
        if not file_path.exists():
            return None
        token = AccessToken.model_validate_json(file_path.read_text(encoding="utf-8"))
        if token.tenant_id != tenant_id:
            return None
        return token

    async def get_by_hash(
        self, token_hash: str
    ) -> Optional[AccessToken]:
        """按令牌哈希查询令牌。"""
        index = self._load_hash_index()
        token_id = index.get(token_hash)
        if token_id is None:
            return None
        file_path = self._token_file(token_id)
        if not file_path.exists():
            return None
        return AccessToken.model_validate_json(file_path.read_text(encoding="utf-8"))

    async def is_revoked(self, token_id: str) -> bool:
        """判断令牌是否已吊销。"""
        file_path = self._token_file(token_id)
        if not file_path.exists():
            return True
        token = AccessToken.model_validate_json(file_path.read_text(encoding="utf-8"))
        return token.status == TokenStatus.REVOKED

    async def revoke_token(self, token_id: str) -> None:
        """吊销令牌。"""
        file_path = self._token_file(token_id)
        if not file_path.exists():
            return
        token = AccessToken.model_validate_json(file_path.read_text(encoding="utf-8"))
        token.status = TokenStatus.REVOKED
        token.revoked_at = _utc_now()
        file_path.write_text(token.model_dump_json(indent=2), encoding="utf-8")

        # 从哈希索引移除
        index = self._load_hash_index()
        index.pop(token.token_hash, None)
        self._save_hash_index(index)

    async def revoke_for_user(
        self, tenant_id: str, user_id: str
    ) -> int:
        """撤销用户的所有活跃令牌，返回撤销数量。"""
        if not self._base_dir.exists():
            return 0
        count = 0
        index = self._load_hash_index()
        for file_path in self._base_dir.glob("*.json"):
            if file_path.name == "_hash_index.json":
                continue
            token = AccessToken.model_validate_json(file_path.read_text(encoding="utf-8"))
            if (
                token.tenant_id == tenant_id
                and token.user_id == user_id
                and token.status == TokenStatus.ACTIVE
            ):
                token.status = TokenStatus.REVOKED
                token.revoked_at = _utc_now()
                file_path.write_text(token.model_dump_json(indent=2), encoding="utf-8")
                index.pop(token.token_hash, None)
                count += 1
        self._save_hash_index(index)
        return count

    async def list_tokens_for_user(
        self, tenant_id: str, user_id: str
    ) -> List[AccessToken]:
        """列出用户的全部令牌。"""
        if not self._base_dir.exists():
            return []
        tokens: List[AccessToken] = []
        for file_path in self._base_dir.glob("*.json"):
            if file_path.name == "_hash_index.json":
                continue
            token = AccessToken.model_validate_json(file_path.read_text(encoding="utf-8"))
            if token.tenant_id == tenant_id and token.user_id == user_id:
                tokens.append(token)
        tokens.sort(key=lambda t: t.issued_at, reverse=True)
        return tokens

    async def update_last_used(
        self, token_id: str
    ) -> None:
        """更新令牌最近使用时间。"""
        file_path = self._token_file(token_id)
        if not file_path.exists():
            return
        token = AccessToken.model_validate_json(file_path.read_text(encoding="utf-8"))
        token.last_used_at = _utc_now()
        file_path.write_text(token.model_dump_json(indent=2), encoding="utf-8")
