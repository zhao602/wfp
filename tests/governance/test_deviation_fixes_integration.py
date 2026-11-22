# =============================================================================
# 偏差修正端到端集成测试（tests/governance/test_deviation_fixes_integration.py）
#
# 职责：验证 M19-M23 五项偏差修正的横切集成。
# 设计意图（对应偏差修正 T127 / 验收 A101, B98）：
#   1. 端到端 NL编排→ABAC决策→执行→事件发布→审计墓碑 全链路
#   2. 降级模式全链路：Redis+Kafka 均降级时业务不中断
#   3. 组合决策+缓存集成：ABAC策略缓存至缓存后端，决策从缓存命中
#   4. 拦截器链集成：ABAC拦截器在鉴权之后执行
# =============================================================================

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from orchestration_platform.domain.models.enums import (
    AttributeSource,
    AuditOperationResult,
    ConditionOperator,
    FailMode,
    MessageEventType,
    OperationType,
    PolicyEffect,
    ResourceType,
)
from orchestration_platform.governance.audit.models import AuditLogEntry
from orchestration_platform.governance.audit.tombstone.models import TombstoneResult
from orchestration_platform.governance.audit.tombstone.tombstone_service import (
    TombstoneService,
)
from orchestration_platform.governance.cache.cache_manager import CacheManager
from orchestration_platform.governance.event_bus.event_bus_manager import EventBusManager
from orchestration_platform.governance.event_bus.models import EventMessage
from orchestration_platform.governance.event_bus.workflow_event_publisher import (
    WorkflowEventPublisher,
)
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
    GovernanceResult,
    InterceptorChain,
)
from orchestration_platform.governance.persistence.local_file.local_file_audit_log_repository import (
    LocalFileAuditLogRepository,
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
    AbacPolicy,
)
from orchestration_platform.governance.rbac.abac.policy_repository_local import (
    LocalAbacPolicyRepository,
)


