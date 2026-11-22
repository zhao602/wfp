# =============================================================================
# 回滚/重放路由接线单测（tests/unit/test_replay_wiring.py）
#
# 覆盖：arch_review_v2_tasks_wave2 任务 5.1/5.2/5.3/5.4/5.5（CHG-57 + CHG-63/
#       CHG-50/CHG-60；AC-03）。
# 分层：
#   A. strict_replay 一致断言（服务 _strict_replay 零改动复用 + 路由层
#      compared_outputs 逐节点一致摘要 + REPLAY 留痕 + strict 不触发幂等护栏）；
#   B. re_execute 新实例 fork（{origin}-replay-{N} 注册 + resume 真跑 +
#      原记录零覆盖 + trigger_source=re_execute + P9 instance.replayed）；
#   C. divergent 确定性对比正反例（白名单节点差异 → divergent_node_ids；
#      非确定性节点差异 → divergent_reason="nondeterministic" 不计 divergent，
#      R6 伪证反例守护）；
#   D. 幂等拒绝（REPLAY_ALREADY_IN_PROGRESS 409）与解除（活跃实例消失放行）；
#   E. RUNNING 拒绝（rollback/replay 双路由）+ 模式值外 422 + 检查点 404；
#   F. rollback 实测（位点恢复 + void_after + PENDING + P9 instance.rolled_back）。
# 纪律：路由直调 + 同步等待驱动（对齐 test_review_service 范式，spec FR-08）；
#      测试文件自包含（桩/固件不跨文件导入）。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_replay_wiring.py -q
# =============================================================================

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from orchestration_platform.api.routers.rollback_replay_router import (
    ReplayBody,
    RollbackBody,
    replay_instance,
    rollback_instance,
)
from orchestration_platform.domain.errors import REPLAY_ALREADY_IN_PROGRESS
from orchestration_platform.domain.models.enums import EventType
from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
    LocalFileCheckpointRepository,
)
from orchestration_platform.persistence.local_file.local_file_event_repository import (
    LocalFileEventRepository,
)

# 重放接线流水线（对比载体）：trigger_manual（TRIGGER/manual，真实执行器
# 入口标记）→ transform_a（DATA_TRANSFORM，可控桩——确定性白名单正例载体）
# → agent_b（AGENT/output_name=analysis，可控桩——非确定性反例载体）。
# 检查点轮次（scheduler 每节点后写检查点）：trigger 完成=0、transform 完成=1、
# agent 完成=2。
_REPLAY_FLOW_YAML = """spec_version: "1.0"
definition_id: replay_flow
name: 重放接线流水线
definition_version: 1
inputs:
  - name: items
    type: array
    required: true
    description: 待转换数据集

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

  - node_id: transform_a
    name: 数据转换
    node_type: data_transform
    configuration:
      operation: map
      input_dataset_reference: items

  - node_id: agent_b
    name: 分析节点
    node_type: agent
    output_name: analysis
    configuration:
      model_identifier: primary-model
      system_prompt: 你是一名数据分析师。

edges:
  - edge_id: e1
    source_node_id: trigger_manual
    target_node_id: transform_a
  - edge_id: e2
    source_node_id: transform_a
    target_node_id: agent_b
"""


class _ControllableStubExecutor:
    """可控桩执行器：输出值可跨运行翻转（divergent 正反例载体）。

    对齐 test_review_service._CountingStubExecutor 范式（node_calls 计数 +
    effective_output_name 输出），追加 values 表支持输出翻转。
    """

    def __init__(self, node_type) -> None:
        self._node_type = node_type
        self.node_calls: dict = {}
        self.values: dict = {}  # node_id -> 输出值；缺省 {"stub": node_id}

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
            output_value=self.values.get(node_id, {"stub": node_id}),
        )


class _RecordingPublisher:
    """发布记录桩：收集已发布领域事件（P9 断言用）。"""

    def __init__(self) -> None:
        self.events = []

    async def publish_domain_event(self, event) -> None:
        self.events.append(event)


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
def stubbed_transform_executor():
    """热替换路由执行器注册表 DATA_TRANSFORM 执行器为可控桩。"""
    import orchestration_platform.api.routers.execution_instance_router as router_mod
    from orchestration_platform.domain.models.enums import NodeType

    registry = router_mod._executor_registry
    originals = dict(registry._executors)
    stub = _ControllableStubExecutor(NodeType.DATA_TRANSFORM)
    registry.register(stub)
    try:
        yield stub
    finally:
        registry._executors.clear()
        registry._executors.update(originals)


