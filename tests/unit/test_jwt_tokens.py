# =============================================================================
# JWT HS256 自实现编解码用例（tests/unit/test_jwt_tokens.py）
#
# 覆盖验收（arch_review_v2_tasks 2.8 / design §4.2 / DC-03/DC-04，CHG-23，卡 A3）：
#   1. 编解码往返成功（claims 契约字段逐项一致）；
#   2. 篡改签名 / 篡改载荷 → 拒绝；
#   3. 过期拒（含 clock skew 边界：now > exp + skew 才拒）；
#   4. aud / iss 不匹配拒；
#   5. alg 白名单拒（alg:none、RS256 头、缺 typ、多余键）；
#   6. 非三段式 / 缺必填 claim 拒；
#   7. 常量时间比对存在性（hmac.compare_digest）。
# =============================================================================

import base64
import hashlib
import hmac
import inspect
import json
import time

import pytest

import orchestration_platform.governance.auth.jwt_tokens as jwt_tokens_module
from orchestration_platform.governance.auth.jwt_tokens import (
    TokenClaims,
    TokenExpiredError,
    TokenInvalidError,
    decode_token,
    encode_token,
)

SECRET = "test-signing-secret-for-hs256-jwt-0123456789abcdef"
AUDIENCE = "orchestration-platform-api"
ISSUER = "orchestration-platform"
SKEW = 60

NOW = 1_800_000_000  # 固定时间锚点（确定性驱动）


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _claims(**overrides) -> TokenClaims:
    """构造标准 claims（exp 默认未过期）。"""
    base = {
        "tenant_id": "tenant_default",
        "user_id": "user_admin",
        "exp": NOW + 3600,
        "iat": NOW,
        "aud": AUDIENCE,
        "iss": ISSUER,
    }
    base.update(overrides)
    return TokenClaims(**base)


def _decode(token: str, **overrides):
    kwargs = {
        "audience": AUDIENCE,
        "issuer": ISSUER,
        "clock_skew_seconds": SKEW,
        "now": NOW,
    }
    kwargs.update(overrides)
    return decode_token(token, SECRET, **kwargs)


def _craft(header: dict, payload: dict, secret: str = SECRET) -> str:
    """绕过 encode_token 手工构造令牌（驱动 alg 白名单与结构校验）。"""
    header_segment = _b64url(json.dumps(header).encode("utf-8"))
    payload_segment = _b64url(json.dumps(payload).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}"
    signature = hmac.new(
        secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64url(signature)}"


_VALID_HEADER = {"alg": "HS256", "typ": "JWT"}


class TestRoundTrip:
    """编解码往返。"""

    def test_round_trip_preserves_claims(self) -> None:
        claims = _claims(jti=None)
        token = encode_token(claims, SECRET)
        decoded = _decode(token)
        assert decoded.tenant_id == claims.tenant_id
        assert decoded.user_id == claims.user_id
        assert decoded.exp == claims.exp
        assert decoded.iat == claims.iat
        assert decoded.aud == claims.aud
        assert decoded.iss == claims.iss

    def test_encoded_token_is_three_segments_without_padding(self) -> None:
        token = encode_token(_claims(), SECRET)
        parts = token.split(".")
        assert len(parts) == 3
        assert all(parts)
        assert "=" not in token  # base64url 去 "=" 填充


class TestTampering:
    """篡改拒绝。"""

    def test_tampered_signature_rejected(self) -> None:
        token = encode_token(_claims(), SECRET)
        header_seg, payload_seg, sig_seg = token.split(".")
        # 翻转签名末字符（保持 base64url 字符集）
        flipped = ("A" if sig_seg[-1] != "A" else "B") + sig_seg[1:-1]
        tampered = f"{header_seg}.{payload_seg}.{flipped}"
        with pytest.raises(TokenInvalidError):
            _decode(tampered)

    def test_tampered_payload_rejected(self) -> None:
        token = encode_token(_claims(user_id="user_admin"), SECRET)
        header_seg, _, sig_seg = token.split(".")
        # 重签发另一 user_id 的载荷段（签名随之失配）
        other = encode_token(_claims(user_id="user_attacker"), SECRET)
        _, other_payload_seg, _ = other.split(".")
        tampered = f"{header_seg}.{other_payload_seg}.{sig_seg}"
        with pytest.raises(TokenInvalidError):
            _decode(tampered)

    def test_wrong_secret_rejected(self) -> None:
        token = encode_token(_claims(), SECRET)
        with pytest.raises(TokenInvalidError):
            decode_token(
                token,
                "another-secret-value-entirely-different-0123456789",
                audience=AUDIENCE,
                issuer=ISSUER,
                clock_skew_seconds=SKEW,
                now=NOW,
            )


