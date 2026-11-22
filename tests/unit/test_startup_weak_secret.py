# =============================================================================
# 启动期弱密钥强校验用例（tests/unit/test_startup_weak_secret.py）
#
# 覆盖验收（arch_review_v2_tasks 2.3 / design DC-05 / 卡 A5，CHG-26）：
#   1. production 三项密钥黑名单逐项拒启动（默认值 / change-me 前缀 / 长度 < 32）；
#   2. development 弱密钥仅告警不阻断（logging.warning，2797 例基线保护）；
#   3. 长度 32 边界（= 32 通过、31 拒绝）；
#   4. 修复指引文本存在性断言（fail-fast 异常附修复指引，DC-05）。
# =============================================================================

import logging

import pytest

from orchestration_platform.configuration.settings import Settings
from orchestration_platform.configuration.startup_checks import (
    StartupConfigError,
    check_weak_secrets,
    run_startup_checks,
)

# 强密钥样本：32 字符、非默认值、非 change-me 前缀（三项黑名单全不命中）
_STRONG = "s3cure-Random-Secret-Value-32-bytes!"
assert len(_STRONG) >= 32

# 三项密钥当前字段默认值（settings.py E12 锚点，黑名单第一条）
_FIELD_DEFAULTS = {
    "api_access_token": "change-me-please",
    "postgres_password": "change-me-please",
    "token_signing_secret": "change-me-token-secretF",
}


def _production_settings(**overrides) -> Settings:
    """构造 production 配置（三项密钥默认给强值，逐项覆盖驱动黑名单）。"""
    base = {
        "environment": "production",
        # token_verify_mode 固定 jwt（roadmap B0/D2：生产令牌校验守卫先于弱密钥
        # 检查执行，此处聚焦弱密钥用例本身）
        "token_verify_mode": "jwt",
        "api_access_token": _STRONG,
        "postgres_password": _STRONG,
        "token_signing_secret": _STRONG,
    }
    base.update(overrides)
    return Settings(**base)


class TestProductionWeakSecretFailFast:
    """production + 弱密钥 → 拒绝启动（StartupConfigError）。"""

    @pytest.mark.parametrize("field_name", list(_FIELD_DEFAULTS))
    def test_default_value_rejected(self, field_name: str) -> None:
        """黑名单第一条：命中字段当前默认值 → 拒启动。"""
        settings = _production_settings(
            **{field_name: _FIELD_DEFAULTS[field_name]}
        )
        with pytest.raises(StartupConfigError) as exc_info:
            run_startup_checks(settings)
        # 异常信息定位命中字段并附修复指引
        assert field_name in str(exc_info.value)
        assert "弱密钥修复指引" in str(exc_info.value)

    @pytest.mark.parametrize("field_name", list(_FIELD_DEFAULTS))
    def test_change_me_prefix_rejected(self, field_name: str) -> None:
        """黑名单第二条：change-me 前缀占位符（即使长度足够）→ 拒启动。"""
        weak_with_prefix = "change-me-" + "a" * 40
        settings = _production_settings(**{field_name: weak_with_prefix})
        with pytest.raises(StartupConfigError):
            run_startup_checks(settings)

    @pytest.mark.parametrize("field_name", list(_FIELD_DEFAULTS))
    def test_short_length_rejected(self, field_name: str) -> None:
        """黑名单第三条：长度 < 32（无前缀、非默认值）→ 拒启动。"""
        settings = _production_settings(**{field_name: "a" * 31})
        with pytest.raises(StartupConfigError):
            run_startup_checks(settings)

    def test_repair_guide_mentions_all_env_names(self) -> None:
        """修复指引存在性：三项环境变量名与生成示例均在指引文本中。"""
        settings = _production_settings(api_access_token="short")
        with pytest.raises(StartupConfigError) as exc_info:
            run_startup_checks(settings)
        message = str(exc_info.value)
        assert "API_ACCESS_TOKEN" in message
        assert "POSTGRES_PASSWORD" in message
        assert "TOKEN_SIGNING_SECRET" in message
        assert "secrets.token_urlsafe" in message


class TestLengthBoundary:
    """长度边界：= 32 通过、31 拒绝。"""

    def test_length_32_passes(self) -> None:
        settings = _production_settings(token_signing_secret="b" * 32)
        run_startup_checks(settings)  # 不抛异常

    def test_length_31_rejected(self) -> None:
        settings = _production_settings(token_signing_secret="b" * 31)
        with pytest.raises(StartupConfigError):
            run_startup_checks(settings)


class TestDevelopmentWarningOnly:
    """development + 弱密钥 → 仅告警不阻断。"""

    def test_weak_secrets_warn_without_raising(self, caplog) -> None:
        settings = Settings(
            environment="development",
            api_access_token="change-me-please",
            postgres_password="change-me-please",
            token_signing_secret="change-me-token-secretF",
        )
        with caplog.at_level(
            logging.WARNING,
            logger="orchestration_platform.configuration.startup_checks",
        ):
            run_startup_checks(settings)  # 不抛异常
        assert "弱密钥" in caplog.text


class TestStrongSecretsPass:
    """三项强密钥 → 零问题、零告警。"""

    def test_strong_secrets_no_problems_no_warnings(self, caplog) -> None:
        settings = _production_settings()
        assert check_weak_secrets(settings) == []
        with caplog.at_level(logging.WARNING):
            run_startup_checks(settings)
        assert caplog.text == ""