@pytest.fixture
def stubbed_agent_executor():
    """热替换路由执行器注册表 agent 执行器为可控桩。"""
    import orchestration_platform.api.routers.execution_instance_router as router_mod
    from orchestration_platform.domain.models.enums import NodeType

    registry = router_mod._executor_registry
    originals = dict(registry._executors)
    stub = _ControllableStubExecutor(NodeType.AGENT)
    registry.register(stub)
    try:
        yield stub
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


def _get_rt(instance_id: str):
    """取实例运行时（注册表模块单例）。"""
    import orchestration_platform.api.routers.execution_instance_router as router_mod

    return router_mod._instance_registry.get(instance_id)


async def _start_and_complete():
    """启动重放流水线并同步等待完成：返回 (instance_id, rt)。"""
    from orchestration_platform.api.routers.execution_instance_router import (
        StartInstanceBody,
        start_instance,
    )

    request = SimpleNamespace(state=SimpleNamespace(tenant_id=None))
    body = StartInstanceBody(
        definition_id="replay_flow",
        yaml_text=_REPLAY_FLOW_YAML,
        inputs={"items": [{"id": 1}, {"id": 2}]},
    )
    result = await start_instance(body, request)
    rt = _get_rt(result["instance_id"])
    await rt.task
    assert rt.status == "succeeded"
    return result["instance_id"], rt


async def _pick_checkpoint(instance_id: str, round_index: int) -> str:
    """按轮次取检查点 ID（0=trigger 后，1=transform 后，2=agent 后）。"""
    repo = LocalFileCheckpointRepository()
    summaries = await repo.list_for_instance(instance_id)
    for summary in summaries:
        if summary.round_index == round_index:
            return summary.checkpoint_id
    raise AssertionError(
        f"未找到 round_index={round_index} 检查点: {instance_id}"
    )


# =============================================================================
# A. strict_replay 一致断言（AC-03 前半，任务 5.1；CHG-57 之一）
# =============================================================================
class TestStrictReplayConsistency:
    """strict：服务零改动复用 + 路由层 compared_outputs 摘要。"""

    async def test_strict_replay_consistent_summary(
        self,
        routed_storage_root,
        stubbed_transform_executor,
        stubbed_agent_executor,
        recording_publisher,
    ):
        instance_id, rt = await _start_and_complete()
        origin_event_count = len(rt.events)
        cp_after_transform = await _pick_checkpoint(instance_id, 1)

        result = await replay_instance(
            instance_id,
            ReplayBody(checkpoint_id=cp_after_transform, mode="strict_replay"),
        )

        # strict 一致性（AC-03 前半）：复用历史 node_outputs，零真实重跑
        assert result["consistent"] is True
        assert result["divergent_node_ids"] == []
        assert result["mode"] == "strict_replay"
        assert result["replay_instance_id"] is None, "strict 不 fork 新实例"
        assert result["new_event_count"] == 1, "仅 REPLAY 留痕事件"

        # compared_outputs：origin 快照有输出的节点全量一致（三节点）
        summary = {c["node_id"]: c for c in result["compared_outputs"]}
        assert set(summary) == {"trigger_manual", "transform_a", "agent_b"}
        assert all(c["consistent"] is True for c in summary.values())

        # 原记录零覆盖：注册表事件数不变（strict 不产生新执行事件）
        assert len(_get_rt(instance_id).events) == origin_event_count

        # REPLAY 留痕（origin 持久化事件流）
        event_repo = LocalFileEventRepository()
        events = await event_repo.list_for_instance(instance_id)
        replay_marks = [e for e in events if e.event_type == EventType.REPLAY]
        assert len(replay_marks) == 1
        assert replay_marks[0].payload["mode"] == "strict_replay"
        assert replay_marks[0].payload["checkpoint_id"] == cp_after_transform

        # P9 instance.replayed 发布（strict：replay_instance_id=None）
        p9 = [
            e
            for e in recording_publisher.events
            if e.event_type == "instance.replayed"
        ]
        assert len(p9) == 1
        assert p9[0].payload["origin_instance_id"] == instance_id
        assert p9[0].payload["replay_instance_id"] is None
        assert p9[0].payload["divergent_node_ids"] == []

        # strict 幂等护栏永不触发（无重放实例）：同键再次 strict 放行
        again = await replay_instance(
            instance_id,
            ReplayBody(checkpoint_id=cp_after_transform, mode="strict_replay"),
        )
        assert again["consistent"] is True


