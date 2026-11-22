# =============================================================================
# 令牌校验三态用例（tests/unit/test_permission_checker_token_modes.py）
#
# 覆盖验收（arch_review_v2_tasks 2.8 / design DC-03，CHG-24，GK-02）：
#   1. both（默认）：存量明文令牌继续可用（GK-02 证据）+ JWT 通过 + claims 注入；
#   2. jwt：明文令牌拒（AUTH_TOKEN_FORMAT_UNSUPPORTED）、过期拒（AUTH_TOKEN_EXPIRED）、
#      篡改拒（AUTH_TOKEN_INVALID）；
#   3. legacy：明文令牌通过、JWT 形态走既有明文解析拒绝（原 :54-67 行为保留）；
#   4. 三态下令牌缺失语义一致（"令牌缺失"）。
# =============================================================================

import asyncio
import time

import pytest

import orchestration_platform.governance.rbac.permission_checker as checker_module
from orchestration_platform.configuration.settings import Settings
from orchestration_platform.domain.models.enums import (
    OperationType,
    ResourceType,
)
from orchestration_platform.governance.auth.jwt_tokens import TokenClaims, encode_token
from orchestration_platform.governance.interceptors.base import GovernanceRequest
from orchestration_platform.governance.rbac.permission_cache import PermissionCache
from orchestration_platform.governance.rbac.permission_checker import (
    RbacPermissionChecker,
)
from orchestration_platform.governance.rbac.permission_registry import (
    create_default_registry,
)

SECRET = "checker-mode-test-secret-0123456789abcdef0123456789"
AUDIENCE = "orchestration-platform-api"
ISSUER = "orchestration-platform"
TENANT = "tenant_default"
USER = "user_admin"


def _patch_settings(monkeypatch, **overrides) -> Settings:
    """构造并注入 checker 模块级 get_settings（authenticate 内部读取三态配置）。"""
    base = {
        "token_verify_mode": "both",
        "token_signing_secret": SECRET,
        "token_audience": AUDIENCE,
        "token_issuer": ISSUER,
        "token_clock_skew_seconds": 60,
    }
    base.update(overrides)
    settings = Settings(**base)
    monkeypatch.setattr(checker_module, "get_settings", lambda: settings)
    return settings


def _checker() -> RbacPermissionChecker:
    return RbacPermissionChecker(
        registry=create_default_registry(),
        cache=PermissionCache(),
    )


def _request(token: str) -> GovernanceRequest:
    return GovernanceRequest(
        token=token,
        resource_type=ResourceType.WORKFLOW_DEFINITION,
        operation=OperationType.READ,
        trace_id="trace-token-mode",
    )


def _plaintext_token() -> str:
    """存量明文令牌：tenant_id:user_id:expires_timestamp。"""
    return f"{TENANT}:{USER}:{int(time.time()) + 3600}"


def _jwt_token(**claim_overrides) -> str:
    base = {
        "tenant_id": TENANT,
        "user_id": USER,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "aud": AUDIENCE,
        "iss": ISSUER,
    }
    base.update(claim_overrides)
    return encode_token(TokenClaims(**base), SECRET)


class TestBothMode:
    """both（默认）：JWT 优先 + 明文回退（存量令牌零失效，GK-02）。"""

    def test_plaintext_token_still_accepted_gk02(self, monkeypatch) -> None:
        """GK-02 证据：both 默认下存量明文令牌继续可用。"""
        _patch_settings(monkeypatch, token_verify_mode="both")
        checker = _checker()
        request = _request(_plaintext_token())
        decision = asyncio.run(checker.authenticate(request))
        assert decision.allowed
        assert request.tenant_id == TENANT
        assert request.user_id == USER
        assert decision.tenant_context == TENANT

    def test_valid_jwt_accepted_with_claims_injection(self, monkeypatch) -> None:
        """JWT 通过且 claims 注入 GovernanceRequest.tenant_id/user_id。"""
        _patch_settings(monkeypatch, token_verify_mode="both")
        checker = _checker()
        request = _request(_jwt_token())
        decision = asyncio.run(checker.authenticate(request))
        assert decision.allowed
        assert request.tenant_id == TENANT
        assert request.user_id == USER

    def test_expired_jwt_falls_back_to_legacy_parse(self, monkeypatch) -> None:
        """both 下过期 JWT 回退明文解析（必然失败，但走既有路径语义）。"""
        _patch_settings(monkeypatch, token_verify_mode="both")
        checker = _checker()
        expired = _jwt_token(exp=int(time.time()) - 3600)
        request = _request(expired)
        decision = asyncio.run(checker.authenticate(request))
        assert not decision.allowed
        # 回退到既有明文解析路径：JWT 串无 ":" → "令牌格式无效"
        assert decision.deny_reason == "令牌格式无效"


