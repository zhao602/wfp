# =============================================================================
# M1 执行队列与统一提交器单元测试（tests/unit/test_execution_queue.py）
#
# roadmap-v3 中期窗口 M1（F1 执行队列与提交路径统一，plan D2）：
# 1. LocalFileExecutionQueue 全语义（入队/认领/租约/心跳/过期接管/
#    幂等/终态流转/崩溃残留防御）——测试环境（无活 PG）以本实现证明
#    全部队列语义（plan D2）；
# 2. ExecutionSubmitter in_process 等价路径（create_task + rt.task 同步落值）
#    与 worker 入队分支（幂等键 = instance_id，返回 None）；
# 3. M1 兼容关键缝：路由转发与 runner 同源（身份断言）、publisher hook
#    调用期回读路由全局。
# =============================================================================

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


# =============================================================================
# 测试工具
# =============================================================================

def _make_job(job_id="job-1", instance_id="inst-1", idempotency_key=None,
              trigger_source="manual", tenant_id=None):
    """构造待入队作业（状态 pending）。"""
    from orchestration_platform.domain.models.execution_job import ExecutionJob

    return ExecutionJob(
        job_id=job_id,
        instance_id=instance_id,
        tenant_id=tenant_id,
        definition_id="wf-demo",
        initial_inputs={"topic": "队列语义"},
        trigger_source=trigger_source,
        trace_id="trace-1",
        idempotency_key=idempotency_key if idempotency_key is not None else instance_id,
    )


def _make_queue(tmp_path, name="q"):
    """以独立临时目录构造本地文件队列。"""
    from orchestration_platform.persistence.local_file.local_file_execution_queue import (
        LocalFileExecutionQueue,
    )

    return LocalFileExecutionQueue(queue_dir=str(tmp_path / name))


def _age_lease(queue, job_id, seconds_past=300):
    """将作业租约到期时刻改写为过去（模拟租约过期）。"""
    file_path = queue._dir / f"{job_id}.json"
    data = json.loads(file_path.read_text(encoding="utf-8"))
    data["lease_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=seconds_past)
    ).isoformat()
    file_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# =============================================================================
# LocalFileExecutionQueue：入队与查询
# =============================================================================

class TestLocalFileQueueEnqueue:

    async def test_enqueue_then_get_roundtrip(self, tmp_path):
        queue = _make_queue(tmp_path)
        job = _make_job()

        returned = await queue.enqueue(job)

        assert returned.job_id == job.job_id
        loaded = await queue.get(job.job_id)
        assert loaded is not None
        assert loaded.instance_id == "inst-1"
        assert loaded.status == "pending"
        assert loaded.attempts == 0
        assert loaded.idempotency_key == "inst-1"

    async def test_get_missing_returns_none(self, tmp_path):
        queue = _make_queue(tmp_path)
        assert await queue.get("no-such-job") is None

    async def test_enqueue_idempotent_same_key_nonterminal(self, tmp_path):
        """同幂等键非终态重复提交返回既有作业（提交幂等）。"""
        queue = _make_queue(tmp_path)
        first = _make_job(job_id="job-a", idempotency_key="key-1")
        second = _make_job(job_id="job-b", idempotency_key="key-1")

        await queue.enqueue(first)
        returned = await queue.enqueue(second)

        assert returned.job_id == "job-a"
        assert await queue.get("job-b") is None
        assert len(list(queue._dir.glob("*.json"))) == 1

    async def test_enqueue_allows_new_job_after_terminal(self, tmp_path):
        """既有作业已终态后，同幂等键新提交正常入队（重试语义）。"""
        queue = _make_queue(tmp_path)
        first = _make_job(job_id="job-a", idempotency_key="key-1")
        await queue.enqueue(first)
        first.status = "completed"
        queue._save_job(first)

        second = _make_job(job_id="job-b", idempotency_key="key-1")
        returned = await queue.enqueue(second)

        assert returned.job_id == "job-b"
        assert await queue.get("job-b") is not None


# =============================================================================
# LocalFileExecutionQueue：认领与租约
# =============================================================================

