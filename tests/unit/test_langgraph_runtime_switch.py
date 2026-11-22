# =============================================================================
# LangGraph 第二运行时开关三态单测（tests/unit/test_langgraph_runtime_switch.py）
#
# 覆盖验收：arch_review_gap_b4_architecture AC-B4-02（DC-B4-03/HC-07/INV-B4-03）：
#   态一 False（默认，零变化）/ 态二 True + 依赖在位（可构造第二运行时）/
#   态三 True + 依赖缺失（降级日志 + None，不伪造等价结论）。
# =============================================================================

import logging

import pytest

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.api.routers import execution_instance_router
from orchestration_platform.configuration.settings import Settings, get_settings
from orchestration_platform.engine.adapter.langgraph_adapter import (
    LangGraphAdapter,
    is_langgraph_available,
)


class TestLangGraphRuntimeSwitch:
    """开关三态（作用面：容器 langgraph_engine_factory；生产 start_instance 零改）。"""

    def test_settings_default_off(self):
        """态一（默认 False）：settings 字段默认关闭（HC-07/GK-11）。"""
        assert Settings(_env_file=None).langgraph_runtime_enabled is False

    def test_singleton_settings_default_off(self):
        """态一（默认 False）：运行期单例同样关闭（容器 provider 读取源）。"""
        assert get_settings().langgraph_runtime_enabled is False

    def test_container_factory_none_when_off(self):
        """态一（默认 False）：容器 provider resolve 为 None（零开销零变化）。"""
        app = create_app()
        assert app.state.container.resolve("langgraph_engine_factory") is None

    def test_factory_constructs_adapter_when_on_and_available(self, monkeypatch):
        """态二（True + 依赖在位）：工厂产出 LangGraphAdapter（生产同源注册表）。"""
        monkeypatch.setattr(get_settings(), "langgraph_runtime_enabled", True)
        if not is_langgraph_available():
            pytest.skip("langgraph 依赖不可用：态二本环境不可达（矩阵 skip 口径）")
        app = create_app()
        factory = app.state.container.resolve("langgraph_engine_factory")
        assert factory is not None
        engine = factory()
        assert isinstance(engine, LangGraphAdapter)
        # 节点语义单源（INV-B4-01）：与内置引擎同一注册表实例
        assert engine._registry is execution_instance_router._executor_registry

    def test_factory_degrades_when_dependency_missing(self, monkeypatch, caplog):
        """态三（True + 依赖缺失）：降级告警 + None，不伪造等价（INV-B4-03）。"""
        monkeypatch.setattr(get_settings(), "langgraph_runtime_enabled", True)
        monkeypatch.setattr(
            "orchestration_platform.engine.adapter.langgraph_adapter"
            ".is_langgraph_available",
            lambda: False,
        )
        app = create_app()
        factory = app.state.container.resolve("langgraph_engine_factory")
        assert factory is not None
        with caplog.at_level(logging.WARNING):
            engine = factory()
        assert engine is None
        assert "langgraph" in caplog.text
