# =============================================================================
# 定价规则管理器（governance/billing/pricing_rule_manager.py）
#
# 职责：管理定价规则的增删改查与生效时间区间。
# 设计意图（对应 spec 5.2、design 2.5、任务 T59.1）：
#   1. 定价规则按模型与 Token 类型定义单价；
#   2. 生效时间区间管理，支持按时间匹配有效规则；
#   3. 单价精度为小数点后 6 位。
# =============================================================================

from datetime import datetime, timezone
from typing import Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.billing.models import PricingRule


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PricingRuleManager:
    """定价规则管理器。

    管理定价规则的增删改查，支持按模型与时间匹配有效规则。
    """

    def __init__(self) -> None:
        """初始化定价规则管理器（内存存储，生产环境使用仓储）。"""
        self._rules: Dict[str, PricingRule] = {}
        self._precision = get_settings().billing_cost_precision

    async def create_rule(self, rule: PricingRule) -> PricingRule:
        """创建定价规则。

        前置条件：rule_id 不重复。
        后置条件：规则持久化，可被 get_rule 查询到。
        """
        rule.prompt_token_price = round(rule.prompt_token_price, self._precision)
        rule.completion_token_price = round(
            rule.completion_token_price, self._precision
        )
        self._rules[rule.rule_id] = rule
        return rule

    async def get_rule(self, rule_id: str) -> Optional[PricingRule]:
        """按标识查询定价规则。"""
        return self._rules.get(rule_id)

    async def list_rules(
        self, tenant_id: Optional[str] = None
    ) -> List[PricingRule]:
        """列出全部定价规则，可按租户过滤。"""
        if tenant_id is None:
            return list(self._rules.values())
        return [
            r for r in self._rules.values()
            if r.tenant_id == tenant_id or r.tenant_id is None
        ]

    async def update_rule(self, rule: PricingRule) -> PricingRule:
        """更新定价规则。"""
        rule.prompt_token_price = round(rule.prompt_token_price, self._precision)
        rule.completion_token_price = round(
            rule.completion_token_price, self._precision
        )
        self._rules[rule.rule_id] = rule
        return rule

    async def delete_rule(self, rule_id: str) -> None:
        """删除定价规则。"""
        self._rules.pop(rule_id, None)

    async def find_effective_rule(
        self,
        model_identifier: str,
        tenant_id: str,
        at_time: Optional[datetime] = None,
    ) -> Optional[PricingRule]:
        """按模型与时间查找有效定价规则。

        优先匹配租户级规则，其次全局规则。
        定价规则缺失时返回 None（用量标记"待计费"）。
        """
        check_time = at_time or _utc_now()

        # 优先查找租户级规则
        tenant_rules = [
            r for r in self._rules.values()
            if r.model_identifier == model_identifier
            and r.tenant_id == tenant_id
            and r.effective_from <= check_time
            and (r.effective_to is None or r.effective_to > check_time)
        ]
        if tenant_rules:
            return tenant_rules[0]

        # 其次查找全局规则
        global_rules = [
            r for r in self._rules.values()
            if r.model_identifier == model_identifier
            and r.tenant_id is None
            and r.effective_from <= check_time
            and (r.effective_to is None or r.effective_to > check_time)
        ]
        if global_rules:
            return global_rules[0]

        return None
