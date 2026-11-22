# =============================================================================
# 治理审计查询单测（tests/unit/test_governance_query_audit.py）
#
# 覆盖：arch_review_v2_tasks_wave2 任务 2.12 测试三件套之三（CHG-58；
# AC-02 审计链证据、CHG-64/CHG-65、W-2 波及=0）。
# 分层：
#   A. 服务层——query_audit_logs 真实条目/空仓储/脱敏/篡改检测/条目级
#      chain_valid + query_audit_entries 原始条目（CHG-64）；
#   B. 路由层——GET /audit-logs 直返服务 Dict + export 真条目导出
#      （CHG-65；行为变更预告 design §6.4：恒空→真实条目）。
# =============================================================================

import json
from typing import Any, Dict, Optional

import pytest

from orchestration_platform.api.routers.governance import governance_router as router_mod
from orchestration_platform.api.routers.governance.governance_router import (
    AuditLogQueryRequest,
)
from orchestration_platform.domain.models.enums import (
    AuditOperationResult,
    MaskMethod,
    OperationType,
    ResourceType,
)
from orchestration_platform.governance.application.governance_services import (
    GovernanceServices,
)
from orchestration_platform.governance.audit.models import (
    AuditLogEntry,
    MaskRule,
)
from orchestration_platform.governance.persistence.local_file.local_file_audit_log_repository import (
    LocalFileAuditLogRepository,
)


# =============================================================================
# 测试基建
# =============================================================================

def _build_services(tmp_path) -> tuple:
    """构造指向临时目录的治理服务聚合（审计仓储显式注入）。"""
    repo = LocalFileAuditLogRepository(storage_root=str(tmp_path))
    services = GovernanceServices(audit_repository=repo)
    return services, repo


async def _write_entry(
    services: GovernanceServices,
    repo: LocalFileAuditLogRepository,
    *,
    tenant_id: str = "tenant-1",
    resource_id: str = "inst-1",
    operation: OperationType = OperationType.EXECUTE,
    operation_result: AuditOperationResult = AuditOperationResult.SUCCESS,
    request_payload: Optional[Dict[str, Any]] = None,
) -> AuditLogEntry:
    """经服务审计日志器记账并落盘（同一 logger 保证哈希链连续）。"""
    entry = await services.audit_logger.record(
        tenant_id=tenant_id,
        actor_id="auditor",
        resource_type=ResourceType.EXECUTION_INSTANCE,
        resource_id=resource_id,
        operation=operation,
        trace_id="trace-1",
        operation_result=operation_result,
        request_payload=(
            request_payload if request_payload is not None else {"action": "run"}
        ),
        actor_type="user",
    )
    await repo.append(entry)
    return entry


# =============================================================================
# A. 服务层：query_audit_logs / query_audit_entries（CHG-64）
# =============================================================================

