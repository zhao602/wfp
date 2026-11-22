# =============================================================================
# 生产令牌校验模式强校验用例（tests/unit/test_startup_token_verify_production.py）
#
# 覆盖验收（roadmap B0/D2，token_verify_mode 生产强制 jwt）：
#   1. production + 非 jwt（both/legacy）→ StartupConfigError 拒绝启动；
#   2. production + jwt → 令牌守卫放行（弱密钥检查独立叠加照常拦截）；
#   3. 守卫先于弱密钥检查执行（同时命中时异常指向令牌模式）；
#   4. development 任意模式 → 完全静默（不抛异常、零告警）；
#   5. 拒绝信息附修复指引（TOKEN_VERIFY_MODE=jwt）。
# =============================================================================

import logging

import pytest

from orchestration_platform.configuration.settings import Settings
from orchestration_platform.configuration.startup_checks import (
    StartupConfigError,
    check_token_verify_mode,
    run_startup_checks,
)

# 强密钥样本（与 test_startup_weak_secret 同源口径，避免弱密钥用例误触发）
_STRONG = "s3cure-Random-Secret-Value-32-bytes!"
assert len(_STRONG) >= 32


def _settings(**overrides) -> Settings:
    """构造可控配置（默认 production + jwt + 三项强密钥，逐项覆盖驱动用例）。"""
    base = {
        "environment": "production",
        "token_verify_mode": "jwt",
        "api_access_token": _STRONG,
        "postgres_password": _STRONG,
        "token_signing_secret": _STRONG,
    }
    base.update(overrides)
    return Settings(**base)


class TestProductionTokenVerifyModeFailFast:
    """production + 非 jwt 令牌校验模式 → 拒绝启动（StartupConfigError）。"""

    @pytest.mark.parametrize("mode", ["both", "legacy"])
    def test_non_jwt_mode_rejected(self, mode: str) -> None:
        """both/legacy 均含明文令牌回退路径，production 拒绝启动并附修复指引。"""
        settings = _settings(token_verify_mode=mode)
        with pytest.raises(StartupConfigError) as exc_info:
            run_startup_checks(settings)
        message = str(exc_info.value)
        assert mode in message
        assert "TOKEN_VERIFY_MODE=jwt" in message

    def test_guard_runs_before_weak_secret_check(self) -> None:
        """守卫先于弱密钥检查：同时命中时异常指向令牌模式而非弱密钥。"""
        settings = _settings(token_verify_mode="both", api_access_token="short")
        with pytest.raises(StartupConfigError) as exc_info:
            run_startup_checks(settings)
        message = str(exc_info.value)
        assert "令牌校验模式" in message
        assert "弱密钥" not in message

    def test_jwt_mode_lets_weak_secret_check_still_fail(self) -> None:
        """jwt 模式放行令牌守卫后，弱密钥检查照常拦截（两守卫独立叠加）。"""
        settings = _settings(token_verify_mode="jwt", api_access_token="short")
        with pytest.raises(StartupConfigError) as exc_info:
            run_startup_checks(settings)
        assert "弱密钥" in str(exc_info.value)

    def test_jwt_mode_with_strong_secrets_passes(self) -> None:
        """production + jwt + 强密钥 → 全部通过（不抛异常）。"""
        run_startup_checks(_settings())


class TestTokenVerifyModeGuardUnit:
    """check_token_verify_mode 单元行为。"""

    @pytest.mark.parametrize("mode", ["both", "legacy"])
    def test_production_non_jwt_reports_problem(self, mode: str) -> None:
        problems = check_token_verify_mode(_settings(token_verify_mode=mode))
        assert len(problems) == 1
        assert mode in problems[0]
        assert "jwt" in problems[0]

    def test_production_jwt_no_problems(self) -> None:
        assert check_token_verify_mode(_settings(token_verify_mode="jwt")) == []

    @pytest.mark.parametrize("mode", ["jwt", "both", "legacy"])
    def test_development_any_mode_silent(self, mode: str, caplog) -> None:
        """development 任意模式完全静默：零问题项、不抛异常、零告警。"""
        settings = _settings(environment="development", token_verify_mode=mode)
        assert check_token_verify_mode(settings) == []
        with caplog.at_level(logging.WARNING):
            run_startup_checks(settings)
        assert caplog.text == ""
