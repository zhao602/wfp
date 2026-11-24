# =============================================================================
# 人工审核决策服务单测（tests/unit/test_review_service.py）
#
# 覆盖：arch_review_v2_tasks_wave2 任务 3.7 之二（CHG-54；AC-01 决策/续跑证据）
#       + 3.5 ReviewDecisionService 全用例 + 3.6 /review 路由改真（CHG-63/P7）。
# 分层：
#   A. 服务层前置——值域外拒绝/非 WAITING_REVIEW 拒绝（INSTANCE_NOT_WAITING_REVIEW）
#      /重复决策拒绝/无检查点（STATE_CHECKPOINT_NOT_FOUND）/latest 回退；
#   B. 决策语义——approve 续跑（resume_checkpoint 复用 + 单写点先于唤醒）/
#      reject 终态映射（reject→cancelled、wait→failed、缺省→cancelled）/
#      timeout_action→review_timeout/STATUS_CHANGED 留痕/P7 发布与降级；
#   C. 审计映射——governance.review_decision 经订阅器映射 UPDATE/SUCCESS 落盘；
#   D. 路由级端到端——挂起 → approve 续跑至 succeeded（不双发）/reject/
#      timeout_action/404/409/取值域外。
# =============================================================================

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from orchestration_platform.application.review_service import ReviewDecisionService
from orchestration_platform.domain.errors import (
    INSTANCE_NOT_WAITING_REVIEW,
    STATE_CHECKPOINT_NOT_FOUND,
    WF_VALIDATION_FAILED,
    DomainError,
    InstanceStateConflictError,
)
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.domain_event import DomainEvent
from orchestration_platform.domain.models.enums import (
    AuditOperationResult,
    EventType,
    OperationType,
)
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.governance.audit.audit_event_subscriber import (
    AuditEventSubscriber,
)
from orchestration_platform.governance.persistence.local_file.local_file_audit_log_repository import (
    LocalFileAuditLogRepository,
)
from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
    LocalFileCheckpointRepository,
)
from orchestration_platform.persistence.local_file.local_file_event_repository import (
    LocalFileEventRepository,
)

# 与 test_scheduler_suspend 同源的审核门流水线（文件自包含，CHG-54 独立可跑）
_REVIEW_GATE_YAML = """spec_version: "1.0"
definition_id: review_gate_flow
name: 人工审核门流水线
definition_version: 1

inputs:
  - name: topic
    type: string
    required: true
    description: 分析主题

outputs:
  - name: analysis
    type: object
    description: 分析结果

nodes:
  - node_id: trigger_manual
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual

  - node_id: review_gate
    name: 人工审核门
    node_type: control_flow
    configuration:
      control_flow_kind: human_review_gate
      review_instructions: 请审阅执行计划后放行
      review_timeout_action: reject

  - node_id: after_review
    name: 审阅后节点
    node_type: agent
    output_name: analysis
    configuration:
      model_identifier: primary-model
      system_prompt: 你是一名数据分析师。

edges:
  - edge_id: e1
    source_node_id: trigger_manual
    target_node_id: review_gate
  - edge_id: e2
    source_node_id: review_gate
    target_node_id: after_review
"""

_REVIEW_GATE_YAML_WAIT = _REVIEW_GATE_YAML.replace(
    "review_timeout_action: reject", "review_timeout_action: wait"
)


# =============================================================================
# 测试基建：内存仓储桩 + 记录发布器 + 挂起检查点工厂
# =============================================================================

class _FakeCheckpointRepository:
    """内存检查点仓储桩：get / latest_for_instance 可配置。"""

    def __init__(self, checkpoints=None, latest=None):
        self._checkpoints = {cp.checkpoint_id: cp for cp in (checkpoints or [])}
        self._latest = latest

    async def append(self, checkpoint: Checkpoint) -> str:
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        return checkpoint.checkpoint_id

    async def get(self, checkpoint_id: str):
        return self._checkpoints.get(checkpoint_id)

    async def latest_for_instance(self, instance_id: str):
        if self._latest is not None and self._latest.instance_id == instance_id:
            return self._latest
        candidates = [
            cp
            for cp in self._checkpoints.values()
            if cp.instance_id == instance_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda cp: cp.round_index)


class _FakeEventRepository:
    """内存执行事件仓储桩：收集 append 的事件供断言。"""

    def __init__(self):
        self.events = []

    async def append(self, event) -> int:
        self.events.append(event)
        return len(self.events)

    async def list_for_instance(self, instance_id: str, include_voided: bool = True):
        return [e for e in self.events if e.instance_id == instance_id]


