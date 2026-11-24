# =============================================================================
# M5 /recover 与 /review 端点 worker 读穿透单测
# （tests/unit/test_recover_review_endpoint_worker.py）
#
# 覆盖（design D7/D8，T6）：
#   A. /recover worker——快照守卫（running/waiting_review/review_timeout →
#      409；review_timeout 拒绝 = 防绕过已留痕审核决策，实现期安全补强）；
#      终态检查点续跑作业入队（幂等键 {iid}:recover:{cp}、origin 载荷
#      yaml/租户/版本/trace 补齐、initial_inputs={}、trigger_source=recover）；
#      pending 窗口无检查点 → 500 如实口径；重复恢复同键幂等；队列不可用
#      → 500；
#   B. /review worker——非 waiting_review → 409（detail 同文案）；无挂起
#      检查点 → 404；approve → STATUS_CHANGED(running) 留痕 + 续跑作业入队
#      （幂等键 :review-resume:、trigger_source=review_resume）；reject →
#      cancelled 终态映射不入队；timeout_action → review_timeout；编译
#      重建失败 → 409。
# 隔离：LOCAL_STORAGE_ROOT 重定向 + 事件/检查点仓储注入 + 队列注入。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_recover_review_endpoint_worker.py -q
# =============================================================================

import uuid
from datetime import datetime, timezone

import pytest

from orchestration_platform.api import instance_read_through as read_through
from orchestration_platform.api.instance_read_through import read_instance_snapshot
from orchestration_platform.api.routers import execution_instance_router as inst_router
from orchestration_platform.api.routers.rollback_replay_router import (
    ReviewBody,
    submit_review,
)
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.enums import EventType
from orchestration_platform.domain.models.execution_event import ExecutionEvent
from orchestration_platform.domain.models.execution_job import ExecutionJob
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
    LocalFileCheckpointRepository,
)
from orchestration_platform.persistence.local_file.local_file_event_repository import (
    LocalFileEventRepository,
)
from orchestration_platform.persistence.local_file.local_file_execution_queue import (
    LocalFileExecutionQueue,
)
from fastapi import HTTPException

