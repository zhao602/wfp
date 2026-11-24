# =============================================================================
# SSO 领域模型（governance/sso/models.py）
#
# 职责：定义 IdP 配置、断言主体、登录结果等领域模型。
# 设计意图（对应偏差修正 design 2.3.2）：
#   - IdpConfiguration：IdP 配置（含加密凭据、属性映射、角色映射）
#   - AssertionPrincipal：统一断言视图（协议无关）
#   - SsoLoginResult / AuthRedirect
# =============================================================================

from datetime import datetime, timezone
from typing import Dict, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import SsoProtocolType


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IdpConfiguration(BaseModel):
    """IdP 配置。"""

    idp_id: str = Field(description="IdP 标识（全局唯一）")
    tenant_id: str = Field(description="租户标识")
    protocol_type: SsoProtocolType = Field(description="协议类型")
    auth_endpoint: str = Field(description="认证端点 URL")
    token_endpoint: str = Field(default="", description="令牌/断言端点 URL")
    audience: str = Field(description="受众（SP 标识）")
    oidc_issuer: str = Field(default="", description="OIDC 签发者（非空时校验 id_token 的 iss 声明）")
    encrypted_credential: str = Field(default="", description="加密凭据（密文）")
    credential_version: int = Field(default=0, description="凭据版本号")
    attribute_mappings: Dict[str, str] = Field(
        default_factory=dict, description="属性映射（IdP 属性 → 平台字段）"
    )
    role_mappings: Dict[str, str] = Field(
        default_factory=dict, description="角色映射（IdP 角色 → RBAC 角色）"
    )
    default_role_on_missing: str = Field(default="", description="属性缺失时缺省角色")
    enabled: bool = Field(default=True, description="是否启用")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")


class AssertionPrincipal(BaseModel):
    """统一断言视图（协议无关）。

    SAML 与 OIDC 两协议输出同一模型，SsoService 主流程协议无关。
    """

    external_user_id: str = Field(description="外部用户标识")
    attributes: Dict[str, str] = Field(
        default_factory=dict, description="属性声明"
    )
    assertion_id: str = Field(description="断言标识（唯一）")
    not_before: datetime = Field(description="生效时间")
    not_on_or_after: datetime = Field(description="失效时间")


class SsoLoginResult(BaseModel):
    """SSO 登录结果。"""

    success: bool = Field(description="是否成功")
    token: str = Field(default="", description="平台会话令牌（成功时有效）")
    expires_at: datetime = Field(default_factory=_utc_now, description="令牌过期时间")
    error_code: Optional[str] = Field(default=None, description="错误码")
    error_message: Optional[str] = Field(default=None, description="错误说明")
    user_id: Optional[str] = Field(default=None, description="平台用户标识")
    is_new_user: bool = Field(default=False, description="是否 JIT 新开通用户")


class AuthRedirect(BaseModel):
    """认证重定向。"""

    redirect_url: str = Field(description="重定向 URL")
    state: str = Field(description="状态参数（防 CSRF）")
    is_available: bool = Field(default=True, description="协议是否可用")
    degraded_message: str = Field(default="", description="降级提示信息")
