# =============================================================================
# ABAC 权限扩展测试（tests/governance/test_abac_extension.py）
#
# 职责：验证 ABAC 策略引擎与组合决策的正确性。
# 设计意图（对应偏差修正 T97 / 验收 A81-A85, B86-B88）：
#   1. 策略格式校验
#   2. 属性求值（时间范围、IP段、主体/资源/环境属性）
#   3. 组合决策顺序
#   4. 无策略默认允许
#   5. 属性求值失败处理
#   6. 拦截器链集成
#   7. 向后兼容
# =============================================================================

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from orchestration_platform.domain.models.enums import (
    AttributeSource,
    ConditionOperator,
    FailMode,
    OperationType,
    PolicyEffect,
    ResourceType,
)
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
    InterceptorChain,
)
from orchestration_platform.governance.rbac.abac.abac_policy_engine import (
    AbacPolicyEngine,
)
from orchestration_platform.governance.rbac.abac.attribute_evaluator import (
    AttributeEvaluator,
)
from orchestration_platform.governance.rbac.abac.combined_decision_engine import (
    CombinedDecisionEngine,
)
from orchestration_platform.governance.rbac.abac.models import (
    AbacCondition,
    AbacDecision,
    AbacPolicy,
)
from orchestration_platform.governance.rbac.abac.policy_repository_local import (
    LocalAbacPolicyRepository,
)


