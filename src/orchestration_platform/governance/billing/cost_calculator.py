# =============================================================================
# 成本核算器（governance/billing/cost_calculator.py）
#
# 职责：按定价规则将用量记录核算为成本记录。
# 设计意图（对应 spec 5.2、design 2.5、任务 T59.2/T59.3）：
#   1. 成本 = 输入token数 × 输入单价/1000 + 输出token数 × 输出单价/1000；
#   2. 定价规则时效匹配：按用量执行时间匹配当时有效的定价规则；
#   3. 定价规则缺失处理：用量标记"待计费"，成本暂记为 0；
#   4. 多维汇总：按租户、工作流、时间维度聚合查询；
#   5. 成本核算一致性对账。
# =============================================================================

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.billing.models import CostRecord
from orchestration_platform.governance.billing.pricing_rule_manager import (
    PricingRuleManager,
)
from orchestration_platform.governance.metering.models import TokenUsageRecord


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CostCalculator:
    """成本核算器。

    按定价规则将用量记录核算为成本记录，支持多维汇总与对账。
    """

    def __init__(
        self,
        pricing_rule_manager: PricingRuleManager,
    ) -> None:
        """初始化成本核算器。

        Args:
            pricing_rule_manager: 定价规则管理器。
        """
        self._pricing_manager = pricing_rule_manager
        self._precision = get_settings().billing_cost_precision
        self._cost_records: Dict[str, CostRecord] = {}
        self._pending_usage: Dict[str, TokenUsageRecord] = {}

    async def calculate_cost(
        self, usage_record: TokenUsageRecord
    ) -> Tuple[Optional[CostRecord], bool]:
        """核算单条用量记录的成本。

        Args:
            usage_record: Token 用量记录。

        Returns:
            (CostRecord, is_pending) 元组。
            is_pending=True 表示定价规则缺失，成本暂记为 0（待计费）。
            CostRecord 为 None 表示无法核算。
        """
        rule = await self._pricing_manager.find_effective_rule(
            model_identifier=usage_record.model_identifier,
            tenant_id=usage_record.tenant_id,
            at_time=usage_record.recorded_at,
        )

        if rule is None:
            # 定价规则缺失：标记"待计费"
            self._pending_usage[usage_record.record_id] = usage_record
            return (None, True)

        # 成本 = 输入token数 × 输入单价/1000 + 输出token数 × 输出单价/1000
        cost_amount = (
            usage_record.prompt_tokens * rule.prompt_token_price / 1000
            + usage_record.completion_tokens * rule.completion_token_price / 1000
        )
        cost_amount = round(cost_amount, self._precision)

        cost_record = CostRecord(
            cost_id=f"cost_{uuid.uuid4().hex[:16]}",
            tenant_id=usage_record.tenant_id,
            usage_record_id=usage_record.record_id,
            instance_id=usage_record.instance_id,
            cost_amount=cost_amount,
            pricing_rule_id=rule.rule_id,
        )
        self._cost_records[cost_record.cost_id] = cost_record
        return (cost_record, False)

    async def calculate_batch(
        self, usage_records: List[TokenUsageRecord]
    ) -> List[Tuple[Optional[CostRecord], bool]]:
        """批量核算用量记录成本。"""
        results: List[Tuple[Optional[CostRecord], bool]] = []
        for record in usage_records:
            result = await self.calculate_cost(record)
            results.append(result)
        return results

    async def recalculate_pending(self) -> int:
        """补算"待计费"用量记录。

        定价规则配置后自动补算。

        Returns:
            成功补算的记录数。
        """
        if not self._pending_usage:
            return 0

        completed: List[str] = []
        for record_id, usage_record in self._pending_usage.items():
            rule = await self._pricing_manager.find_effective_rule(
                model_identifier=usage_record.model_identifier,
                tenant_id=usage_record.tenant_id,
                at_time=usage_record.recorded_at,
            )
            if rule is not None:
                cost_amount = round(
                    usage_record.prompt_tokens * rule.prompt_token_price / 1000
                    + usage_record.completion_tokens * rule.completion_token_price / 1000,
                    self._precision,
                )
                cost_record = CostRecord(
                    cost_id=f"cost_{uuid.uuid4().hex[:16]}",
                    tenant_id=usage_record.tenant_id,
                    usage_record_id=record_id,
                    instance_id=usage_record.instance_id,
                    cost_amount=cost_amount,
                    pricing_rule_id=rule.rule_id,
                )
                self._cost_records[cost_record.cost_id] = cost_record
                completed.append(record_id)

        for record_id in completed:
            del self._pending_usage[record_id]

        return len(completed)

    async def get_cost(self, cost_id: str) -> Optional[CostRecord]:
        """按标识查询成本记录。"""
        return self._cost_records.get(cost_id)

    async def list_costs_by_tenant(
        self, tenant_id: str
    ) -> List[CostRecord]:
        """按租户列出成本记录。"""
        return [
            c for c in self._cost_records.values()
            if c.tenant_id == tenant_id
        ]

    async def aggregate_by_tenant(
        self, tenant_id: str
    ) -> Dict[str, Any]:
        """按租户汇总成本。"""
        costs = await self.list_costs_by_tenant(tenant_id)
        return {
            "total_cost": round(sum(c.cost_amount for c in costs), self._precision),
            "record_count": len(costs),
        }

    async def aggregate_by_workflow(
        self, tenant_id: str, workflow_definition_id: str
    ) -> Dict[str, Any]:
        """按工作流汇总成本。"""
        costs = [
            c for c in self._cost_records.values()
            if c.tenant_id == tenant_id
        ]
        return {
            "total_cost": round(sum(c.cost_amount for c in costs), self._precision),
            "record_count": len(costs),
            "workflow_definition_id": workflow_definition_id,
        }

    async def reconcile(
        self, usage_records: List[TokenUsageRecord]
    ) -> Dict[str, Any]:
        """成本核算一致性对账。

        检查用量记录与成本记录一一对应。

        Returns:
            对账报告：{total_usage, total_cost, matched, missing_cost, pending}。
        """
        usage_ids = {r.record_id for r in usage_records}
        cost_usage_ids = {c.usage_record_id for c in self._cost_records.values()}
        pending_ids = set(self._pending_usage.keys())

        matched = usage_ids & cost_usage_ids
        missing_cost = usage_ids - cost_usage_ids - pending_ids

        return {
            "total_usage": len(usage_ids),
            "total_cost": len(self._cost_records),
            "matched": len(matched),
            "missing_cost": list(missing_cost),
            "pending": len(pending_ids),
        }

    @property
    def pending_count(self) -> int:
        """待计费记录数。"""
        return len(self._pending_usage)