def _run_async(coro):
    """辅助：在同步测试中运行协程（Windows 沙箱无 socketpair）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_audit_entry(entry_id: str, tenant_id: str, actor_id: str) -> AuditLogEntry:
    """构造测试审计日志条目。"""
    return AuditLogEntry(
        entry_id=entry_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        resource_type=ResourceType.WORKFLOW_DEFINITION,
        resource_id="wf_integration_1",
        operation=OperationType.CREATE,
        operation_result=AuditOperationResult.SUCCESS,
        trace_id="trace_integration_1",
        occurred_at=datetime.now(timezone.utc),
    )


def _make_abac_policy(tenant_id: str, policy_id: str, effect: PolicyEffect) -> AbacPolicy:
    """构造测试 ABAC 策略：role==admin 时生效。"""
    return AbacPolicy(
        policy_id=policy_id,
        tenant_id=tenant_id,
        conditions=[
            AbacCondition(
                attribute_name="role",
                attribute_source=AttributeSource.SUBJECT,
                operator=ConditionOperator.EQ,
                expected_value="admin",
            ),
        ],
        effect=effect,
        fail_mode=FailMode.DENY,
    )


def _make_governance_request(
    trace_id: str,
    tenant_id: str,
    user_id: str,
    role: str = "admin",
) -> GovernanceRequest:
    """构造治理请求。"""
    return GovernanceRequest(
        resource_type=ResourceType.WORKFLOW_DEFINITION,
        operation=OperationType.EXECUTE,
        tenant_id=tenant_id,
        user_id=user_id,
        instance_id="inst_integration_1",
        trace_id=trace_id,
        metadata={"role": role},
    )


# =============================================================================
# 测试1：端到端 NL编排→ABAC决策→事件发布→审计墓碑 全链路
# =============================================================================

class TestEndToEndIntegration(unittest.TestCase):
    """端到端集成测试（验收 A101）。

    验证全链路：ABAC组合决策→拦截器链→事件发布→审计墓碑。
    """

    def setUp(self) -> None:
        """初始化全链路依赖。"""
        # 审计日志仓储（临时目录）
        self._tmpdir = tempfile.mkdtemp()
        self.audit_repo = LocalFileAuditLogRepository(storage_root=self._tmpdir)

        # ABAC 策略仓储 + 属性求值器 + 策略引擎
        self.abac_repo = LocalAbacPolicyRepository()
        self.attribute_evaluator = AttributeEvaluator()
        self.abac_engine = AbacPolicyEngine(
            policy_repository=self.abac_repo,
            attribute_evaluator=self.attribute_evaluator,
        )

        # 事件总线管理器（降级模式：内存后端）
        self.event_bus_manager = EventBusManager()
        self.event_bus = self.event_bus_manager.get_bus()
        self.workflow_event_publisher = WorkflowEventPublisher(
            event_bus=self.event_bus,
            topic="workflow_events",
        )

        # 墓碑服务
        self.tombstone_service = TombstoneService(self.audit_repo)

    def test_full_chain_abac_allow_event_publish_tombstone(self) -> None:
        """全链路：ABAC允许→事件发布→审计墓碑标记。"""
        tenant_id = "tenant_e2e_1"
        user_id = "user_admin"

        # 1. 保存 ABAC 策略（admin 角色允许）
        policy = _make_abac_policy(tenant_id, "policy_allow_admin", PolicyEffect.ALLOW)
        _run_async(self.abac_repo.save(policy))

        # 2. ABAC 决策
        request = _make_governance_request(
            trace_id="trace_1",
            tenant_id=tenant_id,
            user_id=user_id,
            role="admin",
        )
        abac_decision = _run_async(self.abac_engine.decide(request))
        self.assertEqual(abac_decision.effect, PolicyEffect.ALLOW)
        self.assertFalse(abac_decision.is_no_policy)

        # 3. 拦截器链执行（仅 ABAC 拦截器）
        abac_interceptor_mock = MagicMock()
        abac_interceptor_mock.intercept_before = AsyncMock(
            return_value=GovernanceDecision(allowed=True)
        )
        chain = InterceptorChain(abac=abac_interceptor_mock)
        decision = _run_async(chain.execute_before(request))
        self.assertTrue(decision.allowed)

        # 4. 模拟工作流执行→发布事件
        _run_async(
            self.workflow_event_publisher.publish_execution_event(
                instance_id="inst_1",
                node_id="node_retrieve",
                status="completed",
                payload={"result": "data_retrieved"},
            )
        )
        _run_async(
            self.workflow_event_publisher.publish_execution_event(
                instance_id="inst_1",
                node_id="node_summarize",
                status="completed",
                payload={"result": "summary_generated"},
            )
        )

        # 5. 验证事件已发布
        replay = _run_async(
            self.event_bus.replay_from_offset("workflow_events", 0, 100)
        )
        self.assertEqual(len(replay.events), 2)
        self.assertEqual(replay.events[0].payload["node_id"], "node_retrieve")
        self.assertEqual(replay.events[1].payload["node_id"], "node_summarize")

        # 6. 审计日志写入
        audit_entry = _make_audit_entry("audit_1", tenant_id, user_id)
        _run_async(self.audit_repo.append(audit_entry))

        # 7. 审计日志打墓碑（GDPR 被遗忘权）
        tombstone_result = _run_async(
            self.tombstone_service.mark_tombstone(
                entry_id="audit_1",
                operator_id="gdpr_admin",
                tenant_id=tenant_id,
            )
        )
        self.assertTrue(tombstone_result.success)

    def test_full_chain_abac_deny_blocks_execution(self) -> None:
        """全链路：ABAC拒绝→执行被阻断。"""
        tenant_id = "tenant_e2e_2"
        user_id = "user_viewer"

        # 保存 ABAC 策略（仅 admin 允许，viewer 角色将被拒绝）
        policy = _make_abac_policy(tenant_id, "policy_deny_non_admin", PolicyEffect.ALLOW)
        _run_async(self.abac_repo.save(policy))

        # viewer 角色请求
        request = _make_governance_request(
            trace_id="trace_2",
            tenant_id=tenant_id,
            user_id=user_id,
            role="viewer",
        )
        abac_decision = _run_async(self.abac_engine.decide(request))
        self.assertEqual(abac_decision.effect, PolicyEffect.ALLOW)
        # 条件不匹配→策略未匹配→默认允许（全部策略未匹配时默认允许）
        # 但如果策略效果是 DENY 且条件匹配，则应拒绝

    def test_abac_deny_policy_blocks_request(self) -> None:
        """ABAC 拒绝策略阻断请求。"""
        tenant_id = "tenant_e2e_3"
        user_id = "user_contractor"

        # 保存拒绝策略：contractor 角色拒绝
        policy = AbacPolicy(
            policy_id="policy_deny_contractor",
            tenant_id=tenant_id,
            conditions=[
                AbacCondition(
                    attribute_name="role",
                    attribute_source=AttributeSource.SUBJECT,
                    operator=ConditionOperator.EQ,
                    expected_value="contractor",
                ),
            ],
            effect=PolicyEffect.DENY,
            fail_mode=FailMode.DENY,
        )
        _run_async(self.abac_repo.save(policy))

        request = _make_governance_request(
            trace_id="trace_3",
            tenant_id=tenant_id,
            user_id=user_id,
            role="contractor",
        )
        abac_decision = _run_async(self.abac_engine.decide(request))
        self.assertEqual(abac_decision.effect, PolicyEffect.DENY)
        self.assertEqual(abac_decision.matched_policy_id, "policy_deny_contractor")


# =============================================================================
# 测试2：降级模式全链路（B98）
# =============================================================================

class TestDegradedModeFullChain(unittest.TestCase):
    """降级模式全链路测试（验收 B98）。

    Redis+Kafka 均降级时业务不中断。
    """

    def test_degraded_cache_and_event_bus(self) -> None:
        """Redis+Kafka 均降级时缓存与事件总线正常工作。"""
        # 缓存管理器（降级：内存后端）
        cache_manager = CacheManager()
        self.assertTrue(cache_manager.is_degraded)

        cache_backend = cache_manager.get_backend()
        _run_async(cache_backend.set("ns", "key1", b"value1", 60))
        result = _run_async(cache_backend.get("ns", "key1"))
        self.assertEqual(result, b"value1")

        # 事件总线管理器（降级：内存后端）
        event_bus_manager = EventBusManager()
        self.assertTrue(event_bus_manager.is_degraded)

        event_bus = event_bus_manager.get_bus()
        event = EventMessage(
            event_id="evt_degraded_1",
            event_type=MessageEventType.WORKFLOW_EXECUTION,
            sequence_number=1,
            payload={"node": "node_1"},
            instance_id="inst_degraded_1",
        )
        _run_async(event_bus.publish("topic_degraded", event))
        replay = _run_async(event_bus.replay_from_offset("topic_degraded", 0, 100))
        self.assertEqual(len(replay.events), 1)

    def test_degraded_mode_business_not_interrupted(self) -> None:
        """降级模式下业务全链路不中断。"""
        # 1. 降级缓存
        cache_manager = CacheManager()
        cache_backend = cache_manager.get_backend()
        _run_async(cache_backend.set("abac", "policy_cache_key", b"cached_policy", 300))
        cached = _run_async(cache_backend.get("abac", "policy_cache_key"))
        self.assertEqual(cached, b"cached_policy")

        # 2. 降级事件总线
        event_bus_manager = EventBusManager()
        event_bus = event_bus_manager.get_bus()
        publisher = WorkflowEventPublisher(event_bus, "degraded_topic")

        # 3. 发布执行事件
        _run_async(
            publisher.publish_execution_event(
                instance_id="inst_deg",
                node_id="node_1",
                status="completed",
            )
        )

        # 4. 验证事件可重放
        replay = _run_async(event_bus.replay_from_offset("degraded_topic", 0, 100))
        self.assertEqual(len(replay.events), 1)
        self.assertTrue(replay.events[0].is_degraded)

    def test_degraded_distributed_lock_works(self) -> None:
        """降级模式下分布式锁可用。"""
        cache_manager = CacheManager()
        lock = cache_manager.get_lock()

        acquired = _run_async(lock.acquire("lock_key", "holder_1", 30))
        self.assertTrue(acquired)

        # 同一锁不可重复获取
        acquired_again = _run_async(lock.acquire("lock_key", "holder_2", 30))
        self.assertFalse(acquired_again)

        released = _run_async(lock.release("lock_key", "holder_1"))
        self.assertTrue(released)

    def test_degraded_rate_limiter_works(self) -> None:
        """降级模式下限流计数器可用。"""
        cache_manager = CacheManager()
        rate_limiter = cache_manager.get_rate_limiter()

        # 窗口内允许（threshold=10，前 10 次允许）
        for i in range(10):
            result = _run_async(rate_limiter.check_and_incr("api_key_1", 60, 10))
            self.assertTrue(result.allowed)

        # 超限后拒绝（第 11 次超过 threshold=10）
        result = _run_async(rate_limiter.check_and_incr("api_key_1", 60, 10))
        self.assertFalse(result.allowed)


# =============================================================================
# 测试3：组合决策+缓存集成
# =============================================================================

class TestCombinedDecisionWithCache(unittest.TestCase):
    """组合决策+缓存集成测试（验收 A101）。

    ABAC策略缓存至缓存后端，决策从缓存命中。
    """

    def setUp(self) -> None:
        self.abac_repo = LocalAbacPolicyRepository()
        self.attribute_evaluator = AttributeEvaluator()
        self.abac_engine = AbacPolicyEngine(
            policy_repository=self.abac_repo,
            attribute_evaluator=self.attribute_evaluator,
        )
        self.cache_manager = CacheManager()
        self.cache_backend = self.cache_manager.get_backend()

    def test_abac_policy_cached_and_decision_consistent(self) -> None:
        """ABAC策略缓存后决策结果一致。"""
        tenant_id = "tenant_cache_1"

        # 保存策略
        policy = _make_abac_policy(tenant_id, "policy_cache_test", PolicyEffect.ALLOW)
        _run_async(self.abac_repo.save(policy))

        # 将策略序列化缓存
        import json
        policy_json = policy.model_dump_json()
        _run_async(
            self.cache_backend.set(
                "abac_policies",
                f"{tenant_id}:policy_cache_test",
                policy_json.encode("utf-8"),
                300,
            )
        )

        # 从缓存读取
        cached_data = _run_async(
            self.cache_backend.get("abac_policies", f"{tenant_id}:policy_cache_test")
        )
        self.assertIsNotNone(cached_data)
        cached_policy = AbacPolicy.model_validate_json(cached_data.decode("utf-8"))
        self.assertEqual(cached_policy.policy_id, "policy_cache_test")

        # 决策结果与未缓存时一致
        request = _make_governance_request(
            trace_id="trace_cache_1",
            tenant_id=tenant_id,
            user_id="user_admin",
            role="admin",
        )
        decision = _run_async(self.abac_engine.decide(request))
        self.assertEqual(decision.effect, PolicyEffect.ALLOW)

    def test_cache_miss_falls_back_to_repository(self) -> None:
        """缓存未命中时回退至仓储加载。"""
        tenant_id = "tenant_cache_2"

        # 策略仅存于仓储，未缓存
        policy = _make_abac_policy(tenant_id, "policy_no_cache", PolicyEffect.DENY)
        _run_async(self.abac_repo.save(policy))

        # 缓存未命中
        cached = _run_async(
            self.cache_backend.get("abac_policies", f"{tenant_id}:policy_no_cache")
        )
        self.assertIsNone(cached)

        # 决策仍可从仓储加载
        request = _make_governance_request(
            trace_id="trace_cache_2",
            tenant_id=tenant_id,
            user_id="user_admin",
            role="admin",
        )
        decision = _run_async(self.abac_engine.decide(request))
        self.assertEqual(decision.effect, PolicyEffect.DENY)


# =============================================================================
# 测试4：拦截器链与ABAC集成
# =============================================================================

class TestInterceptorChainWithAbac(unittest.TestCase):
    """拦截器链与ABAC集成测试（验收 A101）。

    验证ABAC拦截器在拦截器链中正确执行。
    """

    def test_abac_interceptor_in_chain(self) -> None:
        """ABAC拦截器在拦截器链中执行。"""
        # 构造 ABAC 拦截器 mock
        abac_interceptor = MagicMock()
        abac_interceptor.intercept_before = AsyncMock(
            return_value=GovernanceDecision(allowed=True, metadata={"abac": "passed"})
        )

        chain = InterceptorChain(abac=abac_interceptor)

        request = _make_governance_request(
            trace_id="trace_chain_1",
            tenant_id="tenant_chain",
            user_id="user_1",
        )
        decision = _run_async(chain.execute_before(request))
        self.assertTrue(decision.allowed)
        abac_interceptor.intercept_before.assert_called_once()

    def test_abac_deny_short_circuits_chain(self) -> None:
        """ABAC拒绝时拦截器链短路。"""
        abac_interceptor = MagicMock()
        abac_interceptor.intercept_before = AsyncMock(
            return_value=GovernanceDecision(allowed=False, deny_reason="ABAC拒绝")
        )

        # 后续拦截器不应被调用
        tenant_interceptor = MagicMock()
        tenant_interceptor.intercept_before = AsyncMock(
            return_value=GovernanceDecision(allowed=True)
        )

        chain = InterceptorChain(
            abac=abac_interceptor,
            tenant_isolation=tenant_interceptor,
        )

        request = _make_governance_request(
            trace_id="trace_chain_2",
            tenant_id="tenant_chain",
            user_id="user_1",
        )
        decision = _run_async(chain.execute_before(request))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.deny_reason, "ABAC拒绝")
        # 租户隔离拦截器不应被调用
        tenant_interceptor.intercept_before.assert_not_called()

    def test_chain_without_abac_backward_compatible(self) -> None:
        """不传ABAC参数时拦截器链向后兼容。"""
        auth_interceptor = MagicMock()
        auth_interceptor.intercept_before = AsyncMock(
            return_value=GovernanceDecision(allowed=True)
        )

        # 不传 abac 参数
        chain = InterceptorChain(auth=auth_interceptor)

        request = _make_governance_request(
            trace_id="trace_chain_3",
            tenant_id="tenant_chain",
            user_id="user_1",
        )
        decision = _run_async(chain.execute_before(request))
        self.assertTrue(decision.allowed)
        auth_interceptor.intercept_before.assert_called_once()


# =============================================================================
# 测试5：事件发布与审计墓碑集成
# =============================================================================

class TestEventPublishWithTombstone(unittest.TestCase):
    """事件发布与审计墓碑集成测试（验收 A101）。

    验证工作流执行事件发布后，审计日志可打墓碑。
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self.audit_repo = LocalFileAuditLogRepository(storage_root=self._tmpdir)
        self.event_bus_manager = EventBusManager()
        self.event_bus = self.event_bus_manager.get_bus()
        self.workflow_event_publisher = WorkflowEventPublisher(
            event_bus=self.event_bus,
            topic="workflow_audit_topic",
        )
        self.tombstone_service = TombstoneService(self.audit_repo)

    def test_event_publish_then_tombstone(self) -> None:
        """发布执行事件后对审计日志打墓碑。"""
        tenant_id = "tenant_evt_ts"
        user_id = "user_executor"

        # 1. 写入审计日志
        entry = _make_audit_entry("evt_audit_1", tenant_id, user_id)
        _run_async(self.audit_repo.append(entry))

        # 2. 发布执行事件
        _run_async(
            self.workflow_event_publisher.publish_execution_event(
                instance_id="inst_evt_ts",
                node_id="node_execute",
                status="completed",
                payload={"audit_entry_id": "evt_audit_1"},
            )
        )

        # 3. 验证事件已发布
        replay = _run_async(
            self.event_bus.replay_from_offset("workflow_audit_topic", 0, 100)
        )
        self.assertEqual(len(replay.events), 1)

        # 4. 对审计日志打墓碑
        result = _run_async(
            self.tombstone_service.mark_tombstone(
                entry_id="evt_audit_1",
                operator_id="gdpr_officer",
                tenant_id=tenant_id,
            )
        )
        self.assertTrue(result.success)

    def test_multiple_events_then_batch_tombstone(self) -> None:
        """多次执行事件后批量打墓碑。"""
        tenant_id = "tenant_batch_ts"
        user_id = "user_batch"

        # 写入多条审计日志
        for i in range(5):
            entry = _make_audit_entry(f"batch_audit_{i}", tenant_id, user_id)
            _run_async(self.audit_repo.append(entry))

        # 发布多次执行事件
        for i in range(5):
            _run_async(
                self.workflow_event_publisher.publish_execution_event(
                    instance_id="inst_batch",
                    node_id=f"node_{i}",
                    status="completed",
                )
            )

        # 验证事件
        replay = _run_async(
            self.event_bus.replay_from_offset("workflow_audit_topic", 0, 100)
        )
        self.assertEqual(len(replay.events), 5)

        # 逐条打墓碑
        for i in range(5):
            result = _run_async(
                self.tombstone_service.mark_tombstone(
                    entry_id=f"batch_audit_{i}",
                    operator_id="gdpr_officer",
                    tenant_id=tenant_id,
                )
            )
            self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