class TestJwtMode:
    """jwt：仅 JWT，回退关闭。"""

    def test_plaintext_token_rejected_format_unsupported(
        self, monkeypatch
    ) -> None:
        """jwt 模式明文令牌拒：deny_reason 携带 AUTH_TOKEN_FORMAT_UNSUPPORTED。"""
        _patch_settings(monkeypatch, token_verify_mode="jwt")
        checker = _checker()
        request = _request(_plaintext_token())
        decision = asyncio.run(checker.authenticate(request))
        assert not decision.allowed
        assert "AUTH_TOKEN_FORMAT_UNSUPPORTED" in decision.deny_reason

    def test_valid_jwt_accepted(self, monkeypatch) -> None:
        _patch_settings(monkeypatch, token_verify_mode="jwt")
        checker = _checker()
        request = _request(_jwt_token())
        decision = asyncio.run(checker.authenticate(request))
        assert decision.allowed
        assert request.user_id == USER

    def test_expired_jwt_rejected_with_expired_code(self, monkeypatch) -> None:
        _patch_settings(monkeypatch, token_verify_mode="jwt")
        checker = _checker()
        expired = _jwt_token(exp=int(time.time()) - 3600)
        request = _request(expired)
        decision = asyncio.run(checker.authenticate(request))
        assert not decision.allowed
        assert "AUTH_TOKEN_EXPIRED" in decision.deny_reason

    def test_tampered_jwt_rejected_with_invalid_code(self, monkeypatch) -> None:
        _patch_settings(monkeypatch, token_verify_mode="jwt")
        checker = _checker()
        token = _jwt_token()
        header_seg, payload_seg, sig_seg = token.split(".")
        flipped_sig = ("A" if sig_seg[-1] != "A" else "B") + sig_seg[1:-1]
        request = _request(f"{header_seg}.{payload_seg}.{flipped_sig}")
        decision = asyncio.run(checker.authenticate(request))
        assert not decision.allowed
        assert "AUTH_TOKEN_INVALID" in decision.deny_reason


class TestLegacyMode:
    """legacy：仅明文（既有解析路径原样保留）。"""

    def test_plaintext_token_accepted(self, monkeypatch) -> None:
        _patch_settings(monkeypatch, token_verify_mode="legacy")
        checker = _checker()
        request = _request(_plaintext_token())
        decision = asyncio.run(checker.authenticate(request))
        assert decision.allowed
        assert request.tenant_id == TENANT

    def test_jwt_token_rejected_via_legacy_parse(self, monkeypatch) -> None:
        """legacy 下 JWT 形态走既有明文解析（无 ":" → 令牌格式无效）。"""
        _patch_settings(monkeypatch, token_verify_mode="legacy")
        checker = _checker()
        request = _request(_jwt_token())
        decision = asyncio.run(checker.authenticate(request))
        assert not decision.allowed
        assert decision.deny_reason == "令牌格式无效"


class TestMissingToken:
    """三态下令牌缺失语义一致。"""

    @pytest.mark.parametrize("mode", ["legacy", "jwt", "both"])
    def test_missing_token_denied(self, monkeypatch, mode: str) -> None:
        _patch_settings(monkeypatch, token_verify_mode=mode)
        checker = _checker()
        request = GovernanceRequest(
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            operation=OperationType.READ,
            trace_id="trace-none",
        )
        decision = asyncio.run(checker.authenticate(request))
        assert not decision.allowed
        assert decision.deny_reason == "令牌缺失"
