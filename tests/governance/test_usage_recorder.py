# =============================================================================
# 执行级用量记录器测试（tests/governance/test_usage_recorder.py，roadmap B4）
#
# 覆盖（design §8 测试矩阵）：
#   t5 用量行数=执行次数（三验收之二）：3 次执行 3 行、字段固定口径、
#      租户缺失兜底 log_tenant_default；
#   t6 混合租户分组 flush：缓冲批量刷新按租户分组逐租户 append_batch
#      （修 Postgres append_batch 首条 tenant_id 建连缺陷）。
# =============================================================================

import json

import pytest

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.metering.usage_buffer_queue import (
    UsageBufferQueue,
)
from orchestration_platform.governance.metering.usage_recorder import (
    ExecutionUsageRecorder,
    set_usage_recorder,
)
from orchestration_platform.governance.persistence.local_file.local_file_token_usage_repository import (
    LocalFileTokenUsageRepository,
)


@pytest.fixture(autouse=True)
def _reset_module_state():
    """隔离模块级注入（闸门/记账）与注册表，防跨测试泄漏。"""
    from orchestration_platform.api.routers import execution_instance_router as _router_mod
    from orchestration_platform.governance.tenant import quota_gate as _gate_mod

    set_usage_recorder(None)
    _gate_mod.set_quota_gate(None)
    yield
    set_usage_recorder(None)
    _gate_mod.set_quota_gate(None)
    _router_mod._instance_registry._instances.clear()


def _read_usage_json(storage_root, tenant: str, record_id: str) -> dict:
    file_path = (
        storage_root / "governance" / "token_usage" / tenant / f"{record_id}.json"
    )
    return json.loads(file_path.read_text(encoding="utf-8"))


async def test_t5_one_record_per_execution(tmp_path):
    """t5 用量行数=执行次数：3 次执行启动恰 3 行；字段口径与租户兜底。"""
    fallback_tenant = get_settings().log_tenant_default
    repository = LocalFileTokenUsageRepository(storage_root=str(tmp_path))
    recorder = ExecutionUsageRecorder(repository, enabled=True, buffer_queue=None)

    r1 = await recorder.record_execution_start("tenant-a", "inst-1", "wf-a")
    r2 = await recorder.record_execution_start("tenant-a", "inst-2", "wf-a")
    r3 = await recorder.record_execution_start(None, "inst-3")  # 无租户 → 兜底

    # 行数=执行次数：3 次执行 3 个唯一 record_id
    assert r1 and r2 and r3
    assert len({r1, r2, r3}) == 3

    # 落盘断言：tenant-a 2 行 + 兜底租户 1 行
    record_1 = _read_usage_json(tmp_path, "tenant-a", r1)
    record_2 = _read_usage_json(tmp_path, "tenant-a", r2)
    record_3 = _read_usage_json(tmp_path, fallback_tenant, r3)

    for record, instance_id in (
        (record_1, "inst-1"),
        (record_2, "inst-2"),
        (record_3, "inst-3"),
    ):
        # 执行级固定口径（design §3.2）
        assert record["node_id"] == "execution"
        assert record["model_identifier"] == "workflow_engine"
        assert record["prompt_tokens"] == 0
        assert record["completion_tokens"] == 0
        assert record["total_tokens"] == 0
        assert record["instance_id"] == instance_id
        assert record["record_id"].startswith("usage_")

    assert record_1["workflow_definition_id"] == "wf-a"
    assert record_3["workflow_definition_id"] is None
    assert record_3["tenant_id"] == fallback_tenant

    # enabled=False no-op：不落盘
    disabled = ExecutionUsageRecorder(
        LocalFileTokenUsageRepository(storage_root=str(tmp_path)),
        enabled=False,
        buffer_queue=None,
    )
    result = await disabled.record_execution_start("tenant-a", "inst-x")
    assert result is None


async def test_t6_grouped_flush_by_tenant(tmp_path):
    """t6 混合租户分组 flush：批量刷新逐租户 append_batch，组内租户唯一。"""
    calls: list = []

    class _StubRepository:
        async def append(self, record):
            calls.append(("append", record.tenant_id, record.record_id))
            return record.record_id

        async def append_batch(self, records):
            tenants = tuple(sorted({r.tenant_id for r in records}))
            calls.append(("append_batch", tenants, len(records)))
            return [r.record_id for r in records]

    queue = UsageBufferQueue(
        flush_callback=None,
        persist_path=str(tmp_path / "buffer"),
    )
    recorder = ExecutionUsageRecorder(
        _StubRepository(), enabled=True, buffer_queue=queue
    )
    queue._flush_callback = recorder._flush_to_repository

    await recorder.record_execution_start("tenant-a", "inst-1")
    await recorder.record_execution_start("tenant-b", "inst-2")
    await recorder.record_execution_start("tenant-a", "inst-3")

    assert queue.size == 3
    flushed = await queue.flush()
    assert flushed == 3

    # 分组断言：恰 2 次 append_batch（tenant-a 2 条 + tenant-b 1 条），
    # 组内租户唯一（混合租户批次会错 Postgres RLS 连接上下文）
    batch_calls = [c for c in calls if c[0] == "append_batch"]
    assert len(batch_calls) == 2
    by_tenant = {tenants: count for _, tenants, count in batch_calls}
    assert by_tenant == {("tenant-a",): 2, ("tenant-b",): 1}
    # 无直落 append（队列未满路径）
    assert not [c for c in calls if c[0] == "append"]
