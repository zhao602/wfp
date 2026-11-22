# =============================================================================
# LLM 客户端工厂（integrations/llm/factory.py）
#
# 职责：按全局配置构造「主备 LLM 客户端」，供各 API 路由共用。
# 设计意图（fix_agent_executor_llm_client DC-2，NL 编排接线复用）：
#   1. 唯一权威：主/备客户端的构造逻辑只在此处实现，路由侧零重复定义；
#   2. 配置缺失（base_url / api_key / model 任一为空）→ 返回 None，
#      由调用方各自决定降级语义（执行器跳过注册 / NL 编排返回明确错误码）；
#   3. 备用模型配置不全或构造失败 → 仅告警并回落到仅主模型，不影响主链路；
#   4. 凭据零泄露：日志只打 model 标识与 has_fallback，不打 api_key / base_url。
# =============================================================================

import logging
from typing import Optional

from orchestration_platform.configuration import settings as _settings_module
from orchestration_platform.integrations.abstractions.external_clients import ILlmClient

_logger = logging.getLogger(__name__)


def build_llm_client() -> Optional[ILlmClient]:
    """构造主备 LLM 客户端；配置缺失或构造失败时返回 None。

    读取 get_settings() 的 llm_primary_base_url / llm_primary_api_key /
    llm_primary_model（以及 fallback 三件套）。重依赖延迟导入，避免模块
    顶层引入 httpx（沙箱无 httpx 时不影响导入）。
    """
    try:
        settings = _settings_module.get_settings()

        base_url = settings.llm_primary_base_url
        api_key = settings.llm_primary_api_key
        model = settings.llm_primary_model

        if not base_url or not api_key or not model:
            _logger.info("LLM 客户端工厂: 主模型配置缺失，返回 None")
            return None

        try:
            from orchestration_platform.integrations.llm.http_llm_client import (
                HttpLlmClient,
            )
            from orchestration_platform.integrations.llm.primary_fallback_llm_client import (
                PrimaryFallbackLlmClient,
            )

            primary = HttpLlmClient(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout_seconds=float(settings.llm_primary_timeout_seconds),
            )

            # B2-C4 T4.2：重试配置经构造后传递（构造签名零改动，既有单测
            # faulty_init 桩兼容）；getattr 防御非完整 settings 桩——
            # 缺省即关闭态（GK-11：关闭时行为与现状逐字节等价）
            retry_enabled = bool(getattr(settings, "llm_retry_enabled", False))
            retry_max_attempts = int(getattr(settings, "llm_retry_max_attempts", 3))
            retry_backoff_base = float(
                getattr(settings, "llm_retry_backoff_base_seconds", 0.5)
            )
            primary.configure_retry(
                enabled=retry_enabled,
                max_attempts=retry_max_attempts,
                backoff_base_seconds=retry_backoff_base,
            )

            fallback = None
            fb_url = settings.llm_fallback_base_url
            fb_key = settings.llm_fallback_api_key
            fb_model = settings.llm_fallback_model
            if fb_url and fb_key and fb_model:
                try:
                    fallback = HttpLlmClient(
                        base_url=fb_url,
                        api_key=fb_key,
                        model=fb_model,
                        timeout_seconds=float(settings.llm_fallback_timeout_seconds),
                    )
                    fallback.configure_retry(
                        enabled=retry_enabled,
                        max_attempts=retry_max_attempts,
                        backoff_base_seconds=retry_backoff_base,
                    )
                except Exception as exc:  # noqa: BLE001 —— 备用失败不拖垮主链路
                    _logger.warning("备用 LLM 客户端构造失败，仅使用主模型: %s", exc)
                    fallback = None

            client = PrimaryFallbackLlmClient(primary=primary, fallback=fallback)
            _logger.info(
                "LLM 客户端工厂: 构造成功 (model=%s, has_fallback=%s)",
                model,
                fallback is not None,
            )
            return client
        except Exception as exc:  # noqa: BLE001 —— 构造失败回落 None（DC-2）
            _logger.warning("LLM 客户端构造失败: %s", exc)
            return None
    except Exception as exc:  # noqa: BLE001 —— 配置读取异常同样回落 None
        _logger.warning("LLM 客户端工厂异常: %s", exc)
        return None