# =============================================================================
# B. re_execute 新实例 fork（AC-03 后半，任务 5.2；原记录零覆盖）
# =============================================================================
class TestReExecuteFork:
    """re_execute：{origin}-replay-{N} fork + resume 真跑 + 独立事件流。"""

    async def test_re_execute_forks_new_instance_and_preserves_origin(
        self,
        routed_storage_root,
        stubbed_transform_executor,
        stubbed_agent_executor,
        recording_publisher,
    ):
        instance_id, rt = await _start_and_complete()
        origin_events = list(rt.events)
        cp_after_transform = await _pick_checkpoint(instance_id, 1)
        agent_calls_before = dict(stubbed_agent_executor.node_calls)

        result = await replay_instance(
            instance_id,
            ReplayBody(checkpoint_id=cp_after_transform, mode="re_execute"),
        )

        replay_id = f"{instance_id}-replay-1"
        assert result["replay_instance_id"] == replay_id
        assert result["mode"] == "re_execute"
        assert result["consistent"] is True
        assert result["divergent_node_ids"] == []
        assert result["new_event_count"] >= 2, "REPLAY 留痕 + 新实例独立事件流"

        # 新实例注册 + 真跑至终态：agent 自检查点后重跑恰一次
        new_rt = _get_rt(replay_id)
        assert new_rt.status == "succeeded"
        assert new_rt.task is not None and new_rt.task.done()
        assert (
            stubbed_agent_executor.node_calls["agent_b"]
            == agent_calls_before.get("agent_b", 0) + 1
        )
        assert (
            stubbed_transform_executor.node_calls.get("transform_a", 0) == 1
        ), "检查点前节点不重跑（resume 自 transform 完成位点）"

        # 原记录零覆盖：origin 注册表事件流零 diff（新实例独立流）
        assert _get_rt(instance_id).events == origin_events

        # 新实例 instance.started 携 trigger_source=re_execute（P1 变体）
        started = [
            e
            for e in recording_publisher.events
            if e.event_type == "instance.started" and e.instance_id == replay_id
        ]
        assert len(started) == 1
        assert started[0].payload["trigger_source"] == "re_execute"

        # P9 instance.replayed：replay_instance_id 回填（附录 A.1 载荷）
        p9 = [
            e
            for e in recording_publisher.events
            if e.event_type == "instance.replayed"
        ]
        assert len(p9) == 1
        assert p9[0].payload["replay_instance_id"] == replay_id
        assert p9[0].payload["origin_instance_id"] == instance_id
        assert p9[0].payload["divergent_node_ids"] == []


