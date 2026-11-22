# =============================================================================
# 配额闸门与 429 契约测试（tests/governance/test_quota_gate_429.py，roadmap B4）
#
# 覆盖（design §8 测试矩阵）：
#   t1 超配额 429 拒绝未执行（三验收之一：超配额 100% 拒绝且未执行）；
#   t2 count_active_by_tenant 并发计数口径（D1：非终态计数/终态释放/跨租户/None 不计）；
#   t3 get_quota 三级回退（仓储命中 → 仓储 None/异常 → settings 默认）；
#   t4 429 handler 映射（异常类精确注册，MRO 优先于 DomainError 500 兜底）。
# =============================================================================

from types import SimpleNamespace

import pytest

from orchestration_platform.api.routers.execution_instance_router import (
    InstanceRegistry,
    InstanceRuntime,
    StartInstanceBody,
    start_instance,
)
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    TENANT_QUOTA_EXCEEDED,
    TenantQuotaExceededError,
)
from orchestration_platform.governance.tenant.models import TenantQuota
from orchestration_platform.governance.tenant.quota_gate import (
    TenantQuotaGate,
    set_quota_gate,
)


class _StubQuotaProvider:
    """桩配额源：返回固定配额（None 模拟仓储未配置；可选抛异常）。"""

    def __init__(self, quota=None, raise_on_get: bool = False):
        self._quota = quota
        self._raise_on_get = raise_on_get

    async def get_quota(self, tenant_id: str):
        if self._raise_on_get:
            raise RuntimeError("stub provider failure")
        return self._quota


@pytest.fixture(autouse=True)
def _reset_module_state():
    """隔离模块级注入（闸门/记账）与注册表，防跨测试泄漏。"""
    from orchestration_platform.api.routers import execution_instance_router as _router_mod
    from orchestration_platform.governance.metering import usage_recorder as _recorder_mod
    from orchestration_platform.governance.tenant import quota_gate as _gate_mod

    _gate_mod.set_quota_gate(None)
    _recorder_mod.set_usage_recorder(None)
    yield
    _gate_mod.set_quota_gate(None)
    _recorder_mod.set_usage_recorder(None)
    _router_mod._instance_registry._instances.clear()


async def test_t1_over_quota_rejected_not_executed():
    """t1 超配额 429 拒绝且未执行：并发达上限时拒绝，注册表零新增。"""
    from orchestration_platform.api.routers import execution_instance_router as router_mod

    registry = router_mod._instance_registry
    # 预置 2 个并发活跃实例（tenant-a，pending 非终态）
    for i in range(2):
        registry.register(
            InstanceRuntime(
                instance_id=f"pre-active-{i}",
                definition_id="wf-x",
                tenant_id="tenant-a",
            )
        )

    # 注入闸门：并发上限 2（当前并发 2 → 拒绝）
    set_quota_gate(
        TenantQuotaGate(
            quota_provider=_StubQuotaProvider(
                TenantQuota(tenant_id="tenant-a", max_instances=2, max_workflows=10)
            )
        )
    )

    request = SimpleNamespace(state=SimpleNamespace(tenant_id="tenant-a"))
    body = StartInstanceBody(definition_id="wf-quota", yaml_text="a: b: c")

    with pytest.raises(TenantQuotaExceededError) as exc_info:
        await start_instance(body, request)

    # 429 契约结构断言（code/details 三元组）
    exc = exc_info.value
    assert exc.code == TENANT_QUOTA_EXCEEDED
    assert exc.details["tenant_id"] == "tenant-a"
    assert exc.details["active_instances"] == 2
    assert exc.details["max_instances"] == 2

    # 未执行：注册表零新增（仍为预置 2 个）；未编译：闸门先于 YAML 解析
    # （若闸门失效，非法 YAML 会以 422 解析失败抛出而非本异常）
    assert len(registry._instances) == 2


def test_t2_count_active_by_tenant():
    """t2 并发计数口径：非终态计入、终态释放、跨租户隔离、None 租户不计数。"""
    registry = InstanceRegistry()
    rt_pending = InstanceRuntime("i-1", "wf", tenant_id="tenant-a")
    rt_running = InstanceRuntime("i-2", "wf", tenant_id="tenant-a")
    rt_running.status = "running"
    rt_done = InstanceRuntime("i-3", "wf", tenant_id="tenant-a")
    rt_done.status = "succeeded"
    rt_review = InstanceRuntime("i-4", "wf", tenant_id="tenant-b")
    rt_review.status = "waiting_review"
    rt_none = InstanceRuntime("i-5", "wf")
    for rt in (rt_pending, rt_running, rt_done, rt_review, rt_none):
        registry.register(rt)

    # tenant-a：pending + running 活跃（succeeded 已释放）；tenant-b：review 活跃
    assert registry.count_active_by_tenant("tenant-a") == 2
    assert registry.count_active_by_tenant("tenant-b") == 1
    assert registry.count_active_by_tenant("tenant-c") == 0
    # None 租户不计数（cron/治理降级不占槽）
    assert registry.count_active_by_tenant(None) == 0

    # 终态释放口径：pending → failed 释放
    rt_pending.status = "failed"
    assert registry.count_active_by_tenant("tenant-a") == 1


async def test_t3_quota_source_fallback_chain():
    """t3 配额源三级回退：仓储命中 → 仓储 None/异常 → settings 默认。"""
    settings = get_settings()
    fallback_instances = settings.tenant_default_quota_max_instances
    fallback_workflows = settings.tenant_default_quota_max_workflows

    # ① 仓储命中
    gate_hit = TenantQuotaGate(
        quota_provider=_StubQuotaProvider(
            TenantQuota(tenant_id="t", max_instances=7, max_workflows=70)
        )
    )
    quota = await gate_hit.get_quota("t")
    assert quota.max_instances == 7
    assert quota.max_workflows == 70

    # ② 仓储未配置 / 仓储异常 → settings 默认回退
    gate_miss = TenantQuotaGate(quota_provider=_StubQuotaProvider(None))
    quota = await gate_miss.get_quota("t")
    assert quota.max_instances == fallback_instances
    assert quota.max_workflows == fallback_workflows

    gate_err = TenantQuotaGate(quota_provider=_StubQuotaProvider(raise_on_get=True))
    quota = await gate_err.get_quota("t")
    assert quota.max_instances == fallback_instances

    # ③ 无 provider → settings 默认；ensure_capacity 未达放行/达限拒绝
    gate_bare = TenantQuotaGate()
    quota = await gate_bare.get_quota("t")
    assert quota.max_instances == fallback_instances
    assert quota.max_workflows == fallback_workflows

    await gate_bare.ensure_capacity("t", 0)
    with pytest.raises(TenantQuotaExceededError):
        await gate_bare.ensure_capacity("t", fallback_instances)


async def test_t4_429_handler_mapping():
    """t4 429 handler 映射：异常类精确注册 → 429 + 统一错误结构。"""
    from orchestration_platform.api.application_factory import create_app

    app = create_app()
    handler = app.exception_handlers.get(TenantQuotaExceededError)
    assert handler is not None, "TenantQuotaExceededError handler 未注册"

    exc = TenantQuotaExceededError("tenant-a", 100, 100)
    response = await handler(SimpleNamespace(), exc)
    assert response.status_code == 429

    import json as _json

    payload = _json.loads(response.body)
    assert payload["code"] == TENANT_QUOTA_EXCEEDED
    assert payload["details"]["active_instances"] == 100
    assert payload["details"]["max_instances"] == 100
    assert payload["details"]["tenant_id"] == "tenant-a"
