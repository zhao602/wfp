# =============================================================================
# 引擎装配 wiring 测试（tests/unit/test_engine_wiring.py）
#
# 职责（roadmap M4 B10 / design D10）：生产装配注入断言——env → Settings →
# build_engine_settings → 组件 ctor 全链不静默失效（计划风险 1 缓解）。
# 覆盖：
#   1. env 特征值法：WORKFLOW_MAX_STEPS=777 → builtin_engine_factory 产
#      Scheduler._settings.workflow_max_steps == 777（env→Settings→wiring→
#      组件全链透传，M1 身份缝先例）；
#   2. 计量注入：metering 装配 on（默认 True）→ 生产 Scheduler.
#      _usage_recorder is get_usage_recorder() 且非 None（防计费静默丢失）；
#   3. 注册表身份：容器 executor_registry is 路由模块级 _executor_registry
#      （生产同源，INV-B4-01 身份缝）；
#   4. 第二运行时全链：langgraph_runtime_enabled on → adapter settings
#      特征值透传 + 注册表身份。
# =============================================================================

import pytest

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.metering.usage_recorder import (
    get_usage_recorder,
    set_usage_recorder,
)


def _reset_settings_cache():
    """失效 get_settings 缓存（env 覆盖生效/还原的统一动作）。"""
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _engine_wiring_env(monkeypatch):
    """env 特征值 + settings 缓存隔离；还原计量全局态防跨用例泄漏。"""
    monkeypatch.setenv("WORKFLOW_MAX_STEPS", "777")
    _reset_settings_cache()
    yield
    _reset_settings_cache()
    set_usage_recorder(None)


class TestEngineWiringInjection:
    """生产装配注入断言（design D10）。"""

    def test_env_feature_value_reaches_builtin_scheduler(self):
        """特征值全链：WORKFLOW_MAX_STEPS=777 → builtin Scheduler._settings。"""
        app = create_app()
        engine = app.state.container.resolve("builtin_engine_factory")()
        assert engine._settings.workflow_max_steps == 777

    def test_metering_recorder_injected_into_builtin_scheduler(self):
        """计量防丢：metering 装配 on（默认）→ Scheduler._usage_recorder
        is get_usage_recorder() 且非 None。"""
        app = create_app()
        engine = app.state.container.resolve("builtin_engine_factory")()
        recorder = get_usage_recorder()
        assert recorder is not None, "metering_enabled 默认 True，装配应已 set"
        assert engine._usage_recorder is recorder

    def test_executor_registry_identity(self):
        """注册表身份缝：容器注册表 is 路由模块级生产注册表（INV-B4-01）。"""
        from orchestration_platform.api.routers import execution_instance_router

        app = create_app()
        assert app.state.container.resolve("executor_registry") is (
            execution_instance_router._executor_registry
        )

    def test_langgraph_adapter_settings_and_registry(self, monkeypatch):
        """第二运行时全链：开关 on → adapter settings 特征值透传 +
        注册表身份。"""
        monkeypatch.setenv("LANGGRAPH_RUNTIME_ENABLED", "true")
        _reset_settings_cache()
        app = create_app()
        factory = app.state.container.resolve("langgraph_engine_factory")
        assert factory is not None, "开关 on + langgraph 在位 → 工厂非 None"
        adapter = factory()
        assert adapter is not None
        assert adapter._settings.workflow_max_steps == 777
        from orchestration_platform.api.routers import execution_instance_router

        assert adapter._registry is execution_instance_router._executor_registry
