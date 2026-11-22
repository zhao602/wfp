# =============================================================================
# 语义索引仓储抽象（governance/agent_discovery/abstractions/semantic_index_repository.py）
#
# 职责：定义语义索引仓储契约——索引重建、Agent 语义档案增删改查。
# 设计意图（对应偏差修正 T194）：
#   - 抽象与实现分离，便于切换本地内存 / 持久化 / 远程索引后端
#   - Protocol + runtime_checkable 允许结构化子类型校验
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.governance.agent_discovery.models import (
    AgentSemanticProfile,
)


@runtime_checkable
class ISemanticIndexRepository(Protocol):
    """语义索引仓储抽象。

    实现方负责维护 Agent 语义档案的存储与索引构建。
    本地实现见 semantic_index_repository_local.LocalSemanticIndexRepository。
    """

    def upsert_profile(self, profile: AgentSemanticProfile) -> None:
        """新增或更新 Agent 语义档案。

        相同 agent_id 的档案将被覆盖；描述来源按三态独立维护。
        """
        ...

    def get_profile(self, agent_id: str) -> Optional[AgentSemanticProfile]:
        """按 agent_id 查询语义档案，不存在返回 None。"""
        ...

    def remove_profile(self, agent_id: str) -> bool:
        """移除 Agent 语义档案，返回是否实际移除。"""
        ...

    def list_profiles(self) -> List[AgentSemanticProfile]:
        """列出全部 Agent 语义档案（用于索引重建）。"""
        ...