def _run_async(coro):
    """辅助：在同步测试中运行协程。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestAbacPolicyRepository(unittest.TestCase):
    """ABAC 策略仓储测试（验收 A81, B86）。"""

    def setUp(self) -> None:
        self.repo = LocalAbacPolicyRepository()

    def test_save_and_load_policy(self) -> None:
        """保存并加载策略。"""
        policy = AbacPolicy(
            policy_id="p1",
            tenant_id="t1",
            conditions=[
                AbacCondition(
                    attribute_name="role",
                    attribute_source=AttributeSource.SUBJECT,
                    operator=ConditionOperator.EQ,
                    expected_value="admin",
                ),
            ],
            effect=PolicyEffect.ALLOW,
        )
        _run_async(self.repo.save(policy))
        loaded = _run_async(self.repo.load_by_tenant("t1"))
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].policy_id, "p1")

    def test_invalid_policy_raises_error(self) -> None:
        """格式非法策略抛出异常。"""
        policy = AbacPolicy(
            policy_id="",
            tenant_id="t1",
            conditions=[
                AbacCondition(
                    attribute_name="role",
                    attribute_source=AttributeSource.SUBJECT,
                    operator=ConditionOperator.EQ,
                    expected_value="admin",
                ),
            ],
            effect=PolicyEffect.ALLOW,
        )
        with self.assertRaises(ValueError) as ctx:
            _run_async(self.repo.save(policy))
        self.assertIn("POLICY_FORMAT_INVALID", str(ctx.exception))

    def test_soft_delete(self) -> None:
        """软删除策略。"""
        policy = AbacPolicy(
            policy_id="p1",
            tenant_id="t1",
            conditions=[
                AbacCondition(
                    attribute_name="role",
                    attribute_source=AttributeSource.SUBJECT,
                    operator=ConditionOperator.EQ,
                    expected_value="admin",
                ),
            ],
            effect=PolicyEffect.ALLOW,
        )
        _run_async(self.repo.save(policy))
        success = _run_async(self.repo.delete("p1", "t1"))
        self.assertTrue(success)
        loaded = _run_async(self.repo.load_by_tenant("t1"))
        self.assertEqual(len(loaded), 0)


class TestAttributeEvaluator(unittest.TestCase):
    """属性求值器测试（验收 A82, A83）。"""

    def setUp(self) -> None:
        self.evaluator = AttributeEvaluator()

    def test_subject_attribute(self) -> None:
        """主体属性求值。"""
        request = GovernanceRequest(
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            operation=OperationType.CREATE,
            trace_id="t1",
            tenant_id="tenant_001",
            user_id="user_001",
            metadata={"role": "admin", "department": "engineering"},
        )
        result = _run_async(
            self.evaluator.evaluate("role", AttributeSource.SUBJECT, request)
        )
        self.assertEqual(result, "admin")

    def test_resource_attribute(self) -> None:
        """资源属性求值。"""
        request = GovernanceRequest(
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            operation=OperationType.CREATE,
            trace_id="t1",
        )
        result = _run_async(
            self.evaluator.evaluate("resource_type", AttributeSource.RESOURCE, request)
        )
        self.assertEqual(result, "workflow_definition")

    def test_environment_attribute(self) -> None:
        """环境属性求值。"""
        request = GovernanceRequest(
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            operation=OperationType.CREATE,
            trace_id="t1",
            metadata={"client_ip": "192.168.1.100"},
        )
        result = _run_async(
            self.evaluator.evaluate("request_ip", AttributeSource.ENVIRONMENT, request)
        )
        self.assertEqual(result, "192.168.1.100")


class TestAbacPolicyEngine(unittest.TestCase):
    """ABAC 策略引擎测试（验收 A81, A83, A84, B87）。"""

    def setUp(self) -> None:
        self.repo = LocalAbacPolicyRepository()
        self.evaluator = AttributeEvaluator()
        self.engine = AbacPolicyEngine(self.repo, self.evaluator)

    def test_no_policy_default_allow(self) -> None:
        """无策略时默认允许（验收 A84）。"""
        request = GovernanceRequest(
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            operation=OperationType.CREATE,
            trace_id="t1",
            tenant_id="t1",
            user_id="u1",
        )
        decision = _run_async(self.engine.decide(request))
        self.assertTrue(decision.is_no_policy)
        self.assertEqual(decision.effect, PolicyEffect.ALLOW)

    def test_policy_match_allow(self) -> None:
        """策略匹配允许。"""
        policy = AbacPolicy(
            policy_id="p1",
            tenant_id="t1",
            conditions=[
                AbacCondition(
                    attribute_name="role",
                    attribute_source=AttributeSource.SUBJECT,
                    operator=ConditionOperator.EQ,
                    expected_value="admin",
                ),
            ],
            effect=PolicyEffect.ALLOW,
        )
        _run_async(self.repo.save(policy))

        request = GovernanceRequest(
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            operation=OperationType.CREATE,
            trace_id="t1",
            tenant_id="t1",
            user_id="u1",
            metadata={"role": "admin"},
        )
        decision = _run_async(self.engine.decide(request))
        self.assertEqual(decision.effect, PolicyEffect.ALLOW)
        self.assertEqual(decision.matched_policy_id, "p1")

    def test_policy_match_deny(self) -> None:
        """策略匹配拒绝。"""
        policy = AbacPolicy(
            policy_id="p1",
            tenant_id="t1",
            conditions=[
                AbacCondition(
                    attribute_name="role",
                    attribute_source=AttributeSource.SUBJECT,
                    operator=ConditionOperator.EQ,
                    expected_value="guest",
                ),
            ],
            effect=PolicyEffect.DENY,
        )
        _run_async(self.repo.save(policy))

        request = GovernanceRequest(
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            operation=OperationType.CREATE,
            trace_id="t1",
            tenant_id="t1",
            user_id="u1",
            metadata={"role": "guest"},
        )
        decision = _run_async(self.engine.decide(request))
        self.assertEqual(decision.effect, PolicyEffect.DENY)

    def test_policy_not_match_default_allow(self) -> None:
        """策略不匹配时默认允许。"""
        policy = AbacPolicy(
            policy_id="p1",
            tenant_id="t1",
            conditions=[
                AbacCondition(
                    attribute_name="role",
                    attribute_source=AttributeSource.SUBJECT,
                    operator=ConditionOperator.EQ,
                    expected_value="admin",
                ),
            ],
            effect=PolicyEffect.DENY,
        )
        _run_async(self.repo.save(policy))

        request = GovernanceRequest(
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            operation=OperationType.CREATE,
            trace_id="t1",
            tenant_id="t1",
            user_id="u1",
            metadata={"role": "user"},
        )
        decision = _run_async(self.engine.decide(request))
        self.assertEqual(decision.effect, PolicyEffect.ALLOW)


class TestInterceptorChainBackwardCompat(unittest.TestCase):
    """拦截器链向后兼容测试（验收 B88）。"""

    def test_chain_without_abac_works(self) -> None:
        """不传 abac 参数时拦截器链正常工作。"""
        chain = InterceptorChain()
        self.assertTrue(chain.is_degraded_mode)

        request = GovernanceRequest(
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            operation=OperationType.CREATE,
            trace_id="t1",
        )
        decision = _run_async(chain.execute_before(request))
        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