class _RecordingPublisher:
    """发布记录桩：收集已发布领域事件（P7 断言用）。"""

    def __init__(self) -> None:
        self.events = []

    async def publish_domain_event(self, event) -> None:
        self.events.append(event)


class _FailingPublisher:
    """恒失败发布桩：验证发布失败降级吞异常（DC-31）。"""

    async def publish_domain_event(self, event) -> None:
        raise RuntimeError("发布器不可用（注入）")


def _mk_checkpoint(
    checkpoint_id="cp-gate",
    instance_id="inst-rv",
    completed="review_gate",
    next_node_id="after_review",
    round_index=1,
) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=checkpoint_id,
        instance_id=instance_id,
        completed_node_id=completed,
        next_node_id=next_node_id,
        state_snapshot=SharedState(instance_id=instance_id),
        round_index=round_index,
    )


def _gate_config(review_timeout_action="reject"):
    return SimpleNamespace(
        control_flow_kind="human_review_gate",
        review_instructions="请审阅",
        review_timeout_action=review_timeout_action,
    )


def _harness(
    checkpoint=None,
    latest=None,
    gate_config=None,
    publisher=None,
    current_status="waiting_review",
    suspended_checkpoint_id="unset",
    decision="approve",
):
    """组装服务 + 桩仓储 + 回调捕获字典；返回 (service, event_repo, captured, kwargs)。

    suspended_checkpoint_id="unset" 哨兵：默认取检查点自身 ID；显式传 None/其他值
    可构造「内存丢失」「指向缺失」等重启场景。
    """
    checkpoint = checkpoint if checkpoint is not None else _mk_checkpoint()
    if suspended_checkpoint_id == "unset":
        suspended_checkpoint_id = checkpoint.checkpoint_id
    checkpoint_repo = _FakeCheckpointRepository(
        checkpoints=[checkpoint], latest=latest
    )
    event_repo = _FakeEventRepository()
    service = ReviewDecisionService(checkpoint_repo, event_repo, publisher)
    order_log = []
    captured = {
        "statuses": [],
        "resumed": [],
        "order_log": order_log,
        "checkpoint": checkpoint,
    }

    def _set_status(status):
        captured["statuses"].append(status)
        order_log.append(("status", status))

    def _resume_runner(cp):
        captured["resumed"].append(cp)
        order_log.append(("resume", cp.checkpoint_id))

    kwargs = dict(
        instance_id="inst-rv",
        decision=decision,
        current_status=current_status,
        actor="reviewer-1",
        comment="同意放行",
        trace_id="trace-rv",
        tenant_id="tenant-1",
        suspended_checkpoint_id=suspended_checkpoint_id,
        get_gate_config=lambda node_id: gate_config,
        set_status=_set_status,
        resume_runner=_resume_runner,
    )
    return service, event_repo, captured, kwargs


# =============================================================================
# A. 服务层前置校验（3.5，DC-27 规则 2；spec §6.4 规则 2）
# =============================================================================

