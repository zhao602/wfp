# =============================================================================
# 治理链六拦截器装配用例（tests/governance/test_governance_chain_six_interceptors.py）
#
# 覆盖验收（roadmap B0/D3，治理链六槽装配 6/6）：
#   1. 六槽全装配：auth/tenant_isolation/audit/quota/budget/metering 均非 None；
#   2. global_scheduler/abac 槽保持 None（装配点移交后续批次）；
#   3. 审计/预算/计量构造失败各自降级 None（链不中断，其余槽不受影响）；
#   4. enabled 开关显式透传（audit_enabled/budget_fail_closed/metering_enabled）；
#   5. 既有行为不回归：rbac/tenant_isolation 均关闭 → 返回 None（降级模式）。
#
# .env 隔离说明：仓库根存在 .env（RBAC_ENABLED=false 等），全部用例经
# monkeypatch governance_factory.get_settings 注入受控 Settings，
# 不依赖也不污染真实 .env。
# =============================================================================

import logging

import pytest

import orchestration_platform.governance.governance_factory as governance_factory
from orchestration_platform.configuration.settings import Settings
from orchestration_platform.governance.interceptors.audit_interceptor import (
    AuditInterceptor,
)
from orchestration_platform.governance.interceptors.budget_interceptor import (
    BudgetInterceptor,
)
from orchestration_platform.governance.interceptors.metering_interceptor import (
    MeteringInterceptor,
)

_STRONG = "s3cure-Random-Secret-Value-32-bytes!"
assert len(_STRONG) >= 32


def _chain_settings(**overrides) -> Settings:
    """构造链装配配置（RBAC/隔离启用 + 三开关启用 + 强密钥）。"""
    base = {
        "environment": "development",
        "rbac_enabled": True,
        "tenant_isolation_enabled": True,
        "metering_enabled": True,
        "budget_fail_closed": True,
        "audit_enabled": True,
        "api_access_token": _STRONG,
        "postgres_password": _STRONG,
        "token_signing_secret": _STRONG,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def isolated_chain_settings(monkeypatch):
    """隔离仓库根 .env：monkeypatch governance_factory.get_settings。"""
    settings = _chain_settings()
    monkeypatch.setattr(governance_factory, "get_settings", lambda: settings)
    return settings


class TestSixInterceptorWiring:
    """六槽装配：create_governance_chain 交付 6/6 拦截器。"""

    def test_chain_wires_six_interceptors(self, isolated_chain_settings) -> None:
        chain = governance_factory.create_governance_chain()
        assert chain is not None
        assert chain._auth is not None
        assert chain._tenant_isolation is not None
        assert chain._audit is not None
        assert chain._quota is not None
        assert chain._budget is not None
        assert chain._metering is not None
        assert not chain.is_degraded_mode

    def test_scheduler_and_abac_slots_remain_none(self, isolated_chain_settings) -> None:
        """global_scheduler/abac 槽保持 None（装配点移交后续批次）。"""
        chain = governance_factory.create_governance_chain()
        assert chain is not None
        assert chain._global_scheduler is None
        assert chain._abac is None

    def test_wired_interceptor_types(self, isolated_chain_settings) -> None:
        chain = governance_factory.create_governance_chain()
        assert chain is not None
        assert isinstance(chain._metering, MeteringInterceptor)
        assert isinstance(chain._budget, BudgetInterceptor)
        assert isinstance(chain._audit, AuditInterceptor)


class TestConstructionDegradation:
    """构造失败降级：单槽失败 → 该槽 None，链仍构造（降级不中断）。"""

    def test_metering_construction_failure_degrades(
        self, isolated_chain_settings, monkeypatch, caplog
    ) -> None:
        import orchestration_platform.governance.interceptors.metering_interceptor as metering_module

        def _boom(*args, **kwargs):
            raise RuntimeError("metering unavailable")

        monkeypatch.setattr(metering_module, "MeteringInterceptor", _boom)
        with caplog.at_level(logging.WARNING):
            chain = governance_factory.create_governance_chain()
        assert chain is not None
        assert chain._metering is None
        assert chain._budget is not None
        assert chain._audit is not None
        assert "计量拦截器构造失败" in caplog.text

    def test_budget_construction_failure_degrades(
        self, isolated_chain_settings, monkeypatch, caplog
    ) -> None:
        import orchestration_platform.governance.interceptors.budget_interceptor as budget_module

        def _boom(*args, **kwargs):
            raise RuntimeError("budget unavailable")

        monkeypatch.setattr(budget_module, "BudgetInterceptor", _boom)
        with caplog.at_level(logging.WARNING):
            chain = governance_factory.create_governance_chain()
        assert chain is not None
        assert chain._budget is None
        assert chain._metering is not None
        assert chain._audit is not None
        assert "预算拦截器构造失败" in caplog.text

    def test_audit_construction_failure_degrades(
        self, isolated_chain_settings, monkeypatch, caplog
    ) -> None:
        import orchestration_platform.governance.interceptors.audit_interceptor as audit_module

        def _boom(*args, **kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr(audit_module, "AuditInterceptor", _boom)
        with caplog.at_level(logging.WARNING):
            chain = governance_factory.create_governance_chain()
        assert chain is not None
        assert chain._audit is None
        assert chain._budget is not None
        assert chain._metering is not None
        assert "审计拦截器构造失败" in caplog.text


class TestEnabledFlagPropagation:
    """enabled 开关显式透传（构造不再隐式读配置）。"""

    def test_metering_disabled_flag_propagates(self, monkeypatch) -> None:
        settings = _chain_settings(metering_enabled=False)
        monkeypatch.setattr(governance_factory, "get_settings", lambda: settings)
        chain = governance_factory.create_governance_chain()
        assert chain is not None
        assert chain._metering is not None
        assert chain._metering._enabled is False

    def test_budget_disabled_flag_propagates(self, monkeypatch) -> None:
        settings = _chain_settings(budget_fail_closed=False)
        monkeypatch.setattr(governance_factory, "get_settings", lambda: settings)
        chain = governance_factory.create_governance_chain()
        assert chain is not None
        assert chain._budget is not None
        assert chain._budget._enabled is False

    def test_audit_disabled_flag_propagates(self, monkeypatch) -> None:
        settings = _chain_settings(audit_enabled=False)
        monkeypatch.setattr(governance_factory, "get_settings", lambda: settings)
        chain = governance_factory.create_governance_chain()
        assert chain is not None
        assert chain._audit is not None
        assert chain._audit._enabled is False


class TestDegradedModeGate:
    """既有行为不回归：rbac/tenant_isolation 均关闭 → 返回 None。"""

    def test_both_disabled_returns_none(self, monkeypatch) -> None:
        settings = _chain_settings(
            rbac_enabled=False, tenant_isolation_enabled=False
        )
        monkeypatch.setattr(governance_factory, "get_settings", lambda: settings)
        assert governance_factory.create_governance_chain() is None
