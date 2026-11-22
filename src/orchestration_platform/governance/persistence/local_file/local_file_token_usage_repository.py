# TODO(tech-debt): B档候选孤儿（族群 B2），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 本地文件用量仓储（governance/persistence/local_file/local_file_token_usage_repository.py）
#
# 职责：以 JSON 文件持久化 Token 用量记录。
# 设计意图（对应 spec 5.2、design 2.5、任务 T58.2）：
#   1. 按 tenant_id 分目录存储，保证隔离；
#   2. 用于无 PostgreSQL 环境与测试。
# =============================================================================

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.metering.models import TokenUsageRecord


class LocalFileTokenUsageRepository:
    """Token 用量仓储——本地文件实现。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        root = storage_root or get_settings().local_storage_root
        self._base_dir = Path(root) / "governance" / "token_usage"

    def _tenant_dir(self, tenant_id: str) -> Path:
        return self._base_dir / tenant_id

    def _record_file(self, tenant_id: str, record_id: str) -> Path:
        return self._tenant_dir(tenant_id) / f"{record_id}.json"

    async def append(self, record: TokenUsageRecord) -> str:
        """追加用量记录。"""
        file_path = self._record_file(record.tenant_id, record.record_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return record.record_id

    async def append_batch(
        self, records: List[TokenUsageRecord]
    ) -> List[str]:
        """批量追加用量记录。"""
        result: List[str] = []
        for r in records:
            await self.append(r)
            result.append(r.record_id)
        return result

    async def list_by_instance(
        self, tenant_id: str, instance_id: str
    ) -> List[TokenUsageRecord]:
        """按实例查询用量记录。"""
        all_records = self._load_all(tenant_id)
        return [r for r in all_records if r.instance_id == instance_id]

    async def list_by_node(
        self, tenant_id: str, instance_id: str, node_id: str
    ) -> List[TokenUsageRecord]:
        """按节点查询用量记录。"""
        all_records = self._load_all(tenant_id)
        return [
            r for r in all_records
            if r.instance_id == instance_id and r.node_id == node_id
        ]

    async def list_by_agent(
        self, tenant_id: str, agent_node_id: str
    ) -> List[TokenUsageRecord]:
        """按 Agent 查询用量记录。"""
        all_records = self._load_all(tenant_id)
        return [r for r in all_records if r.agent_node_id == agent_node_id]

    async def list_by_tenant(
        self, tenant_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[TokenUsageRecord]:
        """按租户查询用量明细记录（roadmap B7 W1a：usage 端点聚合数据源）。"""
        all_records = self._load_all(tenant_id)
        return [
            r for r in all_records
            if (not start_time or r.recorded_at >= start_time)
            and (not end_time or r.recorded_at < end_time)
        ]

    async def aggregate_by_instance(
        self, tenant_id: str, instance_id: str
    ) -> Dict[str, Any]:
        """按实例汇总用量。"""
        records = await self.list_by_instance(tenant_id, instance_id)
        return self._aggregate(records)

    async def aggregate_by_agent(
        self, tenant_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """按 Agent 汇总用量。"""
        all_records = self._load_all(tenant_id)
        result: Dict[str, Dict[str, Any]] = {}
        for r in all_records:
            if start_time and r.recorded_at < start_time:
                continue
            if end_time and r.recorded_at >= end_time:
                continue
            key = r.agent_node_id or "unknown"
            if key not in result:
                result[key] = {
                    "total_prompt_tokens": 0,
                    "total_completion_tokens": 0,
                    "total_tokens": 0,
                    "record_count": 0,
                }
            result[key]["total_prompt_tokens"] += r.prompt_tokens
            result[key]["total_completion_tokens"] += r.completion_tokens
            result[key]["total_tokens"] += r.total_tokens
            result[key]["record_count"] += 1
        return result

    async def aggregate_by_tenant(
        self, tenant_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """按租户汇总用量。"""
        all_records = self._load_all(tenant_id)
        filtered = [
            r for r in all_records
            if (not start_time or r.recorded_at >= start_time)
            and (not end_time or r.recorded_at < end_time)
        ]
        return self._aggregate(filtered)

    def _load_all(self, tenant_id: str) -> List[TokenUsageRecord]:
        """加载租户全部用量记录。"""
        tenant_dir = self._tenant_dir(tenant_id)
        if not tenant_dir.exists():
            return []
        records: List[TokenUsageRecord] = []
        for file_path in tenant_dir.glob("*.json"):
            records.append(TokenUsageRecord.model_validate_json(
                file_path.read_text(encoding="utf-8")
            ))
        return records

    @staticmethod
    def _aggregate(records: List[TokenUsageRecord]) -> Dict[str, Any]:
        """汇总用量记录。"""
        return {
            "total_prompt_tokens": sum(r.prompt_tokens for r in records),
            "total_completion_tokens": sum(r.completion_tokens for r in records),
            "total_tokens": sum(r.total_tokens for r in records),
            "record_count": len(records),
        }