class TestReviewPreconditions:
    """前置状态拒绝 + 值域外拒绝 + 重复决策拒绝 + 检查点解析回退。"""

    async def test_non_waiting_review_rejected(self):
        """非 WAITING_REVIEW 实例提交决策 → INSTANCE_NOT_WAITING_REVIEW。"""
        service, _, _, kwargs = _harness(current_status="running")
        with pytest.raises(InstanceStateConflictError) as exc_info:
            await service.submit_decision(**kwargs)
        assert exc_info.value.code == INSTANCE_NOT_WAITING_REVIEW

    async def test_invalid_decision_value_rejected(self):
        """决策值域外 → WF_VALIDATION_FAILED（服务层独立防线）。"""
        service, _, _, kwargs = _harness(decision="bogus")
        with pytest.raises(DomainError) as exc_info:
            await service.submit_decision(**kwargs)
        assert exc_info.value.code == WF_VALIDATION_FAILED

    async def test_duplicate_decision_rejected(self):
        """重复决策拒绝：第一次 reject 置 cancelled 后，第二次提交被前置校验拒绝。"""
        service, _, captured, kwargs = _harness(
            gate_config=_gate_config("reject"), decision="reject"
        )
        first = await service.submit_decision(**kwargs)
        assert first["status"] == "cancelled"

        with pytest.raises(InstanceStateConflictError) as exc_info:
            await service.submit_decision(
                **{**kwargs, "current_status": "cancelled", "decision": "approve"}
            )
        assert exc_info.value.code == INSTANCE_NOT_WAITING_REVIEW, (
            "已决策实例的二次决策一律拒绝（前置校验天然幂等）"
        )
        assert captured["resumed"] == []

    async def test_missing_checkpoint_rejected(self):
        """挂起检查点解析双失败（get 未命中且 latest 回退亦无）→ STATE_CHECKPOINT_NOT_FOUND。"""
        service, _, _, kwargs = _harness(
            # 仓储内仅存他实例检查点：get(cp-missing) 未命中，
            # latest_for_instance("inst-rv") 回退亦无本实例检查点 → 双失败拒绝
            checkpoint=_mk_checkpoint(instance_id="inst-other"),
            suspended_checkpoint_id="cp-missing",
        )
        with pytest.raises(DomainError) as exc_info:
            await service.submit_decision(**kwargs)
        assert exc_info.value.code == STATE_CHECKPOINT_NOT_FOUND

    async def test_latest_for_instance_fallback_on_restart(self):
        """重启场景回退：内存登记指向缺失检查点 → latest_for_instance 兜底取回。"""
        latest_cp = _mk_checkpoint(checkpoint_id="cp-latest")
        service, _, captured, kwargs = _harness(
            checkpoint=_mk_checkpoint(checkpoint_id="cp-other-instance", instance_id="inst-other"),
            latest=latest_cp,
            suspended_checkpoint_id="cp-missing",
        )
        result = await service.submit_decision(**kwargs)
        assert captured["resumed"] == [latest_cp], (
            "suspended_checkpoint_id 缺失时经 latest_for_instance 回退取回挂起检查点"
        )
        assert result["resumed_from_checkpoint_id"] == "cp-latest"


# =============================================================================
# B. 决策语义（3.5，DC-27 规则 3/4；design §4.3 终态映射）
# =============================================================================

class TestDecisionSemantics:
    """approve 续跑 / reject 终态映射 / timeout_action / 留痕与发布。"""

    async def test_approve_resumes_with_single_write_point_before_wake(self):
        """approve：置 running → 留痕 → 唤醒，resume_checkpoint 复用挂起检查点。"""
        publisher = _RecordingPublisher()
        checkpoint = _mk_checkpoint()
        service, event_repo, captured, kwargs = _harness(
            checkpoint=checkpoint,
            gate_config=_gate_config("reject"),
            publisher=publisher,
        )
        result = await service.submit_decision(**kwargs)

        assert result["status"] == "running"
        assert result["resumed_from_checkpoint_id"] == checkpoint.checkpoint_id
        assert captured["resumed"] == [checkpoint], (
            "approve 经 resume_checkpoint 机制复用挂起检查点唤醒（PA-7）"
        )
        assert captured["order_log"] == [
            ("status", "running"),
            ("resume", checkpoint.checkpoint_id),
        ], "状态单写点先于唤醒（并发决策收窄竞态，§6.3 R4）"

    async def test_approve_records_status_changed_event(self):
        """决策留痕：STATUS_CHANGED 执行事件（既有 EventType 成员）载荷键齐全。"""
        service, event_repo, captured, kwargs = _harness(gate_config=_gate_config("reject"))
        await service.submit_decision(**kwargs)

        assert len(event_repo.events) == 1
        event = event_repo.events[0]
        assert event.event_type == EventType.STATUS_CHANGED
        assert event.instance_id == "inst-rv"
        assert event.node_id == "review_gate"
        assert event.payload["decision"] == "approve"
        assert event.payload["actor"] == "reviewer-1"
        assert event.payload["from_status"] == "waiting_review"
        assert event.payload["to_status"] == "running"
        assert event.payload["checkpoint_id"] == captured["checkpoint"].checkpoint_id
        assert event.payload["comment"] == "同意放行"

    async def test_approve_publishes_p7_governance_review_decision(self):
        """P7 发布：governance.review_decision 载荷键齐全（spec §6.4 规则 3）。"""
        publisher = _RecordingPublisher()
        service, _, captured, kwargs = _harness(publisher=publisher)
        await service.submit_decision(**kwargs)

        assert len(publisher.events) == 1
        event = publisher.events[0]
        assert event.event_type == "governance.review_decision"
        assert event.instance_id == "inst-rv"
        assert event.tenant_id == "tenant-1"
        assert event.node_id == "review_gate"
        assert event.payload["instance_id"] == "inst-rv"
        assert event.payload["node_id"] == "review_gate"
        assert event.payload["decision"] == "approve"
        assert event.payload["actor"] == "reviewer-1"
        assert event.payload["comment"] == "同意放行"

    async def test_publisher_failure_swallowed(self):
        """发布器失败降级吞异常：决策与留痕不受影响（DC-31）。"""
        service, event_repo, captured, kwargs = _harness(publisher=_FailingPublisher())
        result = await service.submit_decision(**kwargs)
        assert result["status"] == "running"
        assert len(event_repo.events) == 1, "执行事件留痕独立于发布器"
        assert captured["resumed"], "发布失败不阻断唤醒"

    async def test_without_publisher_still_records(self):
        """发布器缺省 None：零发布、留痕完整（基线旁路行为）。"""
        service, event_repo, captured, kwargs = _harness(publisher=None)
        result = await service.submit_decision(**kwargs)
        assert result["status"] == "running"
        assert len(event_repo.events) == 1

    async def test_reject_default_maps_to_cancelled(self):
        """reject + review_timeout_action=reject（默认）→ cancelled（词表 reconcile）。"""
        service, _, captured, kwargs = _harness(
            gate_config=_gate_config("reject"), decision="reject"
        )
        result = await service.submit_decision(**kwargs)
        assert result["status"] == "cancelled"
        assert captured["statuses"] == ["cancelled"]
        assert captured["resumed"] == [], "reject 不自动重跑"

    async def test_reject_wait_maps_to_failed(self):
        """reject + review_timeout_action=wait → failed（design fail 分支）。"""
        service, _, captured, kwargs = _harness(
            gate_config=_gate_config("wait"), decision="reject"
        )
        result = await service.submit_decision(**kwargs)
        assert result["status"] == "failed"
        assert captured["resumed"] == []

    async def test_reject_missing_gate_config_defaults_cancelled(self):
        """reject + 门配置缺失（重启上下文丢失）→ 默认 cancel 分支 cancelled。"""
        service, _, captured, kwargs = _harness(gate_config=None, decision="reject")
        result = await service.submit_decision(**kwargs)
        assert result["status"] == "cancelled"

    async def test_timeout_action_maps_to_review_timeout(self):
        """timeout_action 显式提交 → REVIEW_TIMEOUT 既有枚举值置态。"""
        service, _, captured, kwargs = _harness(decision="timeout_action")
        result = await service.submit_decision(**kwargs)
        assert result["status"] == "review_timeout"
        assert captured["resumed"] == []


