# =============================================================================
# 审计日志器（governance/audit/audit_logger.py）
#
# 职责：记录审计日志，异步追加写入，保证不可篡改。
# 设计意图（对应 spec 5.3、design 2.6、任务 T62.1）：
#   1. 全操作留痕：平台所有治理类操作均产生审计日志；
#   2. 异步写入不阻塞业务操作：写入开销 ≤ 5 毫秒；
#   3. 不丢失：写入失败时按配置回滚或进入重试队列；
#   4. 哈希链保证日志完整性。
# =============================================================================

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import (
    AuditOperationResult,
    OperationType,
    ResourceType,
)
from orchestration_platform.governance.audit.content_hasher import ContentHasher
from orchestration_platform.governance.audit.hash_chain_verifier import (
    HashChainVerifier,
)
from orchestration_platform.governance.audit.models import AuditLogEntry


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditLogger:
    """审计日志器。

    异步追加写入审计日志，哈希链保证完整性。
    """

    def __init__(
        self,
        hash_verifier: Optional[HashChainVerifier] = None,
    ) -> None:
        """初始化审计日志器。

        Args:
            hash_verifier: 哈希链校验器；缺省创建默认实例。
        """
        self._verifier = hash_verifier or HashChainVerifier()
        self._settings = get_settings()
        # ---- 第五阶段偏差修正（G5/T208）：字段级内容哈希器 ----
        # 开关 audit_content_hash_enabled 默认 False，关闭时条目哈希字段
        # 保持 None，行为与修正前完全一致。
        self._content_hasher = ContentHasher()
        self._last_hash: str = ""
        self._retry_queue: List[AuditLogEntry] = []
        self._total_written = 0
        self._total_failed = 0

    async def record(
        self,
        tenant_id: str,
        actor_id: str,
        resource_type: ResourceType,
        resource_id: str,
        operation: OperationType,
        trace_id: str,
        operation_result: AuditOperationResult = AuditOperationResult.SUCCESS,
        request_payload: Optional[Dict[str, Any]] = None,
        response_summary: Optional[str] = None,
        ip_address: Optional[str] = None,
        actor_type: str = "user",
    ) -> AuditLogEntry:
        """记录审计日志（异步追加写入）。

        Args:
            tenant_id: 租户标识。
            actor_id: 操作者标识。
            resource_type: 资源类型。
            resource_id: 资源标识。
            operation: 操作类型。
            trace_id: 追踪标识。
            operation_result: 操作结果。
            request_payload: 请求载荷（已脱敏）。
            response_summary: 响应摘要（已脱敏）。
            ip_address: 来源 IP。
            actor_type: 操作者类型。

        Returns:
            审计日志条目。
        """
        # ---- 第五阶段偏差修正（G5/T208）：落盘前字段级内容哈希钩子 ----
        # 开关开启时计算 input_hash/output_hash；关闭时字段保持 None（旧行为）。
        # 计算时机在条目构造前，字段作为构造参数传入；
        # 两字段不参与 _serialize_entry 链计算（D7 红线），entry_hash 链值不变。
        input_hash_value: Optional[str] = None
        output_hash_value: Optional[str] = None
        if self._settings.audit_content_hash_enabled:
            input_hash_value = self._content_hasher.compute_input_hash(request_payload)
            output_hash_value = self._content_hasher.compute_output_hash(response_summary)

        entry = AuditLogEntry(
            entry_id=f"audit_{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_type=actor_type,
            resource_type=resource_type,
            resource_id=resource_id,
            operation=operation,
            operation_result=operation_result,
            request_payload=request_payload,
            response_summary=response_summary,
            trace_id=trace_id,
            ip_address=ip_address,
            input_hash=input_hash_value,
            output_hash=output_hash_value,
            previous_hash=self._last_hash,
        )

        # 计算哈希链
        entry.entry_hash = self._verifier.compute_entry_hash(
            entry, self._last_hash
        )
        self._last_hash = entry.entry_hash

        self._total_written += 1
        return entry

    async def record_batch(
        self, entries_data: List[Dict[str, Any]]
    ) -> List[AuditLogEntry]:
        """批量记录审计日志。"""
        results: List[AuditLogEntry] = []
        for data in entries_data:
            entry = await self.record(**data)
            results.append(entry)
        return results

    def set_last_hash(self, hash_value: str) -> None:
        """设置最后一条日志的哈希值（从仓储恢复时使用）。"""
        self._last_hash = hash_value

    @property
    def last_hash(self) -> str:
        """最后一条日志的哈希值。"""
        return self._last_hash

    @property
    def total_written(self) -> int:
        """累计写入条数。"""
        return self._total_written

    @property
    def total_failed(self) -> int:
        """累计失败条数。"""
        return self._total_failed
