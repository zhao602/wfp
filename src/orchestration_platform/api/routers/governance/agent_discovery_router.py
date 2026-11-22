# =============================================================================
# Agent 发现 REST API（api/routers/governance/agent_discovery_router.py）
#
# 职责：暴露 Agent 语义档案管理与发现查询的 HTTP 接口。
# 设计意图（对应偏差修正 T200-T201）：
#   - 档案管理：upsert / get / remove / list
#   - 发现查询：POST /api/v1/governance/agent-discovery/discover
#   - 路由仅在 settings.agent_discovery_enabled=True 时由应用工厂注册
# =============================================================================
"""

路由清单：
  POST   /governance/agent-discovery/profiles           新增/更新语义档案
  GET    /governance/agent-discovery/profiles/{agent_id} 查询单个语义档案
  DELETE /governance/agent-discovery/profiles/{agent_id} 移除语义档案
  GET    /governance/agent-discovery/profiles            列出全部语义档案
  POST   /governance/agent-discovery/discover            执行发现查询
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orchestration_platform.domain.errors import DomainError
from orchestration_platform.domain.models.enums import DescriptionSource
from orchestration_platform.governance.agent_discovery.discovery_service import (
    DiscoveryService,
)
from orchestration_platform.governance.agent_discovery.models import (
    AgentSemanticProfile,
    CallerContext,
    DiscoveryQuery,
    DiscoveryResult,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["语义索引 Agent 发现"])

# 单例服务实例（路由级共享）
_service = DiscoveryService()


# ---------------- 请求/响应模型 ----------------


class CallerContextModel(BaseModel):
    """调用方上下文请求模型。"""

    user_id: str = Field(description="调用方用户标识")
    tenant_id: str = Field(default="", description="调用方租户标识")
    roles: List[str] = Field(default_factory=list, description="调用方角色清单")
    permissions: List[str] = Field(
        default_factory=list, description="调用方权限清单"
    )


class DiscoveryRequest(BaseModel):
    """发现查询请求。"""

    intent_text: str = Field(description="自然语言意图文本")
    caller: CallerContextModel = Field(description="调用方上下文")
    tags_filter: List[str] = Field(
        default_factory=list, description="标签过滤（AND 语义）"
    )
    limit: Optional[int] = Field(default=None, description="结果数量上限")


class ProfileUpsertRequest(BaseModel):
    """语义档案新增/更新请求。"""

    agent_id: str = Field(description="Agent 标识")
    name: str = Field(description="Agent 显示名称")
    description: str = Field(default="", description="能力描述文本")
    description_source: str = Field(
        default="provider", description="描述来源三态"
    )
    tags: List[str] = Field(default_factory=list, description="能力标签")
    capabilities: List[str] = Field(
        default_factory=list, description="能力清单"
    )
    required_permissions: List[str] = Field(
        default_factory=list, description="访问所需权限标识"
    )
    tenant_id: str = Field(default="", description="所属租户标识")
    metadata: dict = Field(default_factory=dict, description="扩展元数据")


class ProfileResponse(BaseModel):
    """语义档案响应。"""

    agent_id: str = Field(description="Agent 标识")
    name: str = Field(description="Agent 显示名称")
    description: str = Field(default="", description="能力描述文本")
    description_source: str = Field(description="描述来源三态")
    tags: List[str] = Field(default_factory=list, description="能力标签")
    capabilities: List[str] = Field(
        default_factory=list, description="能力清单"
    )
    required_permissions: List[str] = Field(
        default_factory=list, description="访问所需权限标识"
    )
    tenant_id: str = Field(default="", description="所属租户标识")
    metadata: dict = Field(default_factory=dict, description="扩展元数据")


def _profile_to_response(profile: AgentSemanticProfile) -> ProfileResponse:
    """语义档案转响应模型。"""
    return ProfileResponse(
        agent_id=profile.agent_id,
        name=profile.name,
        description=profile.description,
        description_source=profile.description_source.value,
        tags=profile.tags,
        capabilities=profile.capabilities,
        required_permissions=profile.required_permissions,
        tenant_id=profile.tenant_id,
        metadata=profile.metadata,
    )


# ---------------- 路由 ----------------


@router.post(
    "/governance/agent-discovery/profiles",
    response_model=ProfileResponse,
)
async def upsert_profile(request: ProfileUpsertRequest) -> ProfileResponse:
    """新增或更新 Agent 语义档案。"""
    try:
        source = DescriptionSource(request.description_source)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"非法描述来源: {request.description_source}",
        )

    profile = AgentSemanticProfile(
        agent_id=request.agent_id,
        name=request.name,
        description=request.description,
        description_source=source,
        tags=request.tags,
        capabilities=request.capabilities,
        required_permissions=request.required_permissions,
        tenant_id=request.tenant_id,
        metadata=request.metadata,
    )
    saved = _service.upsert_profile(profile)
    return _profile_to_response(saved)


@router.get(
    "/governance/agent-discovery/profiles/{agent_id}",
    response_model=ProfileResponse,
)
async def get_profile(agent_id: str) -> ProfileResponse:
    """查询单个 Agent 语义档案。"""
    profile = _service.get_profile(agent_id)
    if profile is None:
        raise HTTPException(
            status_code=404, detail=f"语义档案不存在: {agent_id}"
        )
    return _profile_to_response(profile)


@router.delete(
    "/governance/agent-discovery/profiles/{agent_id}",
)
async def remove_profile(agent_id: str) -> dict:
    """移除 Agent 语义档案。"""
    removed = _service.remove_profile(agent_id)
    if not removed:
        raise HTTPException(
            status_code=404, detail=f"语义档案不存在: {agent_id}"
        )
    return {"removed": True, "agent_id": agent_id}


@router.get(
    "/governance/agent-discovery/profiles",
    response_model=List[ProfileResponse],
)
async def list_profiles() -> List[ProfileResponse]:
    """列出全部 Agent 语义档案。"""
    profiles = _service.list_profiles()
    return [_profile_to_response(p) for p in profiles]


@router.post(
    "/governance/agent-discovery/discover",
    response_model=DiscoveryResult,
)
async def discover(request: DiscoveryRequest) -> DiscoveryResult:
    """执行 Agent 发现查询。"""
    query = DiscoveryQuery(
        intent_text=request.intent_text,
        caller=CallerContext(
            user_id=request.caller.user_id,
            tenant_id=request.caller.tenant_id,
            roles=request.caller.roles,
            permissions=request.caller.permissions,
        ),
        tags_filter=request.tags_filter,
        limit=request.limit,
    )
    try:
        return _service.discover(query)
    except DomainError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.to_dict(),
        )