class TestLocalFileQueueClaim:

    async def test_claim_transitions_pending_to_running(self, tmp_path):
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())

        claimed = await queue.claim("worker-1", lease_ttl_seconds=60)

        assert claimed is not None
        assert claimed.status == "running"
        assert claimed.lease_owner == "worker-1"
        assert claimed.lease_expires_at is not None
        assert claimed.attempts == 1

    async def test_second_replica_claim_gets_none(self, tmp_path):
        """两副本竞争：先认领者赢，后认领者无作业（不重复执行）。"""
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())

        first = await queue.claim("worker-1", lease_ttl_seconds=60)
        second = await queue.claim("worker-2", lease_ttl_seconds=60)

        assert first is not None
        assert second is None

    async def test_claim_none_when_queue_empty(self, tmp_path):
        queue = _make_queue(tmp_path)
        assert await queue.claim("worker-1") is None

    async def test_lease_expiry_allows_takeover(self, tmp_path):
        """租约过期的 running 作业可被接管（at-least-once，attempts 累加）。"""
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())
        claimed = await queue.claim("worker-1", lease_ttl_seconds=60)
        assert claimed is not None

        # 未过期：接管返回 None
        assert await queue.take_over_expired("worker-2", lease_ttl_seconds=60) is None

        _age_lease(queue, claimed.job_id, seconds_past=300)
        taken = await queue.take_over_expired("worker-2", lease_ttl_seconds=60)

        assert taken is not None
        assert taken.lease_owner == "worker-2"
        assert taken.attempts == 2
        assert taken.lease_expires_at is not None

    async def test_heartbeat_refreshes_only_owner(self, tmp_path):
        """心跳续租：仅租约持有者生效，非持有者续租失败。"""
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())
        claimed = await queue.claim("worker-1")

        assert await queue.heartbeat(claimed.job_id, "worker-2") is False
        assert await queue.heartbeat(claimed.job_id, "worker-1") is True

        refreshed = await queue.get(claimed.job_id)
        assert refreshed.lease_owner == "worker-1"
        # 同默认租约时长续期：到期时刻不早于认领时刻（微秒级时间戳单调）
        assert refreshed.lease_expires_at >= claimed.lease_expires_at

    async def test_takeover_revokes_original_owner_heartbeat(self, tmp_path):
        """接管后原持有者心跳失效（lease_owner 已流转）。"""
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())
        claimed = await queue.claim("worker-1", lease_ttl_seconds=60)
        _age_lease(queue, claimed.job_id, seconds_past=300)

        taken = await queue.take_over_expired("worker-2", lease_ttl_seconds=60)
        assert taken is not None
        assert await queue.heartbeat(claimed.job_id, "worker-1") is False

    async def test_stale_claim_residue_does_not_block(self, tmp_path):
        """崩溃残留认领标记超过租约时长后被清理，作业仍可认领。"""
        queue = _make_queue(tmp_path)
        job = _make_job()
        await queue.enqueue(job)

        # 模拟认领中途崩溃残留：写入 claim 标记并回拨 mtime
        claim_path = queue._dir / f"{job.job_id}.claim"
        claim_path.write_text("crashed-worker", encoding="utf-8")
        old = time.time() - 3600
        os.utime(claim_path, (old, old))

        claimed = await queue.claim("worker-1", lease_ttl_seconds=60)
        assert claimed is not None
        assert claimed.lease_owner == "worker-1"

    async def test_fresh_foreign_claim_skips_job(self, tmp_path):
        """他人持有新鲜认领标记（并行认领窗口）时跳过该作业。"""
        queue = _make_queue(tmp_path)
        job = _make_job()
        await queue.enqueue(job)

        claim_path = queue._dir / f"{job.job_id}.claim"
        claim_path.write_text("other-worker", encoding="utf-8")

        assert await queue.claim("worker-1", lease_ttl_seconds=60) is None


# =============================================================================
# LocalFileExecutionQueue：终态流转
# =============================================================================

class TestLocalFileQueueTerminal:

    async def test_complete_clears_lease(self, tmp_path):
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())
        claimed = await queue.claim("worker-1", lease_ttl_seconds=60)

        assert await queue.complete(claimed.job_id, "worker-1") is True

        finished = await queue.get(claimed.job_id)
        assert finished.status == "completed"
        assert finished.lease_owner is None
        assert finished.lease_expires_at is None

    async def test_complete_rejects_non_owner(self, tmp_path):
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())
        claimed = await queue.claim("worker-1", lease_ttl_seconds=60)

        assert await queue.complete(claimed.job_id, "worker-2") is False
        still = await queue.get(claimed.job_id)
        assert still.status == "running"

    async def test_fail_records_last_error(self, tmp_path):
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())
        claimed = await queue.claim("worker-1", lease_ttl_seconds=60)

        assert await queue.fail(claimed.job_id, "worker-1", "boom") is True

        failed = await queue.get(claimed.job_id)
        assert failed.status == "failed"
        assert failed.last_error == "boom"
        assert failed.lease_owner is None

    async def test_terminal_job_not_reclaimed_or_taken_over(self, tmp_path):
        """终态作业不再认领/接管（不重投）。"""
        queue = _make_queue(tmp_path)
        await queue.enqueue(_make_job())
        claimed = await queue.claim("worker-1", lease_ttl_seconds=60)
        await queue.complete(claimed.job_id, "worker-1")

        assert await queue.claim("worker-2") is None
        assert await queue.take_over_expired("worker-2") is None


# =============================================================================
# ExecutionSubmitter：in_process 等价路径与 worker 入队分支
# =============================================================================

