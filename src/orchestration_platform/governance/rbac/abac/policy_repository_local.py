# =============================================================================
# ABAC 策略仓储本地实现（governance/rbac/abac/policy_repository_local.py）
#
# 职责：进程内 ABAC 策略存储实现，用于单实例部署与测试。
# 设计意图（对应 design 模块B AbacPolicyRepository）：
#   - 按租户隔离存储
#   - 策略格式校验
#   - 软删除（is_active=False）
# =============================================================================

import logging
from typing import Dict, List, Optional

from orchestration_platform.governance.rbac.abac.abstractions.policy_repository import (
    IAbacPolicyRepository,
)
from orchestration_platform.governance.rbac.abac.models import AbacPolicy, AbacCondition

_logger = logging.getLogger(__name__)


class LocalAbacPolicyRepository:
    """本地内存 ABAC 策略仓储。

    按 (tenant_id, policy_id) 索引存储，进程内有效。
    """

    def __init__(self) -> None:
        """初始化空仓储。"""
        self._store: Dict[str, Dict[str, AbacPolicy]] = {}

    async def save(self, policy: AbacPolicy) -> str:
        """保存策略。

        异常映射：抛出 ValueError（含 POLICY_FORMAT_INVALID 语义）当格式非法。
        """
        self._validate_policy(policy)

        tenant_store = self._store.setdefault(policy.tenant_id, {})
        tenant_store[policy.policy_id] = policy
        _logger.info("ABAC 策略已保存: tenant=%s, policy=%s", policy.tenant_id, policy.policy_id)
        return policy.policy_id

    async def load_by_tenant(self, tenant_id: str) -> List[AbacPolicy]:
        """加载租户全部启用策略，按 priority 排序。"""
        tenant_store = self._store.get(tenant_id, {})
        active_policies = [p for p in tenant_store.values() if p.is_active]
        return sorted(active_policies, key=lambda p: p.priority)

    async def delete(self, policy_id: str, tenant_id: str) -> bool:
        """软删除策略。"""
        tenant_store = self._store.get(tenant_id, {})
        if policy_id not in tenant_store:
            return False
        policy = tenant_store[policy_id]
        tenant_store[policy_id] = policy.model_copy(update={"is_active": False})
        _logger.info("ABAC 策略已软删除: tenant=%s, policy=%s", tenant_id, policy_id)
        return True

    async def get_by_id(
        self,
        policy_id: str,
        tenant_id: str,
    ) -> Optional[AbacPolicy]:
        """按标识查询策略。"""
        tenant_store = self._store.get(tenant_id, {})
        return tenant_store.get(policy_id)

    def _validate_policy(self, policy: AbacPolicy) -> None:
        """校验策略格式。

        异常映射：抛出 ValueError（含 POLICY_FORMAT_INVALID 语义）。
        """
        if not policy.policy_id:
            raise ValueError("POLICY_FORMAT_INVALID: 策略标识不能为空")
        if not policy.tenant_id:
            raise ValueError("POLICY_FORMAT_INVALID: 租户标识不能为空")
        if not policy.conditions:
            raise ValueError("POLICY_FORMAT_INVALID: 条件集合不能为空")

        for condition in policy.conditions:
            self._validate_condition(condition)

    def _validate_condition(self, condition: AbacCondition) -> None:
        """校验单个条件格式。"""
        if not condition.attribute_name:
            raise ValueError("POLICY_FORMAT_INVALID: 属性名称不能为空")
        if not condition.expected_value and condition.operator.value not in ("in", "not_in"):
            raise ValueError("POLICY_FORMAT_INVALID: 期望值不能为空")
