# =============================================================================
# M3 PG 执行队列认领/接管原子性静态断言
# （tests/unit/test_pg_takeover_atomicity.py）
#
# 职责：锁 PostgresExecutionQueue 认领与接管的单语句原子形态
#       （roadmap-v3 M3，design D7——M2 出口记录项 7 描述过时：take_over
#       实测已是单语句原子，本测试固化该形态防回退）。
# 断言口径：
#   1. claim / take_over_expired 各恰一条 SQL 语句（UPDATE 内嵌
#      FOR UPDATE SKIP LOCKED 子查询，无 SELECT-后-UPDATE TOCTOU 窗口）；
#   2. 租约赋值 + attempts 递增与状态流转同语句落定；
#   3. heartbeat/complete/fail 受 lease_owner + status 双门（防误接管）。
# 实库并发单赢由 compose 环境 drill 承担（本测试环境无活 PG）。
# =============================================================================

import re
from pathlib import Path

_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "orchestration_platform"
    / "persistence"
    / "postgres"
    / "postgres_execution_queue.py"
).read_text(encoding="utf-8")


def _method_body(name: str) -> str:
    """提取异步方法源码块（含 docstring，至下一方法/类定义）。"""
    pattern = re.compile(
        rf"async def {name}\(.*?(?=\n    async def |\n\nclass |\Z)",
        re.S,
    )
    match = pattern.search(_SOURCE)
    assert match is not None, f"方法 {name} 必须存在"
    return match.group(0)


class TestClaimAtomicity:

    def test_single_statement_no_toctou(self):
        """claim 恰一条查询语句：UPDATE 内嵌 SKIP LOCKED 子查询。"""
        body = _method_body("claim")
        assert body.count("fetchrow") == 1, "单语句（无 SELECT-后-UPDATE 窗口）"
        assert "await conn.execute" not in body
        assert "UPDATE execution_job" in body
        assert "FOR UPDATE SKIP LOCKED" in body
        assert "SET status = 'running'" in body

    def test_lease_and_attempts_same_statement(self):
        """租约赋值 + attempts 递增与认领同语句落定。"""
        body = _method_body("claim")
        assert "lease_owner = $1" in body
        assert "lease_expires_at = NOW()" in body
        assert "attempts = attempts + 1" in body


class TestTakeOverAtomicity:

    def test_single_statement_no_toctou(self):
        """take_over_expired 恰一条查询语句：无并发双接管窗口。"""
        body = _method_body("take_over_expired")
        assert body.count("fetchrow") == 1, "单语句（无 TOCTOU 窗口）"
        assert "await conn.execute" not in body
        assert "UPDATE execution_job" in body
        assert "FOR UPDATE SKIP LOCKED" in body

    def test_expired_filter_and_lease_same_statement(self):
        """过期过滤 + 租约改主 + attempts 递增同语句落定。"""
        body = _method_body("take_over_expired")
        assert "lease_expires_at < NOW()" in body
        assert "lease_owner = $1" in body
        assert "attempts = attempts + 1" in body
        assert "RETURNING *" in body


class TestOwnershipGuards:

    def test_heartbeat_owner_and_status_gated(self):
        body = _method_body("heartbeat")
        assert "lease_owner = $2" in body
        assert "status = 'running'" in body

    def test_complete_owner_and_status_gated(self):
        body = _method_body("complete")
        assert "lease_owner = $2" in body
        assert "status = 'running'" in body

    def test_fail_owner_and_status_gated(self):
        body = _method_body("fail")
        assert "lease_owner = $2" in body
        assert "status = 'running'" in body
