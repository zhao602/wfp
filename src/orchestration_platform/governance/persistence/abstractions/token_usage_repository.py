# TODO(tech-debt): B档候选孤儿（族群 B2），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 用量仓储抽象接口（governance/persistence/abstractions/token_usage_repository.py）
#
# 职责：定义 Token 用量记录的仓储抽象接口。
# 设计意图（对应 spec 5.2、design 2.5、任务 T58.1）：
#   1. 用量记录追加写入（不可变）；
#   2. 按租户/实例/节点/Agent 四维度查询（租户级为 B7 W1a 补缺）；
#   3. 按维度汇总聚合。
# =============================================================================

from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from orchestration_platform.governance.metering.models import TokenUsageRecord


@runtime_checkable
class ITokenUsageRepository(Protocol):
    """Token 用量仓储：用量记录的追加写入与多维查询汇总。"""

    async def append(self, record: TokenUsageRecord) -> str:
        """追加用量记录（不可变写入），返回 record_id。"""
        ...

    async def append_batch(
        self, records: List[TokenUsageRecord]
    ) -> List[str]:
        """批量追加用量记录，返回 record_id 列表。"""
        ...

    async def list_by_instance(
        self, tenant_id: str, instance_id: str
    ) -> List[TokenUsageRecord]:
        """按实例查询用量记录。"""
        ...

    async def list_by_node(
        self, tenant_id: str, instance_id: str, node_id: str
    ) -> List[TokenUsageRecord]:
        """按节点查询用量记录。"""
        ...

    async def list_by_agent(
        self, tenant_id: str, agent_node_id: str
    ) -> List[TokenUsageRecord]:
        """按 Agent 查询用量记录。"""
        ...

    async def list_by_tenant(
        self,
        tenant_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[TokenUsageRecord]:
        """按租户查询用量明细记录（roadmap B7 W1a：usage 端点聚合数据源）。

        时间过滤语义与 aggregate_by_tenant 一致：
        recorded_at >= start_time 且 recorded_at < end_time（缺省不过滤）。
        """
        ...

    async def aggregate_by_instance(
        self, tenant_id: str, instance_id: str
    ) -> Dict[str, Any]:
        """按实例汇总用量。

        返回 {total_prompt_tokens, total_completion_tokens, total_tokens, record_count}。
        """
        ...

    async def aggregate_by_agent(
        self,
        tenant_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """按 Agent 汇总用量。

        返回 {agent_node_id: {total_tokens, record_count, ...}}。
        """
        ...

    async def aggregate_by_tenant(
        self,
        tenant_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """按租户汇总用量。"""
        ...
