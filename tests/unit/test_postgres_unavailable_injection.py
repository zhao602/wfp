# =============================================================================
# PG 不可用故障注入套件（tests/unit/test_postgres_unavailable_injection.py）
#
# 职责（roadmap B14 T4「PG 断连」，design D4-①②③）：以 FakeAsyncPool/
# FakeAsyncConn 假件范式（test_tenant_scoped_postgres_base.py 先例）注入
# 连接失败，固定三层 fail-loud 契约（零真实 PG 依赖）：
#   A. 连接池创建失败：异常冒泡（文档化失败=HTTP 500，非静默降级）+
#      惰性单例未毒化（失败不缓存，下次调用重试；单次调用零内部重试）；
#   B. 事务中连接失败：干净中止无部分提交（事务异常路径退出=ROLLBACK、
#      失败后零语句执行、连接归还）+ API 边界映射 500（框架全局兜底——
#      application_factory 仅注册域异常处理器，未捕获异常经
#      ServerErrorMiddleware → 500）；
#   C. 执行队列租约语义：claim 单语句原子形态（FOR UPDATE SKIP LOCKED
#      子查询单赢，无多语句认领协议可半提交）+ heartbeat/complete/fail
#      lease 门控 SQL 断言 + 连接失败冒泡（worker 重试循环语义）+
#      恢复后接管可用（同一认领协议、租约字段流转正确）。
# 诚实边界：假件断言 SQL 协议形态与异常传播路径；真实 PG 原子性由
# 数据库单语句语义保证（M3 kill-9 drill 交叉引用崩溃恢复行为）。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

import pytest

import asyncpg  # noqa: F401  # monkeypatch 目标（_get_pool 函数内延迟导入）

from orchestration_platform.persistence.postgres.postgres_execution_queue import (
    PostgresExecutionQueue,
)
from orchestration_platform.persistence.postgres.tenant_scoped_base import (
    TenantScopedPostgresRepository,
)


# =============================================================================
# 测试替身：记录型/断连型假件（asyncpg 语义，零真实库）
# =============================================================================


class RecordingTransaction:
    """asyncpg 事务语义 mock：记录进入/退出与退出异常（异常退出=ROLLBACK）。"""

    def __init__(self):
        self.entered = 0
        self.exited = 0
        self.exit_exc: Optional[BaseException] = None

    async def __aenter__(self):
        self.entered += 1

    async def __aexit__(self, exc_type, exc, tb):
        self.exited += 1
        self.exit_exc = exc
        return False


class FlakyAsyncConn:
    """断连注入连接：第 fail_at 次语句起抛 ConnectionError。

    记录失败前全部语句序列（operations）与失败后调用数
    （post_failure_calls，断言干净中止后零执行）。
    """

    def __init__(self, fail_at: int, fetchrow_results: Optional[List[Any]] = None):
        self.fail_at = fail_at
        self._calls = 0
        self.failed = False
        self.post_failure_calls = 0
        self.operations: List[Tuple[str, str]] = []
        self.transactions: List[RecordingTransaction] = []
        self._fetchrow_results = list(fetchrow_results or [])

    def transaction(self):
        t = RecordingTransaction()
        self.transactions.append(t)
        return t

    async def _gate(self) -> None:
        if self.failed:
            self.post_failure_calls += 1
            raise ConnectionError("连接已中断（模拟 PG 断连）")
        self._calls += 1
        if self._calls >= self.fail_at:
            self.failed = True
            raise ConnectionError("连接中断（模拟 PG 断连）")

    async def execute(self, sql, *args):
        await self._gate()
        self.operations.append(("execute", sql))
        return "OK"

    async def fetchrow(self, sql, *args):
        await self._gate()
        self.operations.append(("fetchrow", sql))
        if self._fetchrow_results:
            return self._fetchrow_results.pop(0)
        return None


class RecordingAcquire:
    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        self._pool.release_count += 1
        return False


class RecordingAsyncPool:
    """acquire 语义 mock：记录归还次数（断言连接必归还）。"""

    def __init__(self, conn):
        self._conn = conn
        self.release_count = 0

    def acquire(self):
        return RecordingAcquire(self._conn, self)


def _make_queue_row(
    job_id: str = "job-1",
    status: str = "running",
    lease_owner: Optional[str] = "worker-1",
    attempts: int = 1,
) -> dict:
    """构造 execution_job 行（_row_to_job 全字段口径）。"""
    return {
        "job_id": job_id,
        "instance_id": "inst-1",
        "tenant_id": "tenant_default",
        "definition_id": "wf-demo",
        "definition_version": 3,
        "yaml_text": "spec_version: '1.0'",
        "initial_inputs": '{"topic": "队列语义"}',
        "trigger_source": "manual",
        "trace_id": "trace-1",
        "resume_checkpoint_id": None,
        "idempotency_key": "inst-1",
        "status": status,
        "lease_owner": lease_owner,
        "lease_expires_at": None,
        "attempts": attempts,
        "last_error": None,
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc),
    }