class TestQueryAuditLogs:
    """真实条目 + 哈希链校验 + 脱敏 + 篡改如实标记（不隐瞒）。"""

    async def test_returns_real_entries_with_chain_check(self, tmp_path):
        services, repo = _build_services(tmp_path)
        entry1 = await _write_entry(services, repo)
        entry2 = await _write_entry(services, repo, resource_id="inst-2")

        result = await services.query_audit_logs(tenant_id="tenant-1")

        assert [e["entry_id"] for e in result["entries"]] == [
            entry1.entry_id,
            entry2.entry_id,
        ], "恒空占位改为真实条目（design §6.4 行为变更预告）"
        assert result["chain_valid"] is True
        assert result["chain_check"]["is_valid"] is True
        assert result["chain_check"]["verified_count"] == 2
        assert result["chain_check"]["failed_count"] == 0
        assert all(e["chain_valid"] for e in result["entries"])
        first = result["entries"][0]
        assert first["operation"] == "execute"
        assert first["resource_type"] == "execution_instance"
        assert first["actor_id"] == "auditor"
        assert first["request_payload"] == {"action": "run"}

    async def test_empty_repository_returns_empty_structure(self, tmp_path):
        services, _ = _build_services(tmp_path)

        result = await services.query_audit_logs(tenant_id="tenant-1")

        assert result["entries"] == []
        assert result["chain_valid"] is True, "空链 is_valid=True（空结构语义保留）"
        assert result["chain_check"]["verified_count"] == 0
        assert result["chain_check"]["failed_count"] == 0
        assert result["chain_check"]["is_valid"] is True

    async def test_masks_sensitive_fields(self, tmp_path):
        services, repo = _build_services(tmp_path)
        services.mask_engine.add_rule(
            MaskRule(
                rule_id="r-secret",
                field_path="request_payload.secret",
                mask_method=MaskMethod.FULL_MASK,
            )
        )
        await _write_entry(
            services,
            repo,
            request_payload={"secret": "hunter2", "action": "run"},
        )

        result = await services.query_audit_logs(tenant_id="tenant-1")

        payload = result["entries"][0]["request_payload"]
        assert payload["secret"] == "****"
        assert payload["action"] == "run"

    async def test_detects_tampering_and_marks_broken_entries(self, tmp_path):
        services, repo = _build_services(tmp_path)
        entry1 = await _write_entry(services, repo)
        entry2 = await _write_entry(services, repo, resource_id="inst-2")

        log_file = (
            tmp_path / "governance" / "audit_logs" / "tenant-1" / "audit_log.jsonl"
        )
        rebuilt = []
        for line in log_file.read_text(encoding="utf-8").strip().splitlines():
            data = json.loads(line)
            if data["entry_id"] == entry2.entry_id:
                data["entry_hash"] = data["entry_hash"] + "broken"
                line = json.dumps(data, ensure_ascii=False)
            rebuilt.append(line)
        log_file.write_text("\n".join(rebuilt) + "\n", encoding="utf-8")

        result = await services.query_audit_logs(tenant_id="tenant-1")

        assert result["chain_valid"] is False, "篡改如实上报，不隐瞒"
        assert result["chain_check"]["failed_count"] == 1
        assert result["chain_check"]["failed_entry_ids"] == [entry2.entry_id]
        by_id = {e["entry_id"]: e for e in result["entries"]}
        assert by_id[entry1.entry_id]["chain_valid"] is True
        assert by_id[entry2.entry_id]["chain_valid"] is False, "断链条目级标记"


class TestQueryAuditEntries:
    """原始条目查询（未脱敏，供合规导出使用）。"""

    async def test_returns_raw_models(self, tmp_path):
        services, repo = _build_services(tmp_path)
        entry = await _write_entry(services, repo)

        entries = await services.query_audit_entries(tenant_id="tenant-1")

        assert len(entries) == 1
        assert isinstance(entries[0], AuditLogEntry)
        assert entries[0].entry_id == entry.entry_id
        assert entries[0].request_payload == {"action": "run"}


# =============================================================================
# B. 路由层：GET /audit-logs 直返 + export 真条目（CHG-65）
# =============================================================================

class TestGovernanceAuditRoutes:
    """路由契约：GET 直返服务 Dict；export 基于真实条目导出。"""

    async def test_get_audit_logs_returns_service_dict(self, tmp_path, monkeypatch):
        services, repo = _build_services(tmp_path)
        await _write_entry(services, repo)
        monkeypatch.setattr(router_mod, "get_governance_services", lambda: services)

        result = await router_mod.query_audit_logs(
            tenant_id="tenant-1",
            start_time=None,
            end_time=None,
            limit=1000,
        )

        assert set(result.keys()) == {"entries", "chain_check", "chain_valid"}
        assert len(result["entries"]) == 1
        assert result["chain_valid"] is True
        assert result["entries"][0]["chain_valid"] is True

    async def test_export_audit_logs_exports_real_entries(self, tmp_path, monkeypatch):
        services, repo = _build_services(tmp_path)
        entry1 = await _write_entry(services, repo)
        entry2 = await _write_entry(services, repo, resource_id="inst-2")
        monkeypatch.setattr(router_mod, "get_governance_services", lambda: services)

        request = AuditLogQueryRequest(tenant_id="tenant-1", limit=100)
        result = await router_mod.export_audit_logs(request)

        assert result["record_count"] == 2, "导出计数基于真实条目"
        exported = json.loads(result["exported_data"])
        assert isinstance(exported, list)
        assert {e["entry_id"] for e in exported} == {
            entry1.entry_id,
            entry2.entry_id,
        }
        assert exported[0]["operation"] == "execute"
        assert exported[0]["resource_type"] == "execution_instance"
