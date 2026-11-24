# =============================================================================
# LLM 客户端工厂与集成测试（tests/unit/test_build_llm_client.py）
#
# 覆盖 fix_agent_executor_llm_client TC-12~TC-22：
#   TC-12 配置完整→非None / TC-13~15 配置缺失→None
#   TC-16 fallback完整 / TC-17 fallback缺失 / TC-18 fallback构造异常→仍返回primary
#   TC-20 日志不含api_key
#   TC-21 Agent执行器注册成功 / TC-22 Agent执行器注册跳过
#
# 根因组 A（fix_baseline_test_failures）：patch 目标
# execution_instance_router.get_settings 不存在（实现为函数内延迟导入），
# 统一替换为 configuration.settings.get_settings（FR-01 / INV-13）。
# TC-19 无对应测试函数，按 spec 开放问题 4 确认不补充。
#
# 沙箱限制：pytest 无法安装，本文件作为交付物编写，待 CI 执行。
# =============================================================================

from unittest.mock import MagicMock, patch


from orchestration_platform.integrations.llm.primary_fallback_llm_client import (
    PrimaryFallbackLlmClient,
)


def _mock_settings(base_url="http://localhost:8080", api_key="sk-key", model="gpt-4",
                   fb_url="", fb_key="", fb_model=""):
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


class TestBuildLlmClientConfigComplete:
    """TC-12：配置完整 → 非 None"""

    def test_t12_config_complete_returns_client(self):
        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=_mock_settings(),
        ):
            from orchestration_platform.api.routers.execution_instance_router import _build_llm_client
            client = _build_llm_client()
        assert client is not None
        assert isinstance(client, PrimaryFallbackLlmClient)


class TestBuildLlmClientConfigMissing:
    """TC-13~TC-15：配置缺失 → None"""

    def test_t13_base_url_missing(self):
        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=_mock_settings(base_url=""),
        ):
            from orchestration_platform.api.routers.execution_instance_router import _build_llm_client
            assert _build_llm_client() is None

    def test_t14_api_key_missing(self):
        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=_mock_settings(api_key=""),
        ):
            from orchestration_platform.api.routers.execution_instance_router import _build_llm_client
            assert _build_llm_client() is None

    def test_t15_model_missing(self):
        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=_mock_settings(model=""),
        ):
            from orchestration_platform.api.routers.execution_instance_router import _build_llm_client
            assert _build_llm_client() is None


class TestBuildLlmClientFallback:
    """TC-16~TC-18：备用客户端构造"""

    def test_t16_fallback_complete(self):
        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=_mock_settings(
                fb_url="http://fb:8080", fb_key="sk-fb", fb_model="gpt-3.5"
            ),
        ):
            from orchestration_platform.api.routers.execution_instance_router import _build_llm_client
            client = _build_llm_client()
        assert client is not None
        assert client._fallback is not None

    def test_t17_fallback_missing(self):
        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=_mock_settings(),
        ):
            from orchestration_platform.api.routers.execution_instance_router import _build_llm_client
            client = _build_llm_client()
        assert client is not None
        assert client._fallback is None

    def test_t18_fallback_construction_error(self):
        settings = _mock_settings(
            fb_url="http://fb:8080", fb_key="sk-fb", fb_model="gpt-3.5"
        )
        original_init = None

        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=settings,
        ):
            from orchestration_platform.integrations.llm.http_llm_client import HttpLlmClient
            original_init = HttpLlmClient.__init__

            def faulty_init(self, base_url, api_key, model, timeout_seconds=60.0):
                if "fb" in base_url:
                    raise RuntimeError("fallback construction failed")
                original_init(self, base_url, api_key, model, timeout_seconds)

            with patch.object(HttpLlmClient, "__init__", faulty_init):
                from orchestration_platform.api.routers.execution_instance_router import _build_llm_client
                client = _build_llm_client()

        assert client is not None
        assert client._fallback is None


class TestBuildLlmClientLogging:
    """TC-20：日志不含 api_key"""

    def test_t20_api_key_not_in_logs(self, caplog):
        secret = "sk-super-secret-key-99999"
        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=_mock_settings(api_key=secret),
        ):
            with caplog.at_level("INFO"):
                from orchestration_platform.api.routers.execution_instance_router import _build_llm_client
                _build_llm_client()

        for record in caplog.records:
            assert secret not in record.getMessage()


class TestAgentExecutorRegistration:
    """TC-21/TC-22：Agent 执行器注册"""

    def test_t21_agent_executor_registered(self):
        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=_mock_settings(),
        ):
            from orchestration_platform.api.routers.execution_instance_router import _build_executor_registry
            registry = _build_executor_registry()
        assert registry is not None
        assert registry.has_executor("agent") or "agent" in [t.value for t in registry.registered_types()]

    def test_t22_agent_executor_skipped(self):
        with patch(
            "orchestration_platform.configuration.settings.get_settings",
            return_value=_mock_settings(api_key="", model=""),
        ):
            from orchestration_platform.api.routers.execution_instance_router import _build_executor_registry
            registry = _build_executor_registry()
        if registry is not None:
            assert not registry.has_executor("agent")