# =============================================================================
# A. 连接池创建失败：fail-loud + 惰性单例未毒化
# =============================================================================


class TestPoolCreationFailure:
    async def test_create_pool_failure_propagates_and_singleton_not_poisoned(
        self, monkeypatch
    ):
        """① create_pool 抛 ConnectionError → 异常冒泡（文档化失败=500，
        非静默降级）；self._pool 保持 None（未毒化），下次调用重试；
        单次调用零内部重试（每次恰好一次 create_pool 尝试）。"""
        attempts: List[dict] = []

        async def failing_create_pool(**kwargs):
            attempts.append(kwargs)
            raise ConnectionError("PG 不可达（连接拒绝）")

        monkeypatch.setattr(asyncpg, "create_pool", failing_create_pool)

        repo = TenantScopedPostgresRepository()
        with pytest.raises(ConnectionError):
            await repo._get_pool()
        assert repo._pool is None, "失败不得缓存毒化单例"

        with pytest.raises(ConnectionError):
            await repo._get_pool()
        assert len(attempts) == 2, "每次调用恰一次尝试：零内部重试，但可重进"

    async def test_create_pool_recovers_after_failure(self, monkeypatch):
        """① 恢复语义：首次失败 → 再次调用创建成功并缓存；后续命中缓存。"""
        calls: List[int] = []
        sentinel_pool = object()

        async def flaky_create_pool(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("PG 不可达（连接拒绝）")
            return sentinel_pool

        monkeypatch.setattr(asyncpg, "create_pool", flaky_create_pool)

        repo = TenantScopedPostgresRepository()
        with pytest.raises(ConnectionError):
            await repo._get_pool()
        assert await repo._get_pool() is sentinel_pool
        assert await repo._get_pool() is sentinel_pool, "成功后缓存生效"
        assert len(calls) == 2, "缓存命中不再创建"


# =============================================================================
# B. 事务中连接失败：干净中止无部分提交 + API 边界 500
# =============================================================================


class TestMidTransactionFailure:
    async def test_mid_transaction_failure_aborts_cleanly_without_partial_commit(
        self,
    ):
        """② 事务中途断连：已执行语句不提交（事务异常路径退出=ROLLBACK）、
        失败后零语句执行、连接归还、异常冒泡。"""
        conn = FlakyAsyncConn(fail_at=3)  # set_config(1) + INSERT(2) 成功；UPDATE(3) 断连
        pool = RecordingAsyncPool(conn)
        repo = TenantScopedPostgresRepository(pool=pool)

        with pytest.raises(ConnectionError):
            async with repo._tenant_connection("tenant-a"):
                await conn.execute("INSERT INTO execution_event (...) VALUES (...)")
                await conn.execute("UPDATE execution_instance SET status = 'running'")

        transaction = conn.transactions[0]
        assert transaction.entered == 1 and transaction.exited == 1
        assert isinstance(transaction.exit_exc, ConnectionError), (
            "事务以异常路径退出（asyncpg 语义 = ROLLBACK，无部分提交）"
        )
        assert conn.post_failure_calls == 0, "断连后零语句执行"
        assert len(conn.operations) == 2, "失败前仅 set_config + 首条业务语句"
        assert pool.release_count == 1, "连接必须归还连接池"

    def test_connection_failure_maps_to_500_at_api_boundary(self, monkeypatch):
        """② API 边界：路由层不吞连接错误，异常冒泡至框架全局兜底 → 500
        （application_factory 仅注册域异常处理器 :276-299，ConnectionError
        未捕获 → ServerErrorMiddleware → 500；fail-loud，非静默降级）。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        async def failing_create_pool(**kwargs):
            raise ConnectionError("PG 不可达（连接拒绝）")

        monkeypatch.setattr(asyncpg, "create_pool", failing_create_pool)

        app = FastAPI()

        @app.get("/boom")
        async def boom():
            repo = TenantScopedPostgresRepository()
            async with repo._tenant_connection("tenant-a"):
                pass
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/boom")
        assert response.status_code == 500, "连接失败在 HTTP 边界映射 500"


# =============================================================================
# C. 执行队列租约语义：单语句认领原子形态 + lease 门控 + 断连恢复
# =============================================================================


class TestQueueLeaseSemanticsUnderFailure:
    async def test_claim_is_single_statement_skip_locked(self):
        """③ claim 单语句原子形态：单条 UPDATE + FOR UPDATE SKIP LOCKED
        子查询认领（数据库原子单赢；无多语句认领协议可半提交），
        事务内仅 set_config + 认领语句两条。"""
        row = _make_queue_row(status="running", lease_owner="worker-A", attempts=1)
        conn = FlakyAsyncConn(fail_at=10**9, fetchrow_results=[row])
        queue = PostgresExecutionQueue(pool=RecordingAsyncPool(conn))

        job = await queue.claim("worker-A", lease_ttl_seconds=60)

        assert job is not None and job.status == "running"
        assert job.lease_owner == "worker-A"
        assert job.attempts == 1
        claim_calls = [op for op in conn.operations if op[0] == "fetchrow"]
        assert len(claim_calls) == 1, "认领为单语句（单赢原子性）"
        sql = claim_calls[0][1]
        assert "FOR UPDATE SKIP LOCKED" in sql
        assert "attempts = attempts + 1" in sql
        assert "status = 'running'" in sql, "认领即置 running（租约起点）"

    async def test_heartbeat_complete_fail_carry_lease_gating_sql(self):
        """③ lease 门控 SQL：heartbeat/complete/fail 三操作的 WHERE 均含
        job_id + lease_owner + status='running' 三重门控（非持有者/非
        running 一律不生效）；complete/fail 另清空租约字段。"""
        conn = FlakyAsyncConn(
            fail_at=10**9,
            fetchrow_results=[
                {"job_id": "job-1"},  # heartbeat → True
                None,  # 非持有者 heartbeat → False
                {"job_id": "job-1"},  # complete → True
                {"job_id": "job-1"},  # fail → True
            ],
        )
        queue = PostgresExecutionQueue(pool=RecordingAsyncPool(conn))

        assert await queue.heartbeat("job-1", "worker-A", lease_ttl_seconds=60) is True
        assert await queue.heartbeat("job-1", "worker-B", lease_ttl_seconds=60) is False
        assert await queue.complete("job-1", "worker-A") is True
        assert await queue.fail("job-1", "worker-A", "boom") is True

        sqls = [op[1] for op in conn.operations if op[0] == "fetchrow"]
        assert len(sqls) == 4
        for sql in sqls:
            assert "lease_owner = $2" in sql, "租约持有者门控"
            assert "status = 'running'" in sql, "仅 running 态可续租/流转"
        assert "lease_owner = NULL" in sqls[2], "complete 清空租约"
        assert "lease_owner = NULL" in sqls[3], "fail 清空租约"
        assert "last_error = $3" in sqls[3], "fail 记录失败原因"

    async def test_claim_connection_failure_propagates_without_partial_state(self):
        """③ 认领语句断连：异常冒泡（worker 重试循环处置），事务异常路径
        退出（含 set_config 整体回滚），无内部重试（认领语句恰一次尝试）。"""
        conn = FlakyAsyncConn(fail_at=2)  # set_config(1) 成功；认领语句(2) 断连
        pool = RecordingAsyncPool(conn)
        queue = PostgresExecutionQueue(pool=pool)

        with pytest.raises(ConnectionError):
            await queue.claim("worker-A", lease_ttl_seconds=60)

        transaction = conn.transactions[0]
        assert isinstance(transaction.exit_exc, ConnectionError), "事务整体回滚"
        assert conn.post_failure_calls == 0, "断连后零语句执行（无内部重试）"
        assert pool.release_count == 1, "连接归还"

    async def test_takeover_available_after_connection_recovery(self):
        """③ 恢复后接管可用：首轮认领断连（worker-A 死亡）→ 次轮同一认领
        协议成功（worker-B 单赢），租约字段流转正确（owner/attempts）。"""
        recovered_row = _make_queue_row(
            status="running", lease_owner="worker-B", attempts=2
        )
        conn = FlakyAsyncConn(
            fail_at=2,  # set_config(1) 成功；认领语句(2) 断连
            fetchrow_results=[recovered_row],
        )
        queue = PostgresExecutionQueue(pool=RecordingAsyncPool(conn))

        with pytest.raises(ConnectionError):
            await queue.claim("worker-A", lease_ttl_seconds=60)

        # 复位断连标记：模拟 PG 恢复后的新一轮事务
        conn.fail_at = 10**9
        conn.failed = False
        conn.post_failure_calls = 0
        conn._calls = 0
        conn.operations = []
        conn.transactions = []

        job = await queue.claim("worker-B", lease_ttl_seconds=60)

        assert job is not None
        assert job.lease_owner == "worker-B"
        assert job.attempts == 2, "接管重试计数累加（at-least-once 口径）"
        claim_calls = [op for op in conn.operations if op[0] == "fetchrow"]
        assert len(claim_calls) == 1
        assert "FOR UPDATE SKIP LOCKED" in claim_calls[0][1], (
            "恢复后走同一认领协议（SKIP LOCKED 单赢）"
        )