# =============================================================================
# C. 审计映射（3.8 门禁④ 依据：P7 事件经订阅器落盘）
# =============================================================================

class TestDecisionAuditTrail:
    """governance.review_decision 经审计订阅映射 UPDATE/SUCCESS 落盘。"""

    async def test_review_decision_event_maps_to_audit_update(self, tmp_path):
        repo = LocalFileAuditLogRepository(storage_root=str(tmp_path))
        subscriber = AuditEventSubscriber(primary_repository=repo)

        event = DomainEvent(
            event_type="governance.review_decision",
            instance_id="inst-rv",
            tenant_id="tenant-1",
            trace_id="trace-rv",
            node_id="review_gate",
            payload={
                "instance_id": "inst-rv",
                "node_id": "review_gate",
                "decision": "approve",
                "actor": "reviewer-1",
                "comment": "同意放行",
            },
        )
        entry = await subscriber.handle(event)

        assert entry is not None
        assert entry.operation == OperationType.UPDATE
        assert entry.operation_result == AuditOperationResult.SUCCESS
        assert entry.actor_id == "reviewer-1", "决策人取自 P7 载荷 actor 字段"
        persisted = await repo.query(tenant_id="tenant-1")
        assert [e.entry_id for e in persisted] == [entry.entry_id]


# =============================================================================
# D. 路由级端到端（3.6，CHG-63/P7；AC-01 沙箱链：挂起 → 决策 → 续跑终态）
# =============================================================================

class _CountingStubExecutor:
    """计数桩执行器（路由级 agent 桩，after_review 不依赖 LLM）。"""

    def __init__(self, node_type):

        self._node_type = node_type
        self.node_calls: dict = {}

    @property
    def node_type(self):
        return self._node_type

    async def execute(self, context):
        from orchestration_platform.engine.nodes.base import NodeExecutionResult

        node_id = context.node_definition.node_id
        self.node_calls[node_id] = self.node_calls.get(node_id, 0) + 1
        return NodeExecutionResult(
            success=True,
            output_name=getattr(
                context.node_definition, "effective_output_name", None
            ),
            output_value={"stub": node_id},
        )


