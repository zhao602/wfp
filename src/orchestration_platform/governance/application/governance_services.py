# =============================================================================
# 治理应用服务（governance/application/governance_services.py）
#
# 职责：封装治理各模块的应用服务，供 REST API 路由调用。
# 设计意图（对应 spec 5.5、design 2.8、任务 T72）：
#   1. 应用服务接口与实现分离，依赖领域组件而非具体实现；
#   2. 服务方法含前置条件与后置条件注释。
# =============================================================================

from datetime import datetime
from typing import Any, Dict, List, Optional

from orchestration_platform.governance.audit.audit_logger import AuditLogger
from orchestration_platform.governance.audit.compliance_exporter import (
    ComplianceExporter,
)
from orchestration_platform.governance.audit.mask_engine import MaskEngine
from orchestration_platform.governance.audit.models import AuditLogEntry
from orchestration_platform.governance.billing.budget_manager import BudgetManager
from orchestration_platform.governance.billing.cost_calculator import CostCalculator
from orchestration_platform.governance.billing.pricing_rule_manager import (
    PricingRuleManager,
)
from orchestration_platform.governance.metering.token_meter import TokenMeter
from orchestration_platform.governance.persistence.abstractions.audit_log_repository import (
    IAuditLogRepository,
)
from orchestration_platform.governance.persistence.abstractions.token_usage_repository import (
    ITokenUsageRepository,
)
from orchestration_platform.governance.rbac.permission_checker import (
    RbacPermissionChecker,
)
from orchestration_platform.governance.rbac.permission_registry import (
    PermissionRegistry,
    create_default_registry,
)
from orchestration_platform.governance.resource.resource_pool import (
    ResourcePoolManager,
)
from orchestration_platform.governance.scheduler.global_scheduler import (
    GlobalScheduler,
)
from orchestration_platform.governance.tenant.quota_checker import QuotaChecker
from orchestration_platform.governance.tenant.tenant_isolator import TenantIsolator
from orchestration_platform.governance.tenant.token_manager import TokenManager


