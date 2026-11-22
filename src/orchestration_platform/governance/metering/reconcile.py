# =============================================================================
# 用量对账（governance/metering/reconcile.py）
#
# 职责：比对执行级用量记录与运行日志 begin 记录（roadmap B4，DC-B4-05）。
# 设计意图：
#   1. 双源独立持久：用量侧 = 仓储记录 + 未 flush 缓冲（buffer.json）；
#      runlog 侧 = <log_dir>/**/*.jsonl 中 record_type=workflow_run 且
#      status=running 的 begin 记录（每执行恰一条，与记账同 invocation 构造）；
#   2. 执行级口径过滤（B7 W1b）：仅 node_id=="execution" 行入对账——
#      节点级计量行（B7 W1b 新增）不得让执行行丢失差异被掩盖；显式
#      过滤保证未来比较口径演化安全（集合比对当前天然不敏感，仍强制）；
#   3. PG 通道：复用 TenantScopedPostgresRepository._admin_connection 旁路
#      （事务级 app.bypass_rls='on'，B1 运维旁路惯例）跨租户管理读；
#   4. 容错扫描：单行/单文件解析失败跳过，不中断全量扫描；
#   5. 前提声明：metering_enabled=False 时对账必差异（usage 侧空）。
# =============================================================================

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, Optional, Set

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.metering.models import TokenUsageRecord
from orchestration_platform.governance.metering.usage_recorder import EXECUTION_NODE_ID
from orchestration_platform.observability.run_log.fields import (
    RECORD_TYPE_WORKFLOW_RUN,
    STATUS_RUNNING,
)

logger = logging.getLogger(__name__)


class ReconcileEnvironmentError(RuntimeError):
    """对账环境异常（仓储后端不可达/驱动缺失），CLI 映射退出码 2。"""


@dataclass(frozen=True)
class TenantReconcileEntry:
    """单租户对账结果：用量侧/运行日志侧实例标识集合与差异视图。"""

    tenant_id: str
    usage_instance_ids: FrozenSet[str]
    runlog_run_ids: FrozenSet[str]

    @property
    def missing_in_usage(self) -> FrozenSet[str]:
        """runlog 有而用量无（记账丢失口径）。"""
        return self.runlog_run_ids - self.usage_instance_ids

    @property
    def missing_in_runlog(self) -> FrozenSet[str]:
        """用量有而 runlog 无（日志缺失口径）。"""
        return self.usage_instance_ids - self.runlog_run_ids

    @property
    def ok(self) -> bool:
        """两侧集合相等（零差异）。"""
        return self.usage_instance_ids == self.runlog_run_ids


async def collect_usage_by_tenant(
    storage_backend: str,
    storage_root: Optional[str] = None,
) -> Dict[str, FrozenSet[str]]:
    """采集用量侧：仓储记录 + 未 flush 缓冲，返回 租户 → 实例标识集合。

    参数：
        storage_backend —— "local_file" 或 "postgres"
        storage_root    —— 本地存储根目录（local_file 模式；缺省 settings）
    """
    result: Dict[str, Set[str]] = {}

    if storage_backend == "postgres":
        await _collect_usage_postgres(result)
    else:
        _collect_usage_local_file(result, storage_root)

    _collect_usage_buffer(result)
    return {tenant: frozenset(ids) for tenant, ids in result.items()}


def _collect_usage_local_file(
    result: Dict[str, Set[str]], storage_root: Optional[str]
) -> None:
    """本地文件模式：扫 <root>/governance/token_usage/<tenant>/*.json。"""
    root = storage_root or get_settings().local_storage_root
    base_dir = Path(root) / "governance" / "token_usage"
    if not base_dir.exists():
        return
    for tenant_dir in base_dir.iterdir():
        if not tenant_dir.is_dir():
            continue
        for file_path in tenant_dir.glob("*.json"):
            try:
                record = TokenUsageRecord.model_validate_json(
                    file_path.read_text(encoding="utf-8")
                )
            except Exception:
                logger.warning("用量记录解析失败跳过: %s", file_path)
                continue
            if record.node_id != EXECUTION_NODE_ID:
                continue  # 执行级口径过滤（B7 W1b：节点级行不入对账）
            result.setdefault(record.tenant_id, set()).add(record.instance_id)


def _collect_usage_buffer(result: Dict[str, Set[str]]) -> None:
    """合并未 flush 缓冲（<persist_path>/buffer.json，含恢复语义）。"""
    buffer_file = Path(get_settings().metering_buffer_persist_path) / "buffer.json"
    if not buffer_file.exists():
        return
    try:
        data = json.loads(buffer_file.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("用量缓冲持久化文件解析失败跳过: %s", buffer_file)
        return
    for item in data:
        try:
            record = TokenUsageRecord.model_validate(item)
        except Exception:
            continue
        if record.node_id != EXECUTION_NODE_ID:
            continue  # 执行级口径过滤（B7 W1b：节点级行不入对账）
        result.setdefault(record.tenant_id, set()).add(record.instance_id)


async def _collect_usage_postgres(result: Dict[str, Set[str]]) -> None:
    """Postgres 模式：_admin_connection 旁路（app.bypass_rls）跨租户管理读。"""
    from orchestration_platform.governance.persistence.postgres.postgres_token_usage_repository import (
        PostgresTokenUsageRepository,
    )

    repository = PostgresTokenUsageRepository()
    try:
        async with repository._admin_connection() as conn:
            rows = await conn.fetch(
                "SELECT tenant_id, instance_id FROM governance_token_usage "
                "WHERE node_id = $1",
                EXECUTION_NODE_ID,
            )
    except Exception as exc:
        raise ReconcileEnvironmentError(
            f"用量仓储连接失败（postgres）: {exc}"
        ) from exc
    for row in rows:
        result.setdefault(row["tenant_id"], set()).add(row["instance_id"])


def collect_runlog_begins_by_tenant(log_dir: str) -> Dict[str, FrozenSet[str]]:
    """采集 runlog 侧：扫描 JSONL 中 workflow_run/running 的 begin 记录。

    每执行恰一条 begin（run_id=instance_id）；tenant_id 缺失或单行解析
    失败跳过（容错全量扫描）。
    """
    result: Dict[str, Set[str]] = {}
    root = Path(log_dir)
    if not root.exists():
        return {}
    for file_path in root.rglob("*.jsonl"):
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            logger.warning("运行日志文件读取失败跳过: %s", file_path)
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            if (
                record.get("record_type") != RECORD_TYPE_WORKFLOW_RUN
                or record.get("status") != STATUS_RUNNING
            ):
                continue
            tenant_id = record.get("tenant_id")
            run_id = record.get("run_id")
            if not tenant_id or not run_id:
                continue
            result.setdefault(tenant_id, set()).add(run_id)
    return {tenant: frozenset(ids) for tenant, ids in result.items()}


def reconcile(
    usage: Dict[str, FrozenSet[str]],
    runlog: Dict[str, FrozenSet[str]],
) -> Dict[str, TenantReconcileEntry]:
    """全租户并集对账：返回 租户 → 对账条目。"""
    entries: Dict[str, TenantReconcileEntry] = {}
    for tenant_id in set(usage) | set(runlog):
        entries[tenant_id] = TenantReconcileEntry(
            tenant_id=tenant_id,
            usage_instance_ids=usage.get(tenant_id, frozenset()),
            runlog_run_ids=runlog.get(tenant_id, frozenset()),
        )
    return entries
