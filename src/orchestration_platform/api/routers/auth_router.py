# =============================================================================
# JWT 签发端点（api/routers/auth_router.py）
#
# 职责：POST /api/v1/auth/token —— JWT 访问令牌签发（arch_review_v2_tasks G2，
#       design §4.2 签发端点契约，CHG-13，DC-03 规则 3）。
# 设计意图：
#   1. 独立开关 auth_token_issuance_enabled 门控（默认 False，HC-07）——
#      关闭时路由不注册（端点 404），与 application_factory 既有开关守卫范式一致（E20）；
#   2. 入参 {tenant_id, user_id, ttl_seconds = token_default_ttl_seconds}；
#      出参 {access_token, token_type: "Bearer", expires_at}；
#   3. ttl 上界 = token_default_ttl_seconds × 24（防超长令牌），超界 422；
#   4. 前置条件：调用方处于部署侧信任边界（内网/网关后调用，
#      不承担自助注册，deploy.md 明示）。
# =============================================================================

import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.auth.jwt_tokens import TokenClaims, encode_token

router = APIRouter(tags=["auth"])


class TokenIssueRequest(BaseModel):
    """令牌签发入参（design §4.2）。"""

    tenant_id: str = Field(
        min_length=1, max_length=128, description="租户标识"
    )
    user_id: str = Field(
        min_length=1, max_length=128, description="用户标识"
    )
    ttl_seconds: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "令牌有效期（秒）；缺省取 token_default_ttl_seconds，"
            "上界为其 × 24（超界 422）"
        ),
    )


class TokenIssueResponse(BaseModel):
    """令牌签发出参（design §4.2）。"""

    access_token: str = Field(description="JWT 访问令牌")
    token_type: str = Field(default="Bearer", description="令牌类型")
    expires_at: int = Field(description="过期时间戳（秒）")


@router.post("/auth/token", response_model=TokenIssueResponse)
async def issue_token(payload: TokenIssueRequest) -> TokenIssueResponse:
    """签发 JWT 访问令牌（HS256，密钥 token_signing_secret，aud/iss 同校验配置）。"""
    settings = get_settings()
    default_ttl = settings.token_default_ttl_seconds
    ttl = default_ttl if payload.ttl_seconds is None else payload.ttl_seconds

    # ttl 上界校验：默认值 × 24（防超长令牌，design §4.2 异常行）
    if ttl > default_ttl * 24:
        raise HTTPException(
            status_code=422,
            detail=(
                f"ttl_seconds 超上界：{default_ttl} × 24 = {default_ttl * 24}"
                f"（当前 {ttl}）"
            ),
        )

    issued_at = int(time.time())
    claims = TokenClaims(
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        exp=issued_at + ttl,
        iat=issued_at,
        aud=settings.token_audience,
        iss=settings.token_issuer,
    )
    token = encode_token(claims, settings.token_signing_secret)
    return TokenIssueResponse(
        access_token=token,
        token_type="Bearer",
        expires_at=claims.exp,
    )
