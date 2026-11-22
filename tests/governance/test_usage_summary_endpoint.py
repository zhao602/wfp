# =============================================================================
# 治理用量查询端点测试（tests/governance/test_usage_summary_endpoint.py）
#
# 职责：验证 B7 W1a——治理 usage 查询真实聚合与降级形状。
# 设计意图（对应 .codeartsdoer/specs/roadmap_b7_harvest spec W1a / design §1）：
#   1. 服务层聚合：executions（执行级口径 node_id=="execution"）/
#      node_records / by_model 分桶 / 总量键；
#   2. instance_id 维度走 list_by_instance，租户维度走 list_by_tenant；
#   3. 仓储未注入 → {"available": False} 降级（路由形状稳定不抛错）；
#   4. 路由层直返服务 Dict（monkeypatch get_governance_services）；
#   5. 空数据零值形状稳定；
#   6. 本地文件后端 list_by_tenant 明细 round-trip（含时间过滤语义）。
# =============================================================================

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from orchestration_platform.api.routers.governance import governance_router as router_mod
from orchestration_platform.governance.application.governance_services import (
    GovernanceServices,
    set_token_usage_repository,
)
from orchestration_platform.governance.metering.models import TokenUsageRecord
from orchestration_platform.governance.persistence.local_file.local_file_token_usage_repository import (
    LocalFileTokenUsageRepository,
)


def _record(
    record_id: str,
    *,
    tenant_id: str = "tenant-1",
    instance_id: str = "inst-1",
    node_id: str = "execution",
    model_identifier: str = "workflow_engine",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    recorded_at: Optional[datetime] = None,
) -> TokenUsageRecord:
    """构造测试用量记录（recorded_at 缺省取当前 UTC 时间）。"""
    return TokenUsageRecord(
        record_id=record_id,
        tenant_id=tenant_id,
        instance_id=instance_id,
        node_id=node_id,
        model_identifier=model_identifier,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        recorded_at=recorded_at or datetime.now(timezone.utc),
    )


class _FakeUsageRepository:
    """内存假仓储：记录查询方法命中路径，供聚合/路由测试。"""

    def __init__(self, records: List[TokenUsageRecord]) -> None:
        self._records = records
        self.calls: List[str] = []

    async def list_by_instance(
        self, tenant_id: str, instance_id: str
    ) -> List[TokenUsageRecord]:
        self.calls.append(f"list_by_instance:{instance_id}")
        return [
            r
            for r in self._records
            if r.tenant_id == tenant_id and r.instance_id == instance_id
        ]

    async def list_by_tenant(
        self,
        tenant_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[TokenUsageRecord]:
        self.calls.append("list_by_tenant")
        return [r for r in self._records if r.tenant_id == tenant_id]


@pytest.fixture(autouse=True)
def _isolate_usage_repository():
    """模块级仓储注入状态隔离：每个用例前后复位为 None（不泄漏他处）。"""
    set_token_usage_repository(None)
    yield
    set_token_usage_repository(None)


class TestUsageSummaryAggregation:
    """服务层聚合：executions/node_records/by_model/总量键。"""

    async def test_tenant_summary_aggregates_execution_and_node_records(self):
        records = [
            _record("r1", node_id="execution", model_identifier="workflow_engine", total_tokens=30),
            _record("r2", instance_id="inst-2", node_id="execution", model_identifier="workflow_engine", total_tokens=20),
            _record("r3", node_id="llm_1", model_identifier="gpt-4", prompt_tokens=100, completion_tokens=50, total_tokens=150),
            _record("r4", node_id="agent_1", model_identifier="gpt-4", prompt_tokens=10, completion_tokens=5, total_tokens=15),
        ]
        repo = _FakeUsageRepository(records)
        set_token_usage_repository(repo)
        services = GovernanceServices()

        result = await services.get_usage_summary("tenant-1")

        assert repo.calls == ["list_by_tenant"], "租户维度走 list_by_tenant"
        assert result["available"] is True
        assert result["record_count"] == 4
        assert result["executions"] == 2, "执行级口径 node_id=='execution'"
        assert result["node_records"] == 2
        assert result["total_prompt_tokens"] == 110
        assert result["total_completion_tokens"] == 55
        assert result["total_tokens"] == 215
        assert result["by_model"]["workflow_engine"] == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 50,
            "record_count": 2,
        }
        assert result["by_model"]["gpt-4"]["record_count"] == 2
        assert result["by_model"]["gpt-4"]["total_tokens"] == 165

    async def test_instance_summary_uses_list_by_instance(self):
        records = [
            _record("r1", node_id="execution", total_tokens=30),
            _record("r2", node_id="llm_1", model_identifier="gpt-4", total_tokens=15),
            _record("r3", instance_id="inst-2", node_id="execution", total_tokens=99),
        ]
        repo = _FakeUsageRepository(records)
        set_token_usage_repository(repo)
        services = GovernanceServices()

        result = await services.get_usage_summary("tenant-1", "inst-1")

        assert repo.calls == ["list_by_instance:inst-1"], "实例维度走 list_by_instance"
        assert result["instance_id"] == "inst-1"
        assert result["record_count"] == 2, "仅统计该实例记录"
        assert result["executions"] == 1
        assert result["node_records"] == 1
        assert result["total_tokens"] == 45

    async def test_empty_tenant_returns_zero_shape(self):
        set_token_usage_repository(_FakeUsageRepository([]))
        services = GovernanceServices()

        result = await services.get_usage_summary("tenant-empty")

        assert result["available"] is True
        assert result["record_count"] == 0
        assert result["executions"] == 0
        assert result["node_records"] == 0
        assert result["total_prompt_tokens"] == 0
        assert result["total_completion_tokens"] == 0
        assert result["total_tokens"] == 0
        assert result["by_model"] == {}

    async def test_missing_repository_degrades_available_false(self):
        services = GovernanceServices()

        result = await services.get_usage_summary("tenant-1", "inst-1")

        assert result == {
            "tenant_id": "tenant-1",
            "instance_id": "inst-1",
            "available": False,
        }, "未注入仓储降级，形状稳定不抛错"