# =============================================================================
# C. divergent 确定性对比正反例（任务 5.4；R6 伪证反例守护）
# =============================================================================
class TestDivergentComparison:
    """白名单节点差异 → divergent；白名单外差异 → 如实标注不计 divergent。"""

    async def test_deterministic_divergence_flags_transform(
        self,
        routed_storage_root,
        stubbed_transform_executor,
        stubbed_agent_executor,
        recording_publisher,
    ):
        """正例：翻转确定性节点（DATA_TRANSFORM）输出 → output_diverged。"""
        instance_id, _ = await _start_and_complete()
        stubbed_transform_executor.values["transform_a"] = {"flipped": True}
        cp_after_trigger = await _pick_checkpoint(instance_id, 0)

        result = await replay_instance(
            instance_id,
            ReplayBody(checkpoint_id=cp_after_trigger, mode="re_execute"),
        )

        assert result["consistent"] is False
        assert result["divergent_node_ids"] == ["transform_a"]
        assert result["replay_instance_id"] == f"{instance_id}-replay-1"
        summary = {c["node_id"]: c for c in result["compared_outputs"]}
        assert summary["transform_a"]["consistent"] is False
        assert summary["transform_a"]["divergent_reason"] == "output_diverged"
        assert summary["agent_b"]["consistent"] is True, "agent 桩值未翻 → 一致"
        assert summary["trigger_manual"]["consistent"] is True
        # 确定性节点自 trigger 位点真重跑（第二次执行取翻转值）
        assert stubbed_transform_executor.node_calls["transform_a"] == 2

    async def test_nondeterministic_divergence_annotated_not_flagged(
        self,
        routed_storage_root,
        stubbed_transform_executor,
        stubbed_agent_executor,
        recording_publisher,
    ):
        """反例：非确定性节点（AGENT）差异 → 如实标注、不计 divergent。"""
        instance_id, _ = await _start_and_complete()
        stubbed_agent_executor.values["agent_b"] = {"flipped": True}
        cp_after_transform = await _pick_checkpoint(instance_id, 1)

        result = await replay_instance(
            instance_id,
            ReplayBody(checkpoint_id=cp_after_transform, mode="re_execute"),
        )

        # R6 伪证反例守护：概率性节点差异如实标注，不伪称一致也不计 divergent
        assert result["divergent_node_ids"] == []
        assert result["consistent"] is True
        summary = {c["node_id"]: c for c in result["compared_outputs"]}
        assert summary["agent_b"]["consistent"] is False
        assert summary["agent_b"]["divergent_reason"] == "nondeterministic"
        assert summary["transform_a"]["consistent"] is True
        # P9 载荷与响应口径一致（空 divergent 清单，审计留痕不夸大）
        p9 = [
            e
            for e in recording_publisher.events
            if e.event_type == "instance.replayed"
        ]
        assert p9[-1].payload["divergent_node_ids"] == []


# =============================================================================
# D. 幂等护栏（任务 5.3；errors.py G4 REPLAY_ALREADY_IN_PROGRESS）
# =============================================================================
class TestReplayIdempotencyGuard:
    """同（mode, checkpoint_id）留痕 + 活跃重放在途 → 409；活跃消失 → 放行。"""

    async def test_active_replay_rejected_then_released(
        self,
        routed_storage_root,
        stubbed_transform_executor,
        stubbed_agent_executor,
        recording_publisher,
    ):
        import orchestration_platform.api.routers.execution_instance_router as router_mod

        instance_id, _ = await _start_and_complete()
        cp_after_transform = await _pick_checkpoint(instance_id, 1)

        # 首次 re_execute 正常完成（replay-1 终态，不算活跃）
        first = await replay_instance(
            instance_id,
            ReplayBody(checkpoint_id=cp_after_transform, mode="re_execute"),
        )
        assert first["replay_instance_id"] == f"{instance_id}-replay-1"

        # 伪造在途重放实例（-replay-9：task 未完成 + running）→ 同键再发起 409
        fake = router_mod.InstanceRuntime(
            instance_id=f"{instance_id}-replay-9", definition_id="replay_flow"
        )
        fake.status = "running"
        fake_task = asyncio.create_task(asyncio.sleep(30))
        fake.task = fake_task
        router_mod._instance_registry.register(fake)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await replay_instance(
                    instance_id,
                    ReplayBody(
                        checkpoint_id=cp_after_transform, mode="re_execute"
                    ),
                )
            assert exc_info.value.status_code == 409
            assert (
                exc_info.value.detail["code"] == REPLAY_ALREADY_IN_PROGRESS
            ), "409 detail 结构化载荷（非纯字符串）"
        finally:
            fake_task.cancel()
            try:
                await fake_task
            except asyncio.CancelledError:
                pass
            router_mod._instance_registry.remove(fake.instance_id)

        # 活跃实例消失 → 同键再次发起放行（序号递增，DC-30 规则 4）
        second = await replay_instance(
            instance_id,
            ReplayBody(checkpoint_id=cp_after_transform, mode="re_execute"),
        )
        assert second["replay_instance_id"] == f"{instance_id}-replay-2"