class GovernanceServices:
    """治理应用服务聚合。

    封装全部治理模块的应用服务，供 REST API 路由调用。
    """

    def __init__(self, audit_repository: Optional[IAuditLogRepository] = None) -> None:
        """初始化治理服务聚合。

        Args:
            audit_repository: 审计日志仓储（可选注入；缺省惰性获取，
                见 _resolve_audit_repository，CHG-64）。
        """
        # RBAC
        self.permission_registry: PermissionRegistry = create_default_registry()
        from orchestration_platform.governance.rbac.permission_cache import (
            PermissionCache,
        )
        self.permission_cache = PermissionCache()
        self.permission_checker = RbacPermissionChecker(
            registry=self.permission_registry,
            cache=self.permission_cache,
        )

        # 租户
        self.tenant_isolator = TenantIsolator()
        self.quota_checker = QuotaChecker()
        self.token_manager = TokenManager()

        # 计量与计费
        self.token_meter = TokenMeter()
        self.pricing_rule_manager = PricingRuleManager()
        self.cost_calculator = CostCalculator(self.pricing_rule_manager)
        self.budget_manager = BudgetManager()

        # 审计
        self.audit_logger = AuditLogger()
        self.mask_engine = MaskEngine()
        self.compliance_exporter = ComplianceExporter(self.mask_engine)
        self._audit_repository = audit_repository

        # 调度与资源
        self.resource_pool_manager = ResourcePoolManager()
        self.resource_pool_manager.init_default_pools()
        self.global_scheduler = GlobalScheduler(
            pool_manager=self.resource_pool_manager
        )

    # ----- 租户管理 -----

    async def create_tenant(
        self, tenant_id: str, tenant_name: str
    ) -> Dict[str, Any]:
        """创建租户。

        前置条件：tenant_id 不重复。
        后置条件：租户创建成功，返回租户信息。
        """
        from orchestration_platform.domain.models.enums import TenantStatus
        from orchestration_platform.governance.tenant.models import Tenant
        tenant = Tenant(
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            status=TenantStatus.ACTIVE,
        )
        return tenant.model_dump(mode="json")

    # ----- 令牌管理 -----

    async def issue_token(
        self, user_id: str, tenant_id: str, ttl_seconds: Optional[int] = None
    ) -> str:
        """签发访问令牌。

        前置条件：用户存在且状态为 ACTIVE。
        后置条件：返回令牌原文（仅此一次）。
        """
        return await self.token_manager.issue_token(
            user_id=user_id,
            tenant_id=tenant_id,
            ttl_seconds=ttl_seconds,
        )

    async def revoke_token(self, token_id: str) -> None:
        """吊销令牌。"""
        await self.token_manager.revoke_token(token_id)

    # ----- 用量查询 -----

    async def get_usage_summary(
        self, tenant_id: str, instance_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """查询用量汇总（roadmap B7 W1a：注入仓储后真实聚合）。

        前置条件：tenant_id 有效。
        后置条件：仓储已注入 → 返回聚合（executions 执行级口径 /
            node_records 节点级 / by_model 分桶 / 总量键）；未注入 →
            降级 {"available": False}（路由形状稳定，不抛错）。
        """
        repository = get_token_usage_repository()
        if repository is None:
            return {
                "tenant_id": tenant_id,
                "instance_id": instance_id,
                "available": False,
            }
        if instance_id:
            records = await repository.list_by_instance(tenant_id, instance_id)
        else:
            records = await repository.list_by_tenant(tenant_id)
        executions = 0
        node_records = 0
        by_model: Dict[str, Dict[str, Any]] = {}
        total_prompt = 0
        total_completion = 0
        total_tokens = 0
        for record in records:
            if record.node_id == "execution":
                executions += 1
            else:
                node_records += 1
            bucket = by_model.setdefault(
                record.model_identifier,
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "record_count": 0,
                },
            )
            bucket["prompt_tokens"] += record.prompt_tokens
            bucket["completion_tokens"] += record.completion_tokens
            bucket["total_tokens"] += record.total_tokens
            bucket["record_count"] += 1
            total_prompt += record.prompt_tokens
            total_completion += record.completion_tokens
            total_tokens += record.total_tokens
        return {
            "tenant_id": tenant_id,
            "instance_id": instance_id,
            "available": True,
            "record_count": len(records),
            "executions": executions,
            "node_records": node_records,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "by_model": by_model,
        }

    # ----- 成本与预算 -----

    async def get_cost_summary(self, tenant_id: str) -> Dict[str, Any]:
        """查询成本汇总。"""
        return await self.cost_calculator.aggregate_by_tenant(tenant_id)

    async def get_budget_status(self, budget_id: str) -> Optional[Dict[str, Any]]:
        """查询预算状态。"""
        return await self.budget_manager.get_budget_status(budget_id)

    # ----- 审计日志 -----

    async def query_audit_logs(
        self,
        tenant_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """查询审计日志：脱敏条目 + 哈希链校验（CHG-64）。

        前置条件：调用者有审计日志查询权限。
        后置条件：返回 {"entries": 脱敏条目列表（含条目级 chain_valid）,
                   "chain_check": 哈希链校验结果,
                   "chain_valid": 链整体有效性}；
                   断链条目如实标记，不隐瞒。
        """
        entries = await self.query_audit_entries(
            tenant_id=tenant_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        repository = self._resolve_audit_repository()
        if repository is None:
            chain_check: Dict[str, Any] = {
                "verified_count": 0,
                "failed_count": 0,
                "failed_entry_ids": [],
                "is_valid": True,
            }
        else:
            chain_check = await repository.verify_hash_chain(
                tenant_id=tenant_id,
                start_time=start_time,
                end_time=end_time,
            )
        failed_entry_ids = set(chain_check.get("failed_entry_ids", []))
        masked_entries: List[Dict[str, Any]] = []
        for entry in entries:
            data = self.mask_engine.mask_record(
                entry.model_dump(mode="json"), tenant_id=tenant_id
            )
            data["chain_valid"] = entry.entry_id not in failed_entry_ids
            masked_entries.append(data)
        return {
            "entries": masked_entries,
            "chain_check": chain_check,
            "chain_valid": bool(chain_check.get("is_valid", False)),
        }

    async def query_audit_entries(
        self,
        tenant_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[AuditLogEntry]:
        """查询原始审计条目（未脱敏，供合规导出使用，CHG-64）。"""
        repository = self._resolve_audit_repository()
        if repository is None:
            return []
        return await repository.query(
            tenant_id=tenant_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    def _resolve_audit_repository(self) -> Optional[IAuditLogRepository]:
        """解析审计日志仓储：构造注入优先，否则惰性获取（CHG-64）。"""
        if self._audit_repository is not None:
            return self._audit_repository
        try:
            from orchestration_platform.governance.audit.audit_event_subscriber import (
                acquire_audit_repository,
            )
        except ImportError:
            return None
        primary, fallback = acquire_audit_repository()
        repository = primary if primary is not None else fallback
        self._audit_repository = repository
        return repository

    # ----- 调度与资源池 -----

    async def get_scheduler_status(self) -> Dict[str, Any]:
        """查询调度器状态。"""
        return self.global_scheduler.get_status()

    async def get_resource_pool_status(self) -> List[Dict[str, Any]]:
        """查询资源池状态。"""
        pools = self.resource_pool_manager.list_pools()
        return [
            self.resource_pool_manager.get_utilization(p.pool_id)
            for p in pools
            if self.resource_pool_manager.get_utilization(p.pool_id)
        ]


# 全局单例
_services: Optional[GovernanceServices] = None


def get_governance_services() -> GovernanceServices:
    """获取全局治理服务实例。"""
    global _services
    if _services is None:
        _services = GovernanceServices()
    return _services


# 用量查询仓储（模块级注入；先例：metering.usage_recorder 的模块级
# set_usage_recorder 机制。roadmap B7 W1a：application_factory 装配期
# 调用 set_token_usage_repository 注入，治理 usage 端点据此真实聚合）
_token_usage_repository: Optional[ITokenUsageRepository] = None


def set_token_usage_repository(
    repository: Optional[ITokenUsageRepository],
) -> None:
    """注入用量查询仓储（application_factory 装配期调用；传 None 卸下）。"""
    global _token_usage_repository
    _token_usage_repository = repository


def get_token_usage_repository() -> Optional[ITokenUsageRepository]:
    """获取注入的用量查询仓储；未注入返回 None（端点降级）。"""
    return _token_usage_repository