class TestUsageRouteContract:
    """路由契约：GET /governance/usage 直返服务聚合 Dict。"""

    async def test_route_returns_service_dict(self, monkeypatch):
        records = [
            _record("r1", node_id="execution", total_tokens=30),
            _record("r2", node_id="llm_1", model_identifier="gpt-4", prompt_tokens=100, completion_tokens=50, total_tokens=150),
        ]
        set_token_usage_repository(_FakeUsageRepository(records))
        services = GovernanceServices()
        monkeypatch.setattr(router_mod, "get_governance_services", lambda: services)

        result = await router_mod.get_usage(tenant_id="tenant-1", instance_id=None)

        assert result["available"] is True
        assert set(result.keys()) >= {
            "tenant_id",
            "instance_id",
            "available",
            "record_count",
            "executions",
            "node_records",
            "by_model",
        }
        assert result["executions"] == 1
        assert result["node_records"] == 1


class TestLocalFileListByTenant:
    """本地文件后端 list_by_tenant 明细 round-trip（B7 W1a 查询缺口）。"""

    async def test_local_file_list_by_tenant_roundtrip_with_time_filter(self, tmp_path):
        repo = LocalFileTokenUsageRepository(storage_root=str(tmp_path))
        now = datetime.now(timezone.utc)
        old = _record("r-old", recorded_at=now - timedelta(hours=2), total_tokens=10)
        recent = _record("r-recent", recorded_at=now - timedelta(minutes=5), node_id="llm_1", total_tokens=20)
        await repo.append(old)
        await repo.append(recent)

        all_records = await repo.list_by_tenant("tenant-1")
        assert {r.record_id for r in all_records} == {"r-old", "r-recent"}

        filtered = await repo.list_by_tenant(
            "tenant-1", start_time=now - timedelta(hours=1)
        )
        assert [r.record_id for r in filtered] == ["r-recent"], "start_time 下界含入"

        end_bound = await repo.list_by_tenant(
            "tenant-1", end_time=now - timedelta(hours=1)
        )
        assert [r.record_id for r in end_bound] == ["r-old"], "end_time 上界排他"