@pytest.fixture
def routed_storage_root(tmp_path, monkeypatch):
    """LOCAL_STORAGE_ROOT 重定向 + settings 缓存刷新（路由侧仓储工厂取根）。"""
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    from orchestration_platform.configuration.settings import get_settings

    get_settings.cache_clear()
    try:
        yield root
    finally:
        get_settings.cache_clear()


@pytest.fixture
def stubbed_agent_executor():
    """热替换路由执行器注册表 agent 执行器为计数桩。"""
    import orchestration_platform.api.routers.execution_instance_router as router_mod
    from orchestration_platform.domain.models.enums import NodeType

    registry = router_mod._executor_registry
    originals = dict(registry._executors)
    agent_stub = _CountingStubExecutor(NodeType.AGENT)
    registry.register(agent_stub)
    try:
        yield agent_stub
    finally:
        registry._executors.clear()
        registry._executors.update(originals)


@pytest.fixture
def recording_publisher():
    """注入记录型领域事件发布器（路由模块全局，测试后恢复原值）。"""
    import orchestration_platform.api.routers.execution_instance_router as router_mod

    publisher = _RecordingPublisher()
    original = router_mod._domain_event_publisher
    router_mod.set_domain_event_publisher(publisher)
    try:
        yield publisher
    finally:
        router_mod.set_domain_event_publisher(original)


async def _start_and_suspend(yaml_text=None, inputs_topic="审批流"):
    """启动审核门流水线并等待挂起：返回 (instance_id, rt)。"""
    import orchestration_platform.api.routers.execution_instance_router as router_mod
    from orchestration_platform.api.routers.execution_instance_router import (
        StartInstanceBody,
        start_instance,
    )

    request = SimpleNamespace(state=SimpleNamespace(tenant_id=None))
    body = StartInstanceBody(
        definition_id="review_gate_flow",
        yaml_text=yaml_text or _REVIEW_GATE_YAML,
        inputs={"topic": inputs_topic},
    )
    result = await start_instance(body, request)
    rt = router_mod._instance_registry.get(result["instance_id"])
    await rt.task
    assert rt.status == "waiting_review"
    return result["instance_id"], rt


