# =============================================================================
# SSO REST API（api/routers/governance/sso_router.py）
# =============================================================================

import logging
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import SsoProtocolType
from orchestration_platform.governance.sso.idp_config_manager import IdpConfigManager
from orchestration_platform.governance.sso.models import (
    IdpConfiguration,
    SsoLoginResult,
)
from orchestration_platform.governance.sso.sso_service import SsoService

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["SSO 企业身份集成"])

_config_manager = IdpConfigManager()
_sso_service = SsoService(config_manager=_config_manager)


class IdpConfigUpsertRequest(BaseModel):
    """IdP 配置创建/更新请求。"""
    idp_id: str = Field(description="IdP 标识")
    tenant_id: str = Field(description="租户标识")
    protocol_type: str = Field(description="协议类型")
    auth_endpoint: str = Field(description="认证端点")
    token_endpoint: str = Field(default="", description="令牌端点")
    audience: str = Field(description="受众")
    oidc_issuer: str = Field(default="", description="OIDC 签发者（非空时校验 iss 声明）")
    credential_plaintext: str = Field(default="", description="凭据明文（仅写入，不回显）")
    attribute_mappings: dict = Field(default_factory=dict, description="属性映射")
    role_mappings: dict = Field(default_factory=dict, description="角色映射")
    enabled: bool = Field(default=True, description="是否启用")


class IdpConfigResponse(BaseModel):
    """IdP 配置响应（凭据恒为掩码）。"""
    idp_id: str = Field(description="IdP 标识")
    tenant_id: str = Field(description="租户标识")
    protocol_type: str = Field(description="协议类型")
    auth_endpoint: str = Field(description="认证端点")
    audience: str = Field(description="受众")
    oidc_issuer: str = Field(default="", description="OIDC 签发者")
    encrypted_credential: str = Field(description="凭据掩码")
    credential_version: int = Field(description="凭据版本号")
    enabled: bool = Field(description="是否启用")


class CallbackRequest(BaseModel):
    """回调请求。"""
    callback_payload: dict = Field(description="回调载荷")


@router.get("/auth/sso/idps", response_model=List[IdpConfigResponse])
async def list_idps(tenant_id: str) -> List[IdpConfigResponse]:
    """列出 IdP 配置。"""
    configs = _config_manager.list_by_tenant(tenant_id)
    return [
        IdpConfigResponse(
            idp_id=c.idp_id,
            tenant_id=c.tenant_id,
            protocol_type=c.protocol_type.value,
            auth_endpoint=c.auth_endpoint,
            audience=c.audience,
            oidc_issuer=c.oidc_issuer,
            encrypted_credential=c.encrypted_credential,
            credential_version=c.credential_version,
            enabled=c.enabled,
        )
        for c in configs
    ]


@router.post("/auth/sso/idps", response_model=IdpConfigResponse)
async def create_idp(request: IdpConfigUpsertRequest) -> IdpConfigResponse:
    """创建 IdP 配置（凭据仅写入不回显）。"""
    config = IdpConfiguration(
        idp_id=request.idp_id,
        tenant_id=request.tenant_id,
        protocol_type=SsoProtocolType(request.protocol_type),
        auth_endpoint=request.auth_endpoint,
        token_endpoint=request.token_endpoint,
        audience=request.audience,
        oidc_issuer=request.oidc_issuer,
        attribute_mappings=request.attribute_mappings,
        role_mappings=request.role_mappings,
        enabled=request.enabled,
    )
    saved = _config_manager.save(config, request.credential_plaintext)
    return IdpConfigResponse(
        idp_id=saved.idp_id,
        tenant_id=saved.tenant_id,
        protocol_type=saved.protocol_type.value,
        auth_endpoint=saved.auth_endpoint,
        audience=saved.audience,
        oidc_issuer=saved.oidc_issuer,
        encrypted_credential=saved.encrypted_credential,
        credential_version=saved.credential_version,
        enabled=saved.enabled,
    )


@router.get("/auth/sso/{idp_id}/initiate")
async def initiate_sso(idp_id: str):
    """发起 SSO 认证重定向。"""
    redirect = _sso_service.initiate(idp_id)
    return {
        "redirect_url": redirect.redirect_url,
        "state": redirect.state,
        "is_available": redirect.is_available,
        "degraded_message": redirect.degraded_message,
    }


@router.post("/auth/sso/{idp_id}/callback", response_model=SsoLoginResult)
async def sso_callback(idp_id: str, request: CallbackRequest) -> SsoLoginResult:
    """处理 IdP 回调。"""
    return await _sso_service.handle_callback(idp_id, request.callback_payload)