_YAML = """spec_version: "1.0"
definition_id: m5_recover_flow
name: M5 恢复审核流水线
definition_version: 1
inputs:
  - name: items
    type: array
    required: true

outputs:
  - name: analysis
    type: object

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


@pytest.fixture
def routed_env(tmp_path, monkeypatch):
    """存储根重定向 + settings 缓存刷新收口。"""
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def event_repo(routed_env, monkeypatch) -> LocalFileEventRepository:
    """独立临时目录事件仓储（读穿透 + 决策留痕注入）。"""
    repo = LocalFileEventRepository()
    monkeypatch.setattr(read_through, "_event_repository_instance", repo)
    monkeypatch.setattr(read_through, "_event_repository_failed", False)
    return repo


@pytest.fixture
def checkpoint_repo(routed_env, monkeypatch) -> LocalFileCheckpointRepository:
    """独立临时目录检查点仓储（_acquire_checkpoint_repository 注入）。"""
    repo = LocalFileCheckpointRepository()
    monkeypatch.setattr(inst_router, "_acquire_checkpoint_repository", lambda: repo)
    return repo


@pytest.fixture
def queue(tmp_path, monkeypatch) -> LocalFileExecutionQueue:
    """独立临时目录执行队列（续跑作业入队 + origin 查询注入）。"""
    q = LocalFileExecutionQueue(queue_dir=str(tmp_path / "queue"))
    monkeypatch.setattr(inst_router, "_execution_queue_instance", q)
    monkeypatch.setattr(inst_router, "_execution_queue_failed", False)
    return q


async def _append(repo, instance_id, event_type, payload=None):
    await repo.append(
        ExecutionEvent(
            event_id=str(uuid.uuid4()),
            instance_id=instance_id,
            event_type=event_type,
            node_id=None,
            sequence=0,
            payload=payload or {},
            occurred_at=datetime.now(timezone.utc),
        )
    )


def _checkpoint(instance_id, checkpoint_id, completed_node_id=None, round_index=1):
    return Checkpoint(
        checkpoint_id=checkpoint_id,
        instance_id=instance_id,
        completed_node_id=completed_node_id,
        next_node_id=None,
        state_snapshot=SharedState(
            instance_id=instance_id,
            node_outputs={completed_node_id or "n": {"stub": True}},
        ),
        round_index=round_index,
    )


async def _enqueue_origin(
    queue,
    instance_id,
    tenant_id="t-m5",
    definition_version=3,
    trace_id=None,
):
    """构造并入队 origin 作业（worker 模式实例的历史载荷正源）。"""
    job = ExecutionJob(
        job_id=f"job-{instance_id}",
        instance_id=instance_id,
        tenant_id=tenant_id,
        definition_id="m5_recover_flow",
        definition_version=definition_version,
        yaml_text=_YAML,
        initial_inputs={"items": [{"id": 1}]},
        trigger_source="manual",
        trace_id=trace_id or f"trace-{instance_id}",
        idempotency_key=instance_id,
    )
    await queue.enqueue(job)
    return job


def _review_body(decision, actor="alice", comment=""):
    return ReviewBody(decision=decision, actor=actor, comment=comment)


# =============================================================================
# A. /recover worker 读穿透（design D7/D8）
# =============================================================================


class TestRecoverWorker:

    async def test_unknown_instance_404(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        with pytest.raises(HTTPException) as exc_info:
            await inst_router.recover_instance("inst-none-m5")
        assert exc_info.value.status_code == 404

    async def test_running_409(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """running → 409 仍在运行（作业在途，镜像 in_process 拒绝）。"""
        iid = "inst-m5-recover-running"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)

        with pytest.raises(HTTPException) as exc_info:
            await inst_router.recover_instance(iid)
        assert exc_info.value.status_code == 409
        assert "仍在运行" in exc_info.value.detail

    async def test_waiting_review_409(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """waiting_review → 409 请提交审核决策（worker 无法代持挂起态）。"""
        iid = "inst-m5-recover-waiting"
        await _append(event_repo, iid, EventType.INSTANCE_SUSPENDED)

        with pytest.raises(HTTPException) as exc_info:
            await inst_router.recover_instance(iid)
        assert exc_info.value.status_code == 409
        assert "审核决策" in exc_info.value.detail

    async def test_review_timeout_409(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """review_timeout → 409（防从门检查点续跑绕过已留痕决策，安全优先）。"""
        iid = "inst-m5-recover-timeout"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)
        await _append(event_repo, iid, EventType.INSTANCE_SUSPENDED)
        await _append(
            event_repo,
            iid,
            EventType.STATUS_CHANGED,
            {"from_status": "waiting_review", "to_status": "review_timeout"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await inst_router.recover_instance(iid)
        assert exc_info.value.status_code == 409
        assert "review_timeout" in exc_info.value.detail

    async def test_terminal_enqueues_resume_job_with_origin_payload(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """终态恢复：检查点续跑作业入队（确定性幂等键 + origin 载荷补齐）。"""
        iid = "inst-m5-recover-failed"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)
        await _append(event_repo, iid, EventType.INSTANCE_FAILED)
        await checkpoint_repo.append(
            _checkpoint(iid, "cp-r1", completed_node_id="transform_a", round_index=1)
        )
        origin = await _enqueue_origin(
            queue, iid, tenant_id="t-r", trace_id="trace-r"
        )

        result = await inst_router.recover_instance(iid)

        assert result == {
            "instance_id": iid,
            "resumed_from_checkpoint_id": "cp-r1",
            "status": "running",
        }

        resume = await queue.find_latest_by_instance(iid)
        assert resume is not None and resume.job_id != origin.job_id
        assert resume.resume_checkpoint_id == "cp-r1"
        assert resume.idempotency_key == f"{iid}:recover:cp-r1", (
            "确定性幂等键（规避 v1_8 全局唯一索引）"
        )
        assert resume.trigger_source == "recover"
        assert resume.yaml_text == _YAML, "origin yaml 载荷补齐（T7）"
        assert resume.tenant_id == "t-r"
        assert resume.definition_version == 3
        assert resume.trace_id == "trace-r"
        assert resume.initial_inputs == {}
        assert await queue.get(origin.job_id) is not None, "origin 作业不被覆盖"

    async def test_pending_window_without_checkpoint_500(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """pending 窗口恢复：无检查点 → 500 如实口径（从未运行，无位点）。"""
        iid = "inst-m5-recover-pending"
        await _append(event_repo, iid, EventType.INSTANCE_QUEUED)

        with pytest.raises(HTTPException) as exc_info:
            await inst_router.recover_instance(iid)
        assert exc_info.value.status_code == 500
        assert "无可用检查点" in exc_info.value.detail

    async def test_repeat_recover_same_checkpoint_idempotent(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """重复恢复同检查点：同键非终态幂等拦截（唯一续跑作业）。"""
        iid = "inst-m5-recover-again"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)
        await _append(event_repo, iid, EventType.INSTANCE_FAILED)
        await checkpoint_repo.append(
            _checkpoint(iid, "cp-again", completed_node_id="transform_a")
        )
        await _enqueue_origin(queue, iid)

        first = await inst_router.recover_instance(iid)
        second = await inst_router.recover_instance(iid)

        assert first == second
        jobs = [j for j in queue._list_jobs() if j.instance_id == iid]
        assert len(jobs) == 2, "origin + 唯一续跑作业（重复入队被幂等拦截）"

    async def test_queue_unavailable_500(
        self, routed_env, event_repo, checkpoint_repo, queue, monkeypatch
    ):
        """队列不可用 → 500 执行队列不可用（无法续跑实例）。"""
        monkeypatch.setattr(inst_router, "_execution_queue_instance", None)
        monkeypatch.setattr(inst_router, "_execution_queue_failed", True)
        iid = "inst-m5-recover-noq"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)
        await _append(event_repo, iid, EventType.INSTANCE_FAILED)
        await checkpoint_repo.append(_checkpoint(iid, "cp-noq"))

        with pytest.raises(HTTPException) as exc_info:
            await inst_router.recover_instance(iid)
        assert exc_info.value.status_code == 500
        assert "执行队列不可用" in exc_info.value.detail

    async def test_enqueue_resume_job_requires_queue(self, monkeypatch):
        """共用入队助手：队列不可用直接 500（/review approve 同源）。"""
        monkeypatch.setattr(inst_router, "_get_execution_queue", lambda: None)

        with pytest.raises(HTTPException) as exc_info:
            await inst_router._enqueue_worker_resume_job(
                "inst-x",
                "cp-x",
                definition_id="wf-x",
                trigger_source="recover",
                idempotency_scope="recover",
            )
        assert exc_info.value.status_code == 500
        assert "执行队列不可用" in exc_info.value.detail


# =============================================================================
# B. /review worker 读穿透（design D7/D8）
# =============================================================================


class TestReviewWorker:

    async def test_unknown_instance_404(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        with pytest.raises(HTTPException) as exc_info:
            await submit_review("inst-none-m5", _review_body("approve"))
        assert exc_info.value.status_code == 404

    async def test_non_waiting_review_409(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """非 waiting_review → 409（INSTANCE_NOT_WAITING_REVIEW 同文案）。"""
        iid = "inst-m5-review-running"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)

        with pytest.raises(HTTPException) as exc_info:
            await submit_review(iid, _review_body("approve"))
        assert exc_info.value.status_code == 409
        assert "仅等待审核" in exc_info.value.detail

    async def test_waiting_without_checkpoint_404(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """waiting_review 但无挂起检查点 → 404（STATE_CHECKPOINT_NOT_FOUND）。"""
        iid = "inst-m5-review-no-cp"
        await _append(event_repo, iid, EventType.INSTANCE_SUSPENDED)

        with pytest.raises(HTTPException) as exc_info:
            await submit_review(iid, _review_body("approve"))
        assert exc_info.value.status_code == 404
        assert "无可用挂起检查点" in exc_info.value.detail

    async def test_compiled_unavailable_409(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """编译重建失败（无 origin、仓储无定义）→ 409 缺少可决策的执行上下文。"""
        iid = "inst-m5-review-ctx"
        await _append(
            event_repo,
            iid,
            EventType.INSTANCE_STARTED,
            {"definition_id": "wf-bogus-m5"},
        )
        await _append(event_repo, iid, EventType.INSTANCE_SUSPENDED)
        await checkpoint_repo.append(
            _checkpoint(iid, "cp-ctx", completed_node_id="review_gate")
        )

        with pytest.raises(HTTPException) as exc_info:
            await submit_review(iid, _review_body("approve"))
        assert exc_info.value.status_code == 409
        assert "缺少可决策的执行上下文" in exc_info.value.detail

    async def test_approve_records_decision_and_enqueues_resume(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """approve：STATUS_CHANGED(running) 留痕 + 续跑作业入队（:review-resume:）。"""
        iid = "inst-m5-review-approve"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)
        await _append(event_repo, iid, EventType.INSTANCE_SUSPENDED)
        await checkpoint_repo.append(
            _checkpoint(iid, "cp-rev1", completed_node_id="review_gate", round_index=2)
        )
        await _enqueue_origin(queue, iid, tenant_id="t-rev", trace_id="trace-rev")

        result = await submit_review(iid, _review_body("approve", comment="同意"))

        assert result["decision"] == "approve"
        assert result["status"] == "running"
        assert result["checkpoint_id"] == "cp-rev1"
        assert result["resumed_from_checkpoint_id"] == "cp-rev1"

        events = await event_repo.list_for_instance(iid)
        status_events = [
            e for e in events if e.event_type == EventType.STATUS_CHANGED
        ]
        assert len(status_events) == 1
        assert status_events[0].payload["to_status"] == "running"
        assert status_events[0].payload["decision"] == "approve"

        resume = await queue.find_latest_by_instance(iid)
        assert resume is not None
        assert resume.idempotency_key == f"{iid}:review-resume:cp-rev1"
        assert resume.trigger_source == "review_resume"
        assert resume.resume_checkpoint_id == "cp-rev1"
        assert resume.yaml_text == _YAML

        snapshot = await read_instance_snapshot(iid)
        assert snapshot["status"] == "running", "决策后快照立即可见 running"

    async def test_reject_maps_cancelled_without_resume_job(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """reject：review_timeout_action 缺省 reject → cancelled 终态，不入队。"""
        iid = "inst-m5-review-reject"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)
        await _append(event_repo, iid, EventType.INSTANCE_SUSPENDED)
        await checkpoint_repo.append(
            _checkpoint(iid, "cp-rej", completed_node_id="review_gate", round_index=2)
        )
        await _enqueue_origin(queue, iid)

        result = await submit_review(iid, _review_body("reject"))

        assert result["status"] == "cancelled"

        snapshot = await read_instance_snapshot(iid)
        assert snapshot["status"] == "cancelled"
        assert snapshot.get("finished_at"), "终态留痕时刻"

        jobs = [j for j in queue._list_jobs() if j.instance_id == iid]
        assert len(jobs) == 1, "reject 不入队续跑作业（仅 origin）"

    async def test_timeout_action_maps_review_timeout(
        self, routed_env, event_repo, checkpoint_repo, queue
    ):
        """timeout_action → REVIEW_TIMEOUT（非终态，无 finished_at），不入队。"""
        iid = "inst-m5-review-timeout"
        await _append(event_repo, iid, EventType.INSTANCE_STARTED)
        await _append(event_repo, iid, EventType.INSTANCE_SUSPENDED)
        await checkpoint_repo.append(
            _checkpoint(iid, "cp-to", completed_node_id="review_gate", round_index=2)
        )
        await _enqueue_origin(queue, iid)

        result = await submit_review(iid, _review_body("timeout_action"))

        assert result["status"] == "review_timeout"

        snapshot = await read_instance_snapshot(iid)
        assert snapshot["status"] == "review_timeout"
        assert not snapshot.get("finished_at"), "review_timeout 非终态"

        jobs = [j for j in queue._list_jobs() if j.instance_id == iid]
        assert len(jobs) == 1, "timeout_action 不入队续跑作业"
