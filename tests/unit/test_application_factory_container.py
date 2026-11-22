# =============================================================================
# application_factory 容器装配单测（tests/unit/test_application_factory_container.py）
#
# 覆盖验收：arch_review_gap_b4_architecture AC-B4-01（装配段迁移——
# provider 解析顺序 / 配置切换沿用 repository_factory 判定 / 重复装配幂等 /
# create_app 签名与对外行为零变化 GK-14）。
# =============================================================================

import pytest

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.api.routers import execution_instance_router

# 装配段注册序（DC-B4-01 P1~P9 减去条件注册的 domain_event_publisher）
_EXPECTED_PROVIDER_ORDER = [
    "settings",
    "metrics",
    "llm_client",
    "executor_registry",
    "workflow_repository",
    "governance_chain",
    "builtin_engine_factory",
    "langgraph_engine_factory",
]


class TestContainerAssembly:
    """create_app 容器装配面。"""

    def test_app_state_container_in_place_and_frozen(self):
        """app.state.container 在位且装配完成已冻结（只读）。"""
        app = create_app()
        container = app.state.container
        assert container is not None
        with pytest.raises(RuntimeError):
            container.register("late", lambda: 1)

    def test_provider_registration_order_deterministic(self):
        """provider 注册序固定（解析顺序确定性锚点）。"""
        app = create_app()
        keys = app.state.container.keys()
        assert keys[: len(_EXPECTED_PROVIDER_ORDER)] == _EXPECTED_PROVIDER_ORDER

    def test_settings_provider_identity_with_get_settings(self):
        """settings provider 与 get_settings() 同一实例（配置单源）。"""
        from orchestration_platform.configuration.settings import get_settings

        app = create_app()
        assert app.state.container.resolve("settings") is get_settings()

    def test_executor_registry_provider_identity_with_router_module(self):
        """executor_registry provider 返回路由模块级单例（生产同源，INV-B4-01）。"""
        app = create_app()
        resolved = app.state.container.resolve("executor_registry")
        assert resolved is execution_instance_router._executor_registry

    def test_repeated_resolve_is_idempotent(self):
        """重复 resolve 幂等（单例记忆化）。"""
        app = create_app()
        container = app.state.container
        assert container.resolve("executor_registry") is container.resolve(
            "executor_registry"
        )

    def test_langgraph_engine_factory_default_off(self):
        """langgraph_runtime_enabled 默认 False：工厂 resolve 为 None（HC-07）。"""
        app = create_app()
        assert app.state.container.resolve("langgraph_engine_factory") is None

    def test_builtin_engine_factory_constructs_scheduler(self):
        """内置引擎工厂调用即构造 Scheduler（harness 面开放，生产路径零改）。"""
        from orchestration_platform.engine.scheduler import Scheduler

        app = create_app()
        factory = app.state.container.resolve("builtin_engine_factory")
        engine = factory()
        assert isinstance(engine, Scheduler)
        assert engine._registry is execution_instance_router._executor_registry

    def test_workflow_repository_provider_resolves_to_repository(self):
        """workflow_repository provider 按 repository_factory 判定口径解析成功。"""
        from orchestration_platform.persistence.abstractions.repositories import (
            IWorkflowRepository,
        )

        app = create_app()
        repository = app.state.container.resolve("workflow_repository")
        assert isinstance(repository, IWorkflowRepository)