# =============================================================================
# E. 路由守卫：RUNNING 409 / 模式值外 422 / 检查点不存在 404
# =============================================================================
class TestRouteGuards:
    """回滚/重放双路由前置校验（DC-30 规则 1/4）。"""

    async def test_running_instance_rejected_on_both_routes(
        self, routed_storage_root
    ):
        import orchestration_platform.api.routers.execution_instance_router as router_mod

        fake_id = "guard-running-instance"
        fake = router_mod.InstanceRuntime(
            instance_id=fake_id, definition_id="replay_flow"
        )
        fake.status = "running"
        fake_task = asyncio.create_task(asyncio.sleep(30))
        fake.task = fake_task
        router_mod._instance_registry.register(fake)
        try:
            with pytest.raises(HTTPException) as rollback_exc:
                await rollback_instance(
                    fake_id, RollbackBody(target_checkpoint_id="cp-any")
                )
            assert rollback_exc.value.status_code == 409

            with pytest.raises(HTTPException) as replay_exc:
                await replay_instance(
                    fake_id,
                    ReplayBody(checkpoint_id="cp-any", mode="strict_replay"),
                )
            assert replay_exc.value.status_code == 409
        finally:
            fake_task.cancel()
            try:
                await fake_task
            except asyncio.CancelledError:
                pass
            router_mod._instance_registry.remove(fake_id)

    async def test_unknown_mode_rejected_422(
        self,
        routed_storage_root,
        stubbed_transform_executor,
        stubbed_agent_executor,
        recording_publisher,
    ):
        instance_id, _ = await _start_and_complete()
        cp_after_transform = await _pick_checkpoint(instance_id, 1)
        with pytest.raises(HTTPException) as exc_info:
            await replay_instance(
                instance_id,
                ReplayBody(checkpoint_id=cp_after_transform, mode="unknown_mode"),
            )
        assert exc_info.value.status_code == 422

    async def test_unknown_checkpoint_404_both_routes(
        self,
        routed_storage_root,
        stubbed_transform_executor,
        stubbed_agent_executor,
        recording_publisher,
    ):
        instance_id, _ = await _start_and_complete()
        with pytest.raises(HTTPException) as rollback_exc:
            await rollback_instance(
                instance_id, RollbackBody(target_checkpoint_id="cp-does-not-exist")
            )
        assert rollback_exc.value.status_code == 404

        with pytest.raises(HTTPException) as replay_exc:
            await replay_instance(
                instance_id,
                ReplayBody(checkpoint_id="cp-does-not-exist", mode="strict_replay"),
            )
        assert replay_exc.value.status_code == 404


# =============================================================================
# F. rollback 路由实测（任务 5.1；AC-03 回滚侧证据）
# =============================================================================
class TestRollbackRoute:
    """位点恢复 + void_after + PENDING + ROLLBACK 留痕 + P9 instance.rolled_back。"""

    async def test_rollback_restores_position_and_audits(
        self,
        routed_storage_root,
        stubbed_transform_executor,
        stubbed_agent_executor,
        recording_publisher,
    ):
        instance_id, _ = await _start_and_complete()
        cp_after_transform = await _pick_checkpoint(instance_id, 1)

        result = await rollback_instance(
            instance_id, RollbackBody(target_checkpoint_id=cp_after_transform)
        )

        assert result["instance_id"] == instance_id
        assert result["restored_checkpoint_id"] == cp_after_transform
        assert result["restored_round_index"] == 1
        # 正常执行链路不向持久化事件流写节点事件 → 作废计数如实为 0
        assert result["voided_event_count"] == 0

        # 注册表状态回写：PENDING（rollback ≠ replay，回滚后再执行走正常启动）
        assert _get_rt(instance_id).status == "pending"

        # ROLLBACK 事件留痕（持久化事件流）
        event_repo = LocalFileEventRepository()
        events = await event_repo.list_for_instance(instance_id)
        assert any(e.event_type == EventType.ROLLBACK for e in events)

        # P9：instance.rolled_back 发布（附录 A.1 载荷，审计留痕）
        p9 = [
            e
            for e in recording_publisher.events
            if e.event_type == "instance.rolled_back"
        ]
        assert len(p9) == 1
        assert p9[0].payload["target_checkpoint_id"] == cp_after_transform
        assert p9[0].payload["restored_round_index"] == 1
        assert p9[0].payload["voided_event_count"] == 0


