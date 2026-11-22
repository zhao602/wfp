# =============================================================================
# JWT HS256 令牌编解码（governance/auth/jwt_tokens.py）
#
# 职责：以标准库自实现 HS256 JWT 的签发与校验（无第三方依赖，HC-06/OP-03）。
# 设计意图（arch_review_v2_tasks design §4.2 / DC-03 / DC-04，卡 A3）：
#   1. 签名输入 = ascii(base64url(header) + "." + base64url(payload))；
#   2. alg 白名单硬编码：仅接受 {"alg":"HS256","typ":"JWT"} 头，其余直接拒
#      （防 alg:none / HS256→RS256 算法混淆攻击）；
#   3. 签名比对用 hmac.compare_digest（常量时间，防时序侧信道）；
#   4. base64url 编码去 "=" 填充、解码容错补齐；
#   5. TokenClaims：tenant_id/user_id/exp/iat/aud/iss 全必填，jti 本轮不启用
#      （无吊销列表，DC-04 刷新边界：不做 refresh/轮换/登出吊销）；
#   6. 错误分层：TokenExpiredError（过期）与 TokenInvalidError（其余全部）
#      可区分，供 checker 映射 AUTH_TOKEN_EXPIRED / AUTH_TOKEN_INVALID。
# =============================================================================

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

# alg 白名单：解码时仅接受此头，其余（含 alg:none / RS256 / 缺 typ / 多余键）直接拒
_ALLOWED_HEADER: Dict[str, str] = {"alg": "HS256", "typ": "JWT"}


class JwtTokenError(Exception):
    """JWT 令牌错误基类（编解码/校验失败）。"""


class TokenInvalidError(JwtTokenError):
    """令牌无效：结构/签名/alg 白名单/claims/aud/iss 校验失败。"""


class TokenExpiredError(JwtTokenError):
    """令牌已过期（now > exp + 时钟偏移容忍）。"""


class TokenClaims(BaseModel):
    """JWT 载荷声明（design §4.2 claims 契约）。

    tenant_id/user_id/exp/iat/aud/iss 全必填；jti 本轮不启用（无吊销列表，DC-04）。
    """

    tenant_id: str = Field(description="租户标识")
    user_id: str = Field(description="用户标识")
    exp: int = Field(description="过期时间戳（秒，必填；缺失即拒）")
    iat: int = Field(description="签发时间戳（秒，仅记录，不做前向校验）")
    aud: str = Field(description="受众（须等于 token_audience）")
    iss: str = Field(description="签发方（须等于 token_issuer）")
    jti: Optional[str] = Field(default=None, description="令牌唯一标识（本轮不启用）")


def _b64url_encode(data: bytes) -> str:
    """base64url 编码（去 "=" 填充）。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    """base64url 解码（容错补齐 "=" 填充）。"""
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign(signing_input: str, secret: str) -> str:
    """HMAC-SHA256 签名：signing_input 为 ascii(header_seg.payload_seg)。"""
    signature = hmac.new(
        secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(signature)


def encode_token(claims: TokenClaims, secret: str) -> str:
    """签发 JWT：三段式 header.payload.signature。

    头固定为 {"alg":"HS256","typ":"JWT"}（alg 白名单硬编码）。
    """
    header_segment = _b64url_encode(
        json.dumps(_ALLOWED_HEADER, separators=(",", ":")).encode("utf-8")
    )
    payload_segment = _b64url_encode(
        json.dumps(claims.model_dump(), separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{header_segment}.{payload_segment}"
    signature_segment = _sign(signing_input, secret)
    return f"{signing_input}.{signature_segment}"


def decode_token(
    token: str,
    secret: str,
    *,
    audience: str,
    issuer: str,
    clock_skew_seconds: int = 60,
    now: Optional[float] = None,
) -> TokenClaims:
    """校验并解码 JWT，失败抛 TokenInvalidError / TokenExpiredError。

    校验顺序：三段式结构 → 头白名单 → 签名（常量时间比对）→ claims 必填
    → aud/iss 匹配 → exp 过期（now > exp + clock_skew_seconds）。

    Args:
        token: 三段式 JWT 字符串。
        secret: HS256 对称密钥（与签发同密钥，DC-04）。
        audience: 期望受众（token_audience）。
        issuer: 期望签发方（token_issuer）。
        clock_skew_seconds: exp 校验时钟偏移容忍（秒）。
        now: 当前时间戳（秒；测试注入用，缺省 time.time()）。
    """
    if not isinstance(token, str):
        raise TokenInvalidError("令牌非字符串")

    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise TokenInvalidError("令牌非三段式结构")

    header_segment, payload_segment, signature_segment = parts

    # 1. 头白名单：仅接受 {"alg":"HS256","typ":"JWT"}，其余直接拒（防算法混淆）
    try:
        header = json.loads(_b64url_decode(header_segment))
    except Exception as exc:
        raise TokenInvalidError(f"令牌头解码失败: {type(exc).__name__}") from exc
    if header != _ALLOWED_HEADER:
        raise TokenInvalidError("令牌头不在 alg 白名单（仅接受 HS256/JWT）")

    # 2. 签名校验（常量时间比对，防时序侧信道）
    expected_signature = _sign(f"{header_segment}.{payload_segment}", secret)
    if not hmac.compare_digest(
        expected_signature.encode("ascii"), signature_segment.encode("ascii")
    ):
        raise TokenInvalidError("令牌签名校验失败（疑似篡改）")

    # 3. claims 解析与必填校验
    try:
        payload: Dict[str, Any] = json.loads(_b64url_decode(payload_segment))
    except Exception as exc:
        raise TokenInvalidError(f"令牌载荷解码失败: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise TokenInvalidError("令牌载荷非对象")

    required_fields = ("tenant_id", "user_id", "exp", "iat", "aud", "iss")
    missing = [f for f in required_fields if f not in payload]
    if missing:
        raise TokenInvalidError(f"令牌缺少必填声明: {', '.join(missing)}")
    for field in ("tenant_id", "user_id", "aud", "iss"):
        if not isinstance(payload[field], str) or not payload[field]:
            raise TokenInvalidError(f"令牌声明 {field} 非非空字符串")
    for field in ("exp", "iat"):
        value = payload[field]
        if not isinstance(value, int) or isinstance(value, bool):
            raise TokenInvalidError(f"令牌声明 {field} 非整数")

    # 4. aud/iss 匹配校验（不匹配即拒，AUTH_TOKEN_INVALID）
    if payload["aud"] != audience:
        raise TokenInvalidError("令牌受众（aud）不匹配")
    if payload["iss"] != issuer:
        raise TokenInvalidError("令牌签发方（iss）不匹配")

    # 5. exp 过期校验：now > exp + 偏移容忍 → 过期（AUTH_TOKEN_EXPIRED，可区分）
    current_time = time.time() if now is None else now
    if current_time > payload["exp"] + clock_skew_seconds:
        raise TokenExpiredError("令牌已过期")

    return TokenClaims(
        tenant_id=payload["tenant_id"],
        user_id=payload["user_id"],
        exp=payload["exp"],
        iat=payload["iat"],
        aud=payload["aud"],
        iss=payload["iss"],
        jti=payload.get("jti"),
    )
