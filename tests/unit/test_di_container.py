# =============================================================================
# DI 容器单测（tests/unit/test_di_container.py）
#
# 覆盖验收：arch_review_gap_b4_architecture AC-B4-01（容器装配单测——
# provider 解析顺序 / 配置切换 / 重复装配幂等 / 冻结只读语义）。
# 范围：Container 机制层；application_factory 装配面见
# tests/unit/test_application_factory_container.py。
# =============================================================================

import pytest

from orchestration_platform.api.di import Container


class TestContainerMechanics:
    """Container 机制：注册/解析/记忆化/冻结/顺序。"""

    def test_register_and_resolve_invokes_factory_once(self):
        """resolve 惰性构造且工厂仅执行一次（单例记忆化）。"""
        container = Container()
        calls = []

        def _factory():
            calls.append(1)
            return object()

        container.register("dep", _factory)
        first = container.resolve("dep")
        second = container.resolve("dep")
        assert first is second
        assert len(calls) == 1

    def test_resolve_unknown_key_raises_key_error(self):
        """未注册键解析抛 KeyError。"""
        container = Container()
        with pytest.raises(KeyError):
            container.resolve("missing")

    def test_register_rejects_non_callable(self):
        """非可调用 provider 注册抛 TypeError。"""
        container = Container()
        with pytest.raises(TypeError):
            container.register("bad", "not-a-factory")

    def test_keys_preserves_registration_order(self):
        """keys() 保持注册顺序（解析顺序确定性锚点）。"""
        container = Container()
        container.register("b", lambda: 1)
        container.register("a", lambda: 2)
        container.register("c", lambda: 3)
        assert container.keys() == ["b", "a", "c"]

    def test_has_reflects_registration_state(self):
        """has() 与注册状态一致。"""
        container = Container()
        container.register("present", lambda: 1)
        assert container.has("present") is True
        assert container.has("absent") is False

    def test_freeze_blocks_registration_and_is_idempotent(self):
        """冻结后禁止注册；freeze 幂等；冻结后 resolve 行为不变。"""
        container = Container()
        container.register("dep", lambda: 42)
        container.freeze()
        container.freeze()  # 幂等
        with pytest.raises(RuntimeError):
            container.register("late", lambda: 1)
        assert container.resolve("dep") == 42

    def test_instance_registration_via_closure(self):
        """现成实例以零参闭包注册后解析身份一致。"""
        container = Container()
        sentinel = object()
        container.register("instance", lambda: sentinel)
        assert container.resolve("instance") is sentinel

    def test_config_switch_returns_respective_provider_result(self):
        """配置切换口径：不同配置 provider 注册后各自解析（判定沿 provider 内委托）。"""
        container = Container()
        container.register("impl", lambda: "postgres-impl")
        assert container.resolve("impl") == "postgres-impl"

        other = Container()
        other.register("impl", lambda: "memory-impl")
        assert other.resolve("impl") == "memory-impl"