class TestExecutionSubmitter:

    def _runtime_and_registry(self):
        from orchestration_platform.application.execution_runner import (
            InstanceRegistry,
            InstanceRuntime,
        )

        rt = InstanceRuntime(instance_id="inst-sub", definition_id="wf-sub")
        registry = InstanceRegistry()
        registry.register(rt)
        return rt, registry

    async def test_in_process_submit_sets_rt_task_and_runs(
        self, monkeypatch
    ):
        """in_process（默认）= create_task 等价路径：rt.task 同步落值，
        run_and_record 收到全量参数。"""
        import orchestration_platform.application.execution_runner as runner_mod

        recorded = {}

        async def fake_run_and_record(
            scheduler, compiled, initial_state, instance_id, trace_id,
            registry, **kwargs,
        ):
            recorded["args"] = (instance_id, trace_id)
            recorded["kwargs"] = kwargs
            await asyncio.sleep(0)
            return None

        monkeypatch.setattr(runner_mod, "run_and_record", fake_run_and_record)
        rt, registry = self._runtime_and_registry()
        submitter = runner_mod.ExecutionSubmitter()

        task = await submitter.submit(
            SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
            "inst-sub", "trace-sub", registry,
            run_log_recorder=None, trigger_source="cron", rt=rt,
        )

        assert task is not None
        assert rt.task is task
        await task
        assert recorded["args"] == ("inst-sub", "trace-sub")
        assert recorded["kwargs"]["trigger_source"] == "cron"

    def test_submit_sync_in_process_sets_rt_task_immediately(
        self, monkeypatch
    ):
        """同步入口（review approve 唤醒链场景）：rt.task 同步落值，
        无需事件循环让拍即可断言（与历史逐拍等价）。"""
        import orchestration_platform.application.execution_runner as runner_mod

        async def fake_run_and_record(*args, **kwargs):
            await asyncio.sleep(0)
            return None

        monkeypatch.setattr(runner_mod, "run_and_record", fake_run_and_record)
        rt, registry = self._runtime_and_registry()
        submitter = runner_mod.ExecutionSubmitter()

        async def _drive():
            task = submitter.submit_sync(
                SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
                "inst-sub", "trace-sub", registry,
                resume_checkpoint=SimpleNamespace(checkpoint_id="cp-1"),
                trigger_source="review_resume", rt=rt,
            )
            # 同步落值：未让拍即可断言
            assert task is not None
            assert rt.task is task
            await task

        asyncio.run(_drive())

    async def test_worker_mode_enqueues_job_and_returns_none(
        self, monkeypatch
    ):
        """worker 模式：作业入队（幂等键 = instance_id）返回 None，
        rt.task 不落值。"""
        import orchestration_platform.application.execution_runner as runner_mod

        monkeypatch.setattr(
            runner_mod,
            "get_settings",
            lambda: SimpleNamespace(execution_mode="worker"),
        )

        enqueued = []

        class _FakeQueue:
            async def enqueue(self, job):
                enqueued.append(job)
                return job

        rt, registry = self._runtime_and_registry()
        rt.tenant_id = "tenant-9"
        submitter = runner_mod.ExecutionSubmitter(queue=_FakeQueue())

        state = SimpleNamespace(node_outputs={"topic": "初始输入"})

        async def _drive():
            return await submitter.submit(
                SimpleNamespace(), SimpleNamespace(), state,
                "inst-sub", "trace-sub", registry,
                resume_checkpoint=SimpleNamespace(checkpoint_id="cp-resume"),
                trigger_source="re_execute", rt=rt,
            )

        returned = await _drive()

        assert returned is None
        assert rt.task is None
        assert len(enqueued) == 1
        job = enqueued[0]
        assert job.instance_id == "inst-sub"
        assert job.idempotency_key == "inst-sub"
        assert job.tenant_id == "tenant-9"
        assert job.trigger_source == "re_execute"
        assert job.resume_checkpoint_id == "cp-resume"
        assert job.initial_inputs == {"topic": "初始输入"}


# =============================================================================
# M1 兼容关键缝：路由转发同源 + publisher hook 回读
# =============================================================================

class TestM1CompatSeams:

    def test_router_reexports_share_runner_identities(self):
        """路由 from-import 转发与 runner 同源（身份断言，monkeypatch 语义）。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod
        import orchestration_platform.application.execution_runner as runner_mod

        assert router_mod.InstanceRuntime is runner_mod.InstanceRuntime
        assert router_mod.InstanceRegistry is runner_mod.InstanceRegistry
        assert router_mod._instance_registry is runner_mod._instance_registry
        assert router_mod._executor_registry is runner_mod._executor_registry
        assert router_mod.run_and_record is runner_mod.run_and_record
        assert router_mod.NodeEventCallbackImpl is runner_mod.NodeEventCallbackImpl
        assert router_mod._execution_submitter is runner_mod._execution_submitter

    def test_publisher_hook_reads_router_global(self):
        """runner 发布钩子调用期回读路由模块全局（set 后立即可见）。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod
        import orchestration_platform.application.execution_runner as runner_mod

        marker = SimpleNamespace(name="publisher-under-test")
        original = router_mod._domain_event_publisher
        router_mod.set_domain_event_publisher(marker)
        try:
            assert runner_mod._current_domain_event_publisher() is marker
        finally:
            router_mod.set_domain_event_publisher(original)
        assert runner_mod._current_domain_event_publisher() is original
