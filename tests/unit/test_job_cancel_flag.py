# =============================================================================
# M3 作业取消标志与实例作业定位单元测试（tests/unit/test_job_cancel_flag.py）
#
# roadmap-v3 中期窗口 M3（P5 实例级取消与超时，design D1/D5）：
# 1. request_cancel 幂等置位（任意状态可置、缺省 False、重复置位无害）；
# 2. find_active_by_instance 仅返回活跃（pending/running）作业、最早优先；
# 3. local_file 旧 JSON 缺字段容错（cancel_requested 缺省读 False）；
# 4. PostgresExecutionQueue 新方法 SQL 形态静态断言（无活 PG 环境，
#    以源码检查锁形——test_status_all_zero 先例）。
# =============================================================================

import json
from pathlib import Path


def _make_job(job_id="job-1", instance_id="inst-1"):
    """构造待入队作业（状态 pending，幂等键缺省 instance_id）。"""
    from orchestration_platform.domain.models.execution_job import ExecutionJob

    return ExecutionJob(
        job_id=job_id,
        instance_id=instance_id,
        definition_id="wf-demo",
        initial_inputs={"topic": "取消语义"},
        trace_id="trace-1",
        idempotency_key=instance_id,
    )


def _make_queue(tmp_path, name="q"):
    """以独立临时目录构造本地文件队列。"""
    from orchestration_platform.persistence.local_file.local_file_execution_queue import (
        LocalFileExecutionQueue,
    )

    return LocalFileExecutionQueue(queue_dir=str(tmp_path / name))


class TestRequestCancelLocalFile:

    async def test_unknown_job_returns_false(self, tmp_path):
        queue = _make_queue(tmp_path)
        assert await queue.request_cancel("missing") is False

    async def test_set_flag_idempotent(self, tmp_path):
        queue = _make_queue(tmp_path)
        job = _make_job()
        await queue.enqueue(job)

        assert await queue.request_cancel(job.job_id) is True
        loaded = await queue.get(job.job_id)
        assert loaded.cancel_requested is True

        # 幂等重复置位：仍 True、仍为 True
        assert await queue.request_cancel(job.job_id) is True
        loaded = await queue.get(job.job_id)
        assert loaded.cancel_requested is True

    async def test_flag_survives_roundtrip_on_disk(self, tmp_path):
        queue = _make_queue(tmp_path)
        job = _make_job()
        await queue.enqueue(job)
        await queue.request_cancel(job.job_id)

        # 磁盘 JSON 落字段
        raw = json.loads(
            (queue._dir / f"{job.job_id}.json").read_text(encoding="utf-8")
        )
        assert raw["cancel_requested"] is True

    async def test_default_false_without_flag(self, tmp_path):
        queue = _make_queue(tmp_path)
        job = _make_job()
        await queue.enqueue(job)

        loaded = await queue.get(job.job_id)
        assert loaded.cancel_requested is False

    async def test_legacy_json_without_field_loads_default_false(self, tmp_path):
        """旧 JSON（M2 前态）无 cancel_requested 字段 → 容错读默认 False。"""
        queue = _make_queue(tmp_path)
        job = _make_job()
        await queue.enqueue(job)

        file_path = queue._dir / f"{job.job_id}.json"
        data = json.loads(file_path.read_text(encoding="utf-8"))
        data.pop("cancel_requested", None)
        file_path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

        loaded = await queue.get(job.job_id)
        assert loaded is not None
        assert loaded.cancel_requested is False


class TestFindActiveByInstanceLocalFile:

    async def test_pending_job_found(self, tmp_path):
        queue = _make_queue(tmp_path)
        job = _make_job(instance_id="inst-a")
        await queue.enqueue(job)

        found = await queue.find_active_by_instance("inst-a")
        assert found is not None
        assert found.job_id == job.job_id

    async def test_running_job_found(self, tmp_path):
        queue = _make_queue(tmp_path)
        job = _make_job(instance_id="inst-a")
        await queue.enqueue(job)
        await queue.claim("worker-1")

        found = await queue.find_active_by_instance("inst-a")
        assert found is not None
        assert found.status == "running"

    async def test_terminal_job_not_returned(self, tmp_path):
        queue = _make_queue(tmp_path)
        job = _make_job(instance_id="inst-a")
        await queue.enqueue(job)
        await queue.claim("worker-1")
        await queue.complete(job.job_id, "worker-1")

        assert await queue.find_active_by_instance("inst-a") is None

    async def test_unknown_instance_returns_none(self, tmp_path):
        queue = _make_queue(tmp_path)
        assert await queue.find_active_by_instance("nobody") is None

    async def test_earliest_active_wins(self, tmp_path):
        queue = _make_queue(tmp_path)
        early = _make_job(job_id="job-early", instance_id="inst-a")
        late = _make_job(job_id="job-late", instance_id="inst-a")
        # created_at 同秒级精度：early 先入队（列表序即入队序）
        await queue.enqueue(early)
        await queue.enqueue(late)

        found = await queue.find_active_by_instance("inst-a")
        assert found is not None
        assert found.job_id == "job-early"


class TestPostgresQueueSqlShape:
    """PostgresExecutionQueue 新方法 SQL 形态静态断言（无活 PG 环境）。"""

    _FILE = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "orchestration_platform"
        / "persistence"
        / "postgres"
        / "postgres_execution_queue.py"
    )

    def test_row_to_job_maps_cancel_requested(self):
        source = self._FILE.read_text(encoding="utf-8")
        assert "cancel_requested=bool(row[\"cancel_requested\"])" in source

    def test_request_cancel_sql_shape(self):
        source = self._FILE.read_text(encoding="utf-8")
        assert "SET cancel_requested = TRUE" in source
        assert "WHERE job_id = $1" in source

    def test_find_active_filters_status_and_orders(self):
        source = self._FILE.read_text(encoding="utf-8")
        assert "status IN ('pending', 'running')" in source
        assert "ORDER BY created_at, job_id" in source
        assert "WHERE instance_id = $1" in source
