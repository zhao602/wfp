# =============================================================================
# 租户配额闸门（governance/tenant/quota_gate.py）
#
# 职责：POST /instances 并发活跃实例配额校验（roadmap B4，DC-B4-02）。
# 设计意图：
#   1. 并发口径（D1 裁决）：非终态实例计数（pending/running/waiting_review），
#      终态（succeeded/failed/rolled_back/cancelled）释放槽位；
#   2. 配额源三级回退：仓储 get_quota → settings 默认配额 → 内置兜底；
#   3. 原子性：get_quota 为唯一 await 点，count→check→raise→register 全同步块，
#      事件循环单线程内无并发穿插——超配额 100% 拒绝保证；
#   4. 拒绝零副作用：raise 先于实例注册/编译/执行（映射 HTTP 429）。
# =============================================================================

from typing import Optional, Protocol

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import TenantQuotaExceededError
from orchestration_platform.governance.tenant.models import TenantQuota


class QuotaProvider(Protocol):
    """配额数据源协议：双后端租户仓储均满足（get_quota 签名一致）。"""

    async def get_quota(self, tenant_id: str) -> Optional[TenantQuota]:
        """查询租户配额；未配置返回 None。"""
        ...


class TenantQuotaGate:
    """租户并发实例配额闸门。

    start_instance 在实例注册前调用 ensure_capacity：当前并发活跃实例数
    达到配额上限即抛 TenantQuotaExceededError（HTTP 429，实例未注册未执行）。
    """

    def __init__(self, quota_provider: Optional[QuotaProvider] = None) -> None:
        """构造闸门。

        参数：
            quota_provider —— 配额数据源（双后端租户仓储）；None 时直接走
                              settings 默认回退。
        """
        self._quota_provider = quota_provider

    async def get_quota(self, tenant_id: str) -> TenantQuota:
        """查询租户配额（三级回退）。

        ① quota_provider.get_quota 命中即返回；
        ② 未命中/数据源异常 → settings 默认配额键构造；
        ③ settings 属性缺失 → 内置兜底 100/1000。
        """
        if self._quota_provider is not None:
            try:
                quota = await self._quota_provider.get_quota(tenant_id)
            except Exception:
                quota = None
            if quota is not None:
                return quota

        settings = get_settings()
        try:
            max_instances = settings.tenant_default_quota_max_instances
        except AttributeError:
            max_instances = 100
        try:
            max_workflows = settings.tenant_default_quota_max_workflows
        except AttributeError:
            max_workflows = 1000
        return TenantQuota(
            tenant_id=tenant_id,
            max_instances=max_instances,
            max_workflows=max_workflows,
        )

    async def ensure_capacity(
        self, tenant_id: str, active_count: int
    ) -> None:
        """校验并发活跃实例容量：达到上限即抛 TenantQuotaExceededError。

        参数：
            tenant_id     —— 租户标识
            active_count  —— 当前并发活跃实例数（调用方同步计数）
        """
        quota = await self.get_quota(tenant_id)
        if active_count >= quota.max_instances:
            raise TenantQuotaExceededError(
                tenant_id=tenant_id,
                active_instances=active_count,
                max_instances=quota.max_instances,
            )


# ---------------------------------------------------------------------------
# 模块注入点（set_domain_event_publisher 先例：装配期注入，路由期取用）
# ---------------------------------------------------------------------------

_quota_gate: Optional[TenantQuotaGate] = None


def set_quota_gate(gate: Optional[TenantQuotaGate]) -> None:
    """注入配额闸门（application_factory 装配期调用）。"""
    global _quota_gate
    _quota_gate = gate


def get_quota_gate() -> Optional[TenantQuotaGate]:
    """取用配额闸门；未装配返回 None（闸门跳过，治理降级口径）。"""
    return _quota_gate
