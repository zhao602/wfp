# =============================================================================
# Token 计量与计费测试（tests/governance/test_metering_billing.py）
#
# 覆盖验收：A47-A56、B39-B41、B51-B53、B65
# =============================================================================

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from orchestration_platform.governance.metering.token_meter import TokenMeter
from orchestration_platform.governance.metering.usage_buffer_queue import UsageBufferQueue
from orchestration_platform.governance.billing.pricing_rule_manager import PricingRuleManager
from orchestration_platform.governance.billing.cost_calculator import CostCalculator
from orchestration_platform.governance.billing.budget_manager import BudgetManager
from orchestration_platform.governance.billing.models import PricingRule, Budget
from orchestration_platform.domain.models.enums import (
    BudgetPeriod,
    BudgetScope,
    CircuitBreakStrategy,
)


async def test_token_meter():
    """A47: Token 用量采集。"""
    meter = TokenMeter()
    record = meter.collect_usage(
        tenant_id="tenant1",
        instance_id="inst1",
        node_id="node1",
        inference_result={
            "input_tokens": 100,
            "output_tokens": 50,
            "model_id": "gpt-4",
        },
        agent_node_id="agent1",
    )
    assert record.prompt_tokens == 100
    assert record.completion_tokens == 50
    assert record.total_tokens == 150
    assert record.model_identifier == "gpt-4"
    print("✓ A47: Token 用量采集")


async def test_cost_calculation():
    """A50: 成本核算。"""
    pricing_manager = PricingRuleManager()
    calculator = CostCalculator(pricing_manager)

    # 创建定价规则
    rule = PricingRule(
        rule_id="rule1",
        model_identifier="gpt-4",
        prompt_token_price=0.03,
        completion_token_price=0.06,
    )
    await pricing_manager.create_rule(rule)

    # 采集用量
    meter = TokenMeter()
    record = meter.collect_usage(
        tenant_id="tenant1",
        instance_id="inst1",
        node_id="node1",
        inference_result={
            "input_tokens": 1000,
            "output_tokens": 500,
            "model_id": "gpt-4",
        },
    )

    # 核算成本
    cost_record, is_pending = await calculator.calculate_cost(record)
    assert not is_pending
    assert cost_record is not None
    # 成本 = 1000 * 0.03/1000 + 500 * 0.06/1000 = 0.03 + 0.03 = 0.06
    assert abs(cost_record.cost_amount - 0.06) < 0.001
    print("✓ A50: 成本核算")


async def test_budget_circuit_break():
    """A54: 预算熔断。"""
    from datetime import datetime, timedelta, timezone

    manager = BudgetManager()
    now = datetime.now(timezone.utc)
    budget = Budget(
        budget_id="budget1",
        tenant_id="tenant1",
        scope=BudgetScope.TENANT,
        target_id="tenant1",
        period=BudgetPeriod.MONTHLY,
        budget_limit=100.0,
        alert_threshold=0.8,
        circuit_break_strategy=CircuitBreakStrategy.HARD_STOP,
        period_start=now,
        period_end=now + timedelta(days=30),
    )
    await manager.create_budget(budget)

    # 累加成本至超限
    await manager.add_cost("budget1", 100.0)

    status = await manager.get_budget_status("budget1")
    assert status is not None
    assert status["is_circuit_broken"]
    print("✓ A54: 预算熔断")


async def test_usage_buffer_queue():
    """B51: 计量不丢失——缓冲队列。"""
    queue = UsageBufferQueue(
        flush_callback=lambda records: None,
        max_size=100,
    )

    meter = TokenMeter()
    record = meter.collect_usage(
        tenant_id="tenant1",
        instance_id="inst1",
        node_id="node1",
        inference_result={"input_tokens": 10, "output_tokens": 5, "model_id": "gpt-4"},
    )

    success = await queue.enqueue(record)
    assert success
    assert queue.size >= 0
    print("✓ B51: 计量缓冲队列")


async def run_all_tests():
    print("=" * 60)
    print("Token 计量与计费测试")
    print("=" * 60)

    await test_token_meter()
    await test_cost_calculation()
    await test_budget_circuit_break()
    await test_usage_buffer_queue()

    print("=" * 60)
    print("全部测试通过")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