class TestReviewRouteEndToEnd:
    """/review 路由改真（CHG-63）：端到端决策与续跑（AC-01 沙箱链）。"""

    async def test_approve_resumes_to_success(
        self, routed_storage_root, stubbed_agent_executor, recording_publisher
    ):
        """AC-01 端到端：挂起 → approve API → resume_checkpoint 续跑至 succeeded。"""
        from orchestration_platform.api.routers.rollback_replay_router import (
            ReviewBody,
            submit_review,
        )

        instance_id, rt = await _start_and_suspend()
        suspended_cp = rt.suspended_checkpoint_id

        result = await submit_review(
            instance_id, ReviewBody(decision="approve", actor="reviewer-1")
        )
        assert result["status"] == "running"
        assert result["resumed_from_checkpoint_id"] == suspended_cp

        rt.task and await rt.task  # 续跑任务完成
        import orchestration_platform.api.routers.execution_instance_router as router_mod

        rt = router_mod._instance_registry.get(instance_id)
        assert rt.status == "succeeded", "approve 续跑至成功终态"
        assert stubbed_agent_executor.node_calls.get("after_review") == 1, (
            "唤醒自挂起检查点 next_node_id 续跑（after_review 恰执行一次）"
        )

        # 检查点链：trigger(0) + gate(1) + after_review(2)，无同轮重复
        repo = LocalFileCheckpointRepository()
        summaries = sorted(
            await repo.list_for_instance(instance_id),
            key=lambda s: s.round_index,
        )
        assert [s.round_index for s in summaries] == [0, 1, 2]

        # 决策留痕：STATUS_CHANGED 落持久化事件仓储
        event_repo = LocalFileEventRepository()
        events = await event_repo.list_for_instance(instance_id)
        status_changed = [
            e for e in events if e.event_type == EventType.STATUS_CHANGED
        ]
        assert len(status_changed) == 1
        assert status_changed[0].payload["decision"] == "approve"
        assert status_changed[0].payload["actor"] == "reviewer-1"
        assert status_changed[0].payload["to_status"] == "running"

        # P7 决策发布 + P1 续跑 trigger_source=review_resume（不双发，拍板项 #6）
        p7 = [
            e
            for e in recording_publisher.events
            if e.event_type == "governance.review_decision"
        ]
        assert len(p7) == 1
        assert p7[0].payload["actor"] == "reviewer-1"
        started = [
            e
            for e in recording_publisher.events
            if e.event_type == "instance.started"
        ]
        assert any(e.payload.get("trigger_source") == "review_resume" for e in started), (
            "续跑 instance.started（P1 变体）即唤醒信号"
        )
        assert not any(
            e.event_type == "instance.resumed" for e in recording_publisher.events
        ), "不另发 instance.resumed（合并口径，不留双发）"

    async def test_reject_maps_to_cancelled(self, routed_storage_root, stubbed_agent_executor):
        """reject（review_timeout_action=reject 默认）→ cancelled 终态，不重跑。"""
        from orchestration_platform.api.routers.rollback_replay_router import (
            ReviewBody,
            submit_review,
        )

        instance_id, rt = await _start_and_suspend()

        result = await submit_review(
            instance_id, ReviewBody(decision="reject", actor="reviewer-1", comment="驳回")
        )
        assert result["status"] == "cancelled"
        assert "resumed_from_checkpoint_id" not in result

        import orchestration_platform.api.routers.execution_instance_router as router_mod

        rt = router_mod._instance_registry.get(instance_id)
        assert rt.status == "cancelled"
        assert stubbed_agent_executor.node_calls == {}, "reject 不自动重跑"

        event_repo = LocalFileEventRepository()
        events = await event_repo.list_for_instance(instance_id)
        status_changed = [
            e for e in events if e.event_type == EventType.STATUS_CHANGED
        ]
        assert len(status_changed) == 1
        assert status_changed[0].payload["decision"] == "reject"
        assert status_changed[0].payload["to_status"] == "cancelled"

    async def test_reject_wait_maps_to_failed(self, routed_storage_root, stubbed_agent_executor):
        """reject + review_timeout_action=wait → failed（design fail 分支）。"""
        from orchestration_platform.api.routers.rollback_replay_router import (
            ReviewBody,
            submit_review,
        )

        instance_id, rt = await _start_and_suspend(yaml_text=_REVIEW_GATE_YAML_WAIT)

        result = await submit_review(
            instance_id, ReviewBody(decision="reject", actor="reviewer-1")
        )
        assert result["status"] == "failed"

    async def test_timeout_action_maps_to_review_timeout(self, routed_storage_root, stubbed_agent_executor):
        """timeout_action 显式提交 → review_timeout 置态。"""
        from orchestration_platform.api.routers.rollback_replay_router import (
            ReviewBody,
            submit_review,
        )

        instance_id, rt = await _start_and_suspend()

        result = await submit_review(
            instance_id, ReviewBody(decision="timeout_action", actor="system")
        )
        assert result["status"] == "review_timeout"

    async def test_review_unknown_instance_404(self):
        """实例不存在 → 404（注册表 get 抛 HTTPException）。"""
        from orchestration_platform.api.routers.rollback_replay_router import (
            ReviewBody,
            submit_review,
        )

        with pytest.raises(HTTPException) as exc_info:
            await submit_review(
                "no-such-instance", ReviewBody(decision="approve", actor="u")
            )
        assert exc_info.value.status_code == 404

    async def test_review_non_waiting_instance_409(self, routed_storage_root):
        """非 WAITING_REVIEW 实例提交决策 → 409 INSTANCE_NOT_WAITING_REVIEW。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod
        from orchestration_platform.api.routers.rollback_replay_router import (
            ReviewBody,
            submit_review,
        )

        rt = router_mod.InstanceRuntime(instance_id="inst-done", definition_id="d")
        rt.compiled = object()
        rt.status = "succeeded"
        router_mod._instance_registry.register(rt)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await submit_review(
                    "inst-done", ReviewBody(decision="approve", actor="u")
                )
            assert exc_info.value.status_code == 409
            assert "waiting_review" in str(exc_info.value.detail)
        finally:
            router_mod._instance_registry.remove("inst-done")

    def test_review_body_rejects_out_of_domain_decision(self):
        """三值取值域外输入拒绝（Literal 强类型，路由层 422 语义）。"""
        from orchestration_platform.api.routers.rollback_replay_router import ReviewBody

        with pytest.raises(ValidationError):
            ReviewBody(decision="bogus", actor="u")
        with pytest.raises(ValidationError):
            ReviewBody(decision="approve")  # actor 必填
        ReviewBody(decision="approve", actor="u")  # 合法构造
