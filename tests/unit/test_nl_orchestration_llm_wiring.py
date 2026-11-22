# =============================================================================
# NL 编排 LLM 接线测试（tests/unit/test_nl_orchestration_llm_wiring.py）
#
# 回归背景：nl_orchestration_router 曾以 PrimaryFallbackLlmClient() 无参构造
# （primary 必填）→ 首次调用即 TypeError 500，NL 编排完全不可用。
#
# 覆盖：
#   NL-W01 工厂配置完整 → 返回主备客户端（构造不再抛 TypeError）
#   NL-W02 工厂配置缺失 → 返回 None（降级契约，不抛异常）
#   NL-W03 配置完整 → _get_nl_service() 返回服务实例且解析器持有该客户端
#   NL-W04 配置缺失 → _get_nl_service() 返回 None，不抛异常
#   NL-W05 服务不可用 → create 端点返回 llm_not_configured 而非 500
# =============================================================================

from unittest.mock import MagicMock, patch

import pytest

from orchestration_platform.api.routers import nl_orchestration_router as nl_router
from orchestration_platform.integrations.llm.primary_fallback_llm_client import (
    PrimaryFallbackLlmClient,
)

_SETTINGS_PATCH = "orchestration_platform.configuration.settings.get_settings"


def _mock_settings(
    base_url: str = "http://localhost:8080",
    api_key: str = "sk-key",
    model: str = "gpt-4",
    fb_url: str = "",
    fb_key: str = "",
    fb_model: str = "",
) -> MagicMock:
    settings = MagicMock()
    settings.llm_primary_base_url = base_url
    settings.llm_primary_api_key = api_key
    settings.llm_primary_model = model
    settings.llm_primary_timeout_seconds = 60
    settings.llm_fallback_base_url = fb_url
    settings.llm_fallback_api_key = fb_key
    settings.llm_fallback_model = fb_model
    settings.llm_fallback_timeout_seconds = 60
    return settings


@pytest.fixture(autouse=True)
def _reset_nl_service():
    """每个用例重置模块级服务单例，避免用例间串味。"""
    nl_router._nl_service = None
    yield
    nl_router._nl_service = None


class TestFactoryContract:
    """NL-W01 / NL-W02：工厂返回契约。"""

    def test_w01_config_complete_returns_client(self):
        with patch(_SETTINGS_PATCH, return_value=_mock_settings()):
            from orchestration_platform.integrations.llm.factory import build_llm_client

            client = build_llm_client()
        assert isinstance(client, PrimaryFallbackLlmClient)

    @pytest.mark.parametrize(
        "kwargs",
        [{"base_url": ""}, {"api_key": ""}, {"model": ""}],
        ids=["base_url_missing", "api_key_missing", "model_missing"],
    )
    def test_w02_config_missing_returns_none(self, kwargs):
        with patch(_SETTINGS_PATCH, return_value=_mock_settings(**kwargs)):
            from orchestration_platform.integrations.llm.factory import build_llm_client

            assert build_llm_client() is None


class TestNlServiceWiring:
    """NL-W03 / NL-W04：NL 服务装配与降级。"""

    def test_w03_service_built_with_real_client(self):
        with patch(_SETTINGS_PATCH, return_value=_mock_settings()):
            service = nl_router._get_nl_service()
        assert service is not None
        # 解析器持有工厂产出的客户端（而非无参构造的空壳）
        assert isinstance(service._intent_parser._llm_client, PrimaryFallbackLlmClient)

    def test_w04_service_none_when_llm_missing(self):
        with patch(_SETTINGS_PATCH, return_value=_mock_settings(model="")):
            service = nl_router._get_nl_service()
        assert service is None


class TestEndpointDegradation:
    """NL-W05：服务不可用时端点返回明确错误码（非 500）。"""

    @pytest.mark.asyncio
    async def test_w05_create_returns_llm_not_configured(self):
        request = nl_router.CreateFromNlRequest(
            directive="创建一个先检索再摘要的工作流",
            tenant_id="tenant_1",
            user_id="user_1",
        )
        with patch(_SETTINGS_PATCH, return_value=_mock_settings(api_key="")):
            response = await nl_router.create_from_nl(request)
        assert response.success is False
        assert response.error_code == "llm_not_configured"
        assert "LLM_PRIMARY_API_KEY" in response.error_message

    def test_w05b_degradation_response_shape(self):
        response = nl_router._llm_not_configured_response()
        assert response.success is False
        assert response.error_code == "llm_not_configured"
        assert response.yaml_text is None
        assert response.validation_issues == []
