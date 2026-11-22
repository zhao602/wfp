# =============================================================================
# 启动期强校验（configuration/startup_checks.py）
#
# 职责：environment=production 时执行启动期强校验 fail-fast（拒绝启动）：
#   1. 令牌校验模式守卫（roadmap B0/D2）：token_verify_mode 仅允许 'jwt'——
#      'both'/'legacy' 存在明文令牌校验回退路径，生产禁用；
#   2. 三项密钥弱密钥黑名单（arch_review_v2 DC-05 / 卡 A5 / CHG-02）：
#      api_access_token / postgres_password / token_signing_secret。
# 设计意图（arch_review_v2_tasks DC-05 / 卡 A5 / CHG-02 + roadmap B0/D2）：
#   1. 三项密钥（评审 §6.1 指认全部弱密钥现状）：api_access_token / postgres_password /
#      token_signing_secret；
#   2. 黑名单三条：各字段当前默认值、change-me 前缀、长度 < 32 字符；
#   3. production 命中即抛 StartupConfigError（附修复指引）——「拒绝启动」而非
#      「启动后接口失败」（create_app 装配最早点调用，CHG-03）；
#   4. development 仅 logging.warning 不阻断（2797 例基线保护）。
# =============================================================================

import logging
from typing import List, Tuple

from orchestration_platform.configuration.settings import Settings

logger = logging.getLogger(__name__)

# 弱密钥判定：长度下界（< 32 即弱）
_WEAK_SECRET_MIN_LENGTH = 32
_CHANGE_ME_PREFIX = "change-me"


class StartupConfigError(RuntimeError):
    """启动期配置强校验失败（production 弱密钥 fail-fast，拒绝启动）。"""


def _check_secret(field_name: str, value: str, default_value: str) -> List[str]:
    """单字段弱密钥判定，返回命中原因清单（空列表 = 通过）。

    黑名单三条（DC-05）：等于字段当前默认值 / change-me 前缀 / 长度 < 32。
    """
    reasons: List[str] = []
    if value == default_value:
        reasons.append(f"等于字段默认值 {default_value!r}")
    if value.startswith(_CHANGE_ME_PREFIX):
        reasons.append("命中 change-me 前缀占位符")
    if len(value) < _WEAK_SECRET_MIN_LENGTH:
        reasons.append(f"长度 {len(value)} < {_WEAK_SECRET_MIN_LENGTH}")
    return reasons


# 三项密钥 → 各自当前默认值（settings.py 字段默认值，E12 锚点）
_WEAK_SECRET_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("api_access_token", "change-me-please"),
    ("postgres_password", "change-me-please"),
    ("token_signing_secret", "change-me-token-secretF"),
)


def check_weak_secrets(settings: Settings) -> List[str]:
    """对三项密钥执行弱密钥黑名单判定，返回问题清单（空 = 全部通过）。"""
    problems: List[str] = []
    for field_name, default_value in _WEAK_SECRET_FIELDS:
        value: str = getattr(settings, field_name)
        reasons = _check_secret(field_name, value, default_value)
        if reasons:
            problems.append(f"{field_name}: {'；'.join(reasons)}")
    return problems


def check_token_verify_mode(settings: Settings) -> List[str]:
    """生产环境令牌校验模式守卫（roadmap B0/D2），返回问题清单（空 = 通过）。

    - environment != production：返回 []（development/test 完全静默零噪音）；
    - production 且 token_verify_mode == 'jwt'：返回 []；
    - production 且 token_verify_mode 为 'both'/'legacy'：返回问题项——
      两模式均含明文令牌校验回退路径，生产禁用。
    """
    if settings.environment != "production":
        return []
    mode: str = settings.token_verify_mode
    if mode == "jwt":
        return []
    return [
        f"token_verify_mode={mode!r}（生产仅允许 'jwt'，当前模式存在明文令牌校验回退路径）"
    ]


_REPAIR_GUIDE = (
    "弱密钥修复指引：为以下字段设置强随机值"
    "（≥32 字符、非默认值、非 change-me 前缀），写入 .env（生产为 .env.production）后重启：\n"
    "  API_ACCESS_TOKEN=<32+ 字符强随机串>\n"
    "  POSTGRES_PASSWORD=<32+ 字符强随机串>\n"
    "  TOKEN_SIGNING_SECRET=<32+ 字符强随机串（JWT 签发与校验同密钥）>\n"
    '生成示例：python -c "import secrets; print(secrets.token_urlsafe(32))"'
)


def run_startup_checks(settings: Settings) -> None:
    """启动期强校验入口（create_app 最早装配点调用，CHG-03）。

    - production：token_verify_mode 非 'jwt' → 抛 StartupConfigError 拒绝启动
      （roadmap B0/D2，先于弱密钥检查执行）；
    - production：命中弱密钥黑名单 → 抛 StartupConfigError 拒绝启动；
    - development：仅 logging.warning 不阻断。
    """
    token_problems = check_token_verify_mode(settings)
    if token_problems:
        raise StartupConfigError(
            "生产环境令牌校验模式 fail-fast：拒绝启动。\n命中项：\n  "
            + "\n  ".join(token_problems)
            + "\n修复指引：设置 TOKEN_VERIFY_MODE=jwt 后重启"
            "（.env.production 已默认 jwt；'both'/'legacy' 的明文令牌校验路径生产禁用）。"
        )
    problems = check_weak_secrets(settings)
    if not problems:
        return
    if settings.environment == "production":
        raise StartupConfigError(
            "生产环境弱密钥 fail-fast：拒绝启动。\n命中项：\n  "
            + "\n  ".join(problems)
            + "\n"
            + _REPAIR_GUIDE
        )
    logger.warning(
        "检测到弱密钥配置（development 不阻断，production 环境将拒绝启动）：%s",
        "; ".join(problems),
    )
