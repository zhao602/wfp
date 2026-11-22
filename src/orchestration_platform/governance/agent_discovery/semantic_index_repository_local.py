# =============================================================================
# 本地语义索引仓储（governance/agent_discovery/semantic_index_repository_local.py）
#
# 职责：本地内存实现的语义索引仓储——维护 Agent 语义档案字典。
# 设计意图（对应偏差修正 T194）：
#   - 进程内内存存储，适用于单实例部署与测试
#   - 持久化后端可由配置切换，本实现为零依赖默认实现
#   - upsert 幂等：相同 agent_id 覆盖，updated_at 刷新
# =============================================================================

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from orchestration_platform.governance.agent_discovery.abstractions.semantic_index_repository import (
    ISemanticIndexRepository,
)
from orchestration_platform.governance.agent_discovery.models import (
    AgentSemanticProfile,
)

_logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """获取当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class LocalSemanticIndexRepository:
    """本地内存语义索引仓储。

    实现 ISemanticIndexRepository 协议（结构化子类型）。
    线程安全由调用方保证（单线程服务或外部加锁）。
    """

    def __init__(self) -> None:
        """构造空仓储。"""
        self._profiles: Dict[str, AgentSemanticProfile] = {}

    def upsert_profile(self, profile: AgentSemanticProfile) -> None:
        """新增或更新 Agent 语义档案。

        相同 agent_id 覆盖；updated_at 刷新为当前时间。
        """
        updated = profile.model_copy(update={"updated_at": _utc_now()})
        self._profiles[profile.agent_id] = updated
        _logger.debug(
            "语义档案 upsert: agent_id=%s source=%s",
            profile.agent_id,
            profile.description_source.value,
        )

    def get_profile(self, agent_id: str) -> Optional[AgentSemanticProfile]:
        """按 agent_id 查询语义档案，不存在返回 None。"""
        return self._profiles.get(agent_id)

    def remove_profile(self, agent_id: str) -> bool:
        """移除 Agent 语义档案，返回是否实际移除。"""
        if agent_id in self._profiles:
            del self._profiles[agent_id]
            _logger.debug("语义档案移除: agent_id=%s", agent_id)
            return True
        return False

    def list_profiles(self) -> List[AgentSemanticProfile]:
        """列出全部 Agent 语义档案。"""
        return list(self._profiles.values())

    def count(self) -> int:
        """返回档案总数。"""
        return len(self._profiles)


# 静态类型检查确认实现满足协议
_: ISemanticIndexRepository = LocalSemanticIndexRepository()  # type: ignore[assignment]
