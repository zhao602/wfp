# =============================================================================
# Agent 发现领域模型（governance/agent_discovery/models.py）
#
# 职责：定义 Agent 语义档案、发现查询、调用上下文、索引命中与发现结果等领域模型。
# 设计意图（对应偏差修正 T193 / design 2.3.2）：
#   - AgentSemanticProfile：Agent 语义档案（能力描述 + 标签 + 描述来源三态）
#   - DiscoveryQuery：发现查询（自然语言意图 + 上下文约束）
#   - CallerContext：调用方上下文（用于 RBAC 过滤）
#   - IndexHit：单路索引命中（agent_id + score + 命中证据）
#   - DiscoveryResultItem / DiscoveryResult：发现结果项与汇总
# =============================================================================

from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import DescriptionSource


def _utc_now() -> datetime:
    """获取当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class AgentSemanticProfile(BaseModel):
    """Agent 语义档案。

    描述 Agent 的能力语义，供语义索引检索。
    描述来源三态：
      - PROVIDER：注册方提供（原始描述）
      - LLM_GENERATED：LLM 辅助生成（自动增强）
      - MANUAL_REVISION：人工修订（最高优先级，覆盖前两者）
    """

    agent_id: str = Field(description="Agent 标识（全局唯一）")
    name: str = Field(description="Agent 显示名称")
    description: str = Field(default="", description="能力描述文本")
    description_source: DescriptionSource = Field(
        default=DescriptionSource.PROVIDER,
        description="描述来源三态",
    )
    tags: List[str] = Field(
        default_factory=list, description="能力标签（用于关键词索引）"
    )
    capabilities: List[str] = Field(
        default_factory=list, description="能力清单（结构化）"
    )
    required_permissions: List[str] = Field(
        default_factory=list, description="访问所需权限标识（RBAC 过滤依据）"
    )
    tenant_id: str = Field(default="", description="所属租户标识")
    metadata: Dict[str, str] = Field(
        default_factory=dict, description="扩展元数据"
    )
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")


class CallerContext(BaseModel):
    """调用方上下文。

    用于 RBAC 过滤：仅返回调用方具备所需权限的 Agent。
    """

    user_id: str = Field(description="调用方用户标识")
    tenant_id: str = Field(default="", description="调用方租户标识")
    roles: List[str] = Field(
        default_factory=list, description="调用方角色清单"
    )
    permissions: List[str] = Field(
        default_factory=list, description="调用方权限清单（用于 RBAC 过滤）"
    )


class DiscoveryQuery(BaseModel):
    """发现查询。

    自然语言意图驱动的 Agent 发现查询。
    """

    intent_text: str = Field(description="自然语言意图文本")
    caller: CallerContext = Field(description="调用方上下文")
    tags_filter: List[str] = Field(
        default_factory=list, description="标签过滤（AND 语义，命中全部标签）"
    )
    limit: Optional[int] = Field(
        default=None, description="结果数量上限（None 使用配置默认）"
    )


class IndexHit(BaseModel):
    """单路索引命中。

    由关键词索引或向量索引产生，供 RRF 融合。
    """

    agent_id: str = Field(description="命中的 Agent 标识")
    score: float = Field(description="命中分数（越高越相关）")
    evidence: str = Field(default="", description="命中证据（命中的词或片段）")


class DiscoveryResultItem(BaseModel):
    """发现结果项。

    RRF 融合后的最终结果项。
    """

    agent_id: str = Field(description="Agent 标识")
    name: str = Field(description="Agent 显示名称")
    description: str = Field(default="", description="能力描述")
    description_source: DescriptionSource = Field(
        default=DescriptionSource.PROVIDER,
        description="描述来源三态",
    )
    tags: List[str] = Field(default_factory=list, description="能力标签")
    capabilities: List[str] = Field(
        default_factory=list, description="能力清单"
    )
    relevance_score: float = Field(description="融合相关度分数（RRF）")
    matched_evidence: List[str] = Field(
        default_factory=list, description="命中证据清单"
    )


class DiscoveryResult(BaseModel):
    """发现结果汇总。"""

    items: List[DiscoveryResultItem] = Field(
        default_factory=list, description="发现结果项清单（按相关度降序）"
    )
    total: int = Field(default=0, description="结果总数")
    query: str = Field(description="原始查询文本")
    backend: str = Field(
        default="keyword", description="实际使用的检索后端（keyword/hybrid）"
    )