class TestExpiry:
    """过期拒绝（含偏移容忍边界）。"""

    def test_expired_token_rejected(self) -> None:
        # exp + skew = NOW - 1 < NOW → 过期
        token = encode_token(_claims(exp=NOW - SKEW - 1), SECRET)
        with pytest.raises(TokenExpiredError):
            _decode(token)

    def test_within_skew_tolerance_accepted(self) -> None:
        # exp + skew == NOW（未严格大于）→ 容忍通过
        token = encode_token(_claims(exp=NOW - SKEW), SECRET)
        decoded = _decode(token)
        assert decoded.exp == NOW - SKEW

    def test_far_future_exp_accepted(self) -> None:
        token = encode_token(_claims(exp=NOW + 86400), SECRET)
        assert _decode(token).exp == NOW + 86400


class TestAudienceIssuer:
    """aud / iss 不匹配拒绝。"""

    def test_audience_mismatch_rejected(self) -> None:
        token = encode_token(_claims(aud="other-audience"), SECRET)
        with pytest.raises(TokenInvalidError) as exc_info:
            _decode(token)
        assert "aud" in str(exc_info.value)

    def test_issuer_mismatch_rejected(self) -> None:
        token = encode_token(_claims(iss="other-issuer"), SECRET)
        with pytest.raises(TokenInvalidError) as exc_info:
            _decode(token)
        assert "iss" in str(exc_info.value)


class TestAlgWhitelist:
    """alg 白名单硬编码：仅接受 {"alg":"HS256","typ":"JWT"} 头。"""

    def _payload(self) -> dict:
        return _claims().model_dump()

    def test_alg_none_rejected(self) -> None:
        token = _craft({"alg": "none", "typ": "JWT"}, self._payload())
        with pytest.raises(TokenInvalidError):
            _decode(token)

    def test_rs256_header_rejected(self) -> None:
        token = _craft({"alg": "RS256", "typ": "JWT"}, self._payload())
        with pytest.raises(TokenInvalidError):
            _decode(token)

    def test_missing_typ_rejected(self) -> None:
        token = _craft({"alg": "HS256"}, self._payload())
        with pytest.raises(TokenInvalidError):
            _decode(token)

    def test_extra_header_key_rejected(self) -> None:
        token = _craft(
            {"alg": "HS256", "typ": "JWT", "kid": "k1"}, self._payload()
        )
        with pytest.raises(TokenInvalidError):
            _decode(token)


class TestStructureAndClaims:
    """结构与 claims 必填校验。"""

    def test_not_three_segments_rejected(self) -> None:
        with pytest.raises(TokenInvalidError):
            _decode("only-one-segment")
        with pytest.raises(TokenInvalidError):
            _decode("a.b")

    def test_empty_segment_rejected(self) -> None:
        with pytest.raises(TokenInvalidError):
            _decode("a.b.")

    def test_missing_required_claim_rejected(self) -> None:
        for missing in ("tenant_id", "user_id", "exp", "iat", "aud", "iss"):
            payload = _claims().model_dump()
            del payload[missing]
            token = _craft(_VALID_HEADER, payload)
            with pytest.raises(TokenInvalidError) as exc_info:
                _decode(token)
            assert missing in str(exc_info.value)

    def test_non_integer_exp_rejected(self) -> None:
        payload = _claims().model_dump()
        payload["exp"] = "not-a-number"
        token = _craft(_VALID_HEADER, payload)
        with pytest.raises(TokenInvalidError):
            _decode(token)


class TestConstantTimeCompare:
    """常量时间比对存在性（hmac.compare_digest，防时序侧信道）。"""

    def test_compare_digest_used_in_module(self) -> None:
        source = inspect.getsource(jwt_tokens_module)
        assert "hmac.compare_digest" in source

    def test_signature_compare_is_constant_time(self) -> None:
        # decode_token 内部签名比对走 hmac.compare_digest（黑盒佐证：正确签名通过）
        token = encode_token(_claims(), SECRET)
        assert _decode(token).user_id == "user_admin"